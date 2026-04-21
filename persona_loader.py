"""Load personas and MCP server configs from mcp.json.

Personas can come from two places, resolved in this order:

1. **Inline** — defined directly under ``personas`` in ``mcp.json``.
2. **External** — discovered from directories listed in ``personasPaths``
   in ``mcp.json``.  Each directory (or subdirectory) containing a
   ``persona.json`` manifest is treated as a standalone persona.

External personas support a ``tools.file`` field (path to a ``.py`` file
relative to the persona directory) in addition to the standard
``tools.module`` dotted import path.

Usage::

    from persona_loader import load_persona, list_personas

    persona = load_persona("devops_engineer")
    # persona.instructions   — str (markdown content)
    # persona.extension_tools — callables from the persona manifest only
    # persona.tools          — merged base + extension (see inheritBaseTools)
    # persona.mcp_servers    — dict[str, dict] | None
"""
from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

from capture_policy import (
    DEFAULT_CAPTURE_POLICY,
    CapturePolicy,
    capture_policy_from_dict,
)
from core_agent_tools import get_base_tools, merge_base_and_extension_tools

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_MCP_JSON = os.path.join(_PROJECT_ROOT, "mcp.json")


# ──────────────────────────────────────────────────────────────────────
# Data model
# ──────────────────────────────────────────────────────────────────────

@dataclass
class Persona:
    """Fully resolved persona ready for the agent."""

    name: str
    description: str
    instructions: str
    heartbeat_task: str = ""
    # Tools from the persona manifest only (before merging with core base tools).
    extension_tools: list[Callable[..., Any]] = field(default_factory=list)
    # When True (default), :attr:`tools` is base + extension with extension winning name clashes.
    inherit_base_tools: bool = True
    tools: list[Callable[..., Any]] = field(default_factory=list)
    mcp_servers: dict[str, dict] | None = None
    workflow: str = "second_brain"
    steps: list[dict] | None = None
    stepwise: bool = False
    roster: list[str] = field(default_factory=list)
    schedule: str = "round_robin"  # round_robin | all

    # Tiered memory: consolidation & lint intervals
    consolidation_interval: int = 5    # every N heartbeats (0 = threshold only)
    consolidation_threshold: int = 10  # max unconsolidated notes before forced
    lint_wiki_interval: int = 20       # every N heartbeats (0 = disabled)
    dream_enabled: bool = True
    dream_idle_streak_min: int = 3
    dream_max_age_days: int = 30

    # Agentic task mode (used by cmd_code / cmd_task)
    prompt_dir: str = ""               # prompt template subdirectory (e.g. "coding_agent")
    done_marker: str = "[TASK_COMPLETE]"
    blocked_marker: str = "[TASK_BLOCKED]"
    max_turns: int = 20                # default agentic-turn limit

    # Governance schemas (loaded from BRAIN.md / HEARTBEAT.md / DECISIONS.md)
    brain_schema: str = ""             # knowledge organization rules
    heartbeat_schema: str = ""         # cycle execution strategy
    decisions_schema: str = ""         # decision-making philosophy

    # Decision engine policy (loaded from "decisions" manifest key)
    decision_policy: Any = None        # DecisionPolicy or None

    capture_policy: CapturePolicy = field(default_factory=lambda: DEFAULT_CAPTURE_POLICY)


# ──────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────


def _resolve_capture_policy(entry: dict) -> CapturePolicy:
    """Load capture rules from ``capturePolicy`` (preferred) or legacy ``capture``."""
    modern = entry.get("capturePolicy")
    if isinstance(modern, dict) and modern:
        return capture_policy_from_dict(modern)
    leg = entry.get("capture")
    if isinstance(leg, dict) and leg:
        strict = bool(leg.get("strictJson", leg.get("strict_json", False)))
        reco = bool(
            leg.get("reconcileEvidenceContradictions", leg.get("reconcile_evidence_contradictions", False))
        )
        return capture_policy_from_dict(
            {
                "reject_if_no_json": strict,
                "reject_if_missing_evidence": bool(
                    leg.get("requireEvidence", leg.get("require_evidence", False))
                ),
                "reject_on_parse_failure": strict,
                "evidence_burst_merge_window_minutes": max(
                    0, int(leg.get("burstMergeWindowMinutes", leg.get("burst_merge_window_minutes", 0)))
                ),
                "after_capture": (
                    "personas.workspace_observer.capture:after_note" if reco else None
                ),
            }
        )
    return DEFAULT_CAPTURE_POLICY


def _resolve_decision_policy(raw: dict | None) -> Any:
    """Merge persona decision config with defaults.

    Returns a :class:`DecisionPolicy` instance.  When *raw* is None or
    empty, returns ``DEFAULT_DECISION_POLICY``.  Lazily imports to avoid
    circular deps.
    """
    from decision_engine import DecisionPolicy, DEFAULT_DECISION_POLICY, ActionType, EventRoute

    if not raw:
        return DEFAULT_DECISION_POLICY

    defaults = DEFAULT_DECISION_POLICY

    # Merge task priority scores
    task_scores = {**defaults.task_priority_scores, **raw.get("task_priority_scores", {})}

    # Resolve enabled_initiatives from string names to ActionType
    raw_initiatives = raw.get("enabled_initiatives")
    if raw_initiatives is not None:
        name_map = {a.value: a for a in ActionType if a.value.startswith("initiative_")}
        enabled = frozenset(
            name_map[name] for name in raw_initiatives if name in name_map
        )
    else:
        enabled = defaults.enabled_initiatives

    # Resolve event routing overrides
    raw_routing = raw.get("event_routing", {})
    event_routing: dict[str, EventRoute] = {}
    for event_type, cfg in raw_routing.items():
        action_str = cfg.get("action", "run_heartbeat")
        try:
            action = ActionType(action_str)
        except ValueError:
            action = ActionType.RUN_HEARTBEAT
        event_routing[event_type] = EventRoute(
            action=action,
            preempt=bool(cfg.get("preempt", False)),
            boost=float(cfg.get("boost", 0.0)),
        )

    # Resolve action biases from string keys
    raw_biases = raw.get("action_biases", {})
    action_biases: dict[ActionType, float] = {}
    for key, val in raw_biases.items():
        try:
            action_biases[ActionType(key)] = float(val)
        except (ValueError, TypeError):
            pass

    return DecisionPolicy(
        task_priority_scores=task_scores,
        consolidation_threshold=int(raw.get("consolidation_threshold", defaults.consolidation_threshold)),
        consolidation_heartbeat_interval=int(raw.get("consolidation_heartbeat_interval", defaults.consolidation_heartbeat_interval)),
        connection_density_target=float(raw.get("connection_density_target", defaults.connection_density_target)),
        lint_heartbeat_interval=int(raw.get("lint_heartbeat_interval", defaults.lint_heartbeat_interval)),
        lint_issue_boost=float(raw.get("lint_issue_boost", defaults.lint_issue_boost)),
        confidence_threshold=float(raw.get("confidence_threshold", defaults.confidence_threshold)),
        ambiguity_margin=float(raw.get("ambiguity_margin", defaults.ambiguity_margin)),
        enabled_initiatives=enabled,
        initiative_cooldown=int(raw.get("initiative_cooldown", defaults.initiative_cooldown)),
        initiative_ceiling=float(raw.get("initiative_ceiling", defaults.initiative_ceiling)),
        event_routing=event_routing,
        action_biases=action_biases,
        positive_outcome_boost=float(raw.get("positive_outcome_boost", defaults.positive_outcome_boost)),
        negative_outcome_penalty=float(raw.get("negative_outcome_penalty", defaults.negative_outcome_penalty)),
    )


def _read_json(path: str = _MCP_JSON) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_instructions(rel_or_abs_path: str, base_dir: str = _PROJECT_ROOT) -> str:
    """Read a markdown file. Absolute paths are used as-is; relative paths
    are resolved against ``base_dir`` (defaults to project root)."""
    full = (
        rel_or_abs_path
        if os.path.isabs(rel_or_abs_path)
        else os.path.normpath(os.path.join(base_dir, rel_or_abs_path))
    )
    if not os.path.isfile(full):
        logger.warning("Instructions file not found: %s", full)
        return ""
    with open(full, "r", encoding="utf-8") as f:
        return f.read().strip()


_DEFAULT_BRAIN_SCHEMA = """\
# Knowledge Schema: Default

## Domain
General AI agents, knowledge systems, and knowledge management.
Capture insights about architecture patterns, tool ecosystems,
best practices, and emerging research.

## Categories
- **projects**: specific build tasks with deliverables
- **areas**: ongoing concerns (e.g., "agent reliability")
- **resources**: reference knowledge (e.g., "comparison of memory frameworks")
- **archive**: superseded or completed items

## Tags
Use lowercase, hyphenated tags. Avoid synonyms of existing tags.
New tags are allowed but should be justified in the review step.

## Citation Rules
Every note must include provenance metadata (auto-populated).
Wiki page sections must reference contributing note IDs as:
  [Source: n0012, n0015]

## Quality Standards
- Notes: minimum 1 sentence, maximum 3 sentences
- Every note needs at least 1 tag
- Claims must be specific — avoid vague generalisations

## Connection Rules
Look for: supports, contradicts, extends, similar.
Only create connections you can explain in one sentence.

## Wiki Page Guidelines
- One page per distinct topic (not per heartbeat)
- Structure: Overview > Key Points > Details > Open Questions
- Update existing pages before creating new ones
- Cite all contributing notes in each section
"""


_DEFAULT_HEARTBEAT_SCHEMA = """\
# Heartbeat Strategy: Default

## Phase
**Discovery** — the brain is young. Focus on breadth.
Update to **Deepening** at 50+ notes and 3+ wiki pages.
Update to **Maintenance** at 200+ notes.

## Cycle Focus Rules
| Brain state | Focus this cycle |
|-------------|-----------------|
| < 10 notes | Capture a foundational concept |
| 10-50 notes, few connections | Prioritize connecting existing notes |
| 50+ notes, < 3 wiki pages | Trigger consolidation |
| 50+ notes, 3+ wiki pages | Alternate: deepen existing page / explore new angle |
| Lint flagged issues | Resolve highest-severity lint issue first |

## Priority Stack
1. Fix flagged lint issues
2. Consolidate pending notes
3. Deepen a weak wiki page
4. Capture something new
5. Explore connections

## Adaptive Behavior
- High error rate (3+ errors in last 10 heartbeats): slow down, review existing knowledge
- Repetitive captures (last 3 notes overlap): force a different topic
- Empty review ("nothing new"): skip capture, focus on connections or consolidation

## Escalation Rules
Flag for human attention:
- Contradiction between high-confidence wiki pages
- Same topic captured 3+ times without resolution
- Tool failure 3 consecutive heartbeats
- Zero new notes for 5+ heartbeats

## Cycle Continuity
Begin by reading the previous review summary. Avoid repeating
what was just captured. Follow up on suggested next areas.

## Resource Constraints
- Status check: 2-3 sentences
- Capture: 2-3 sentence content per note
- Review: 2-3 sentences, focus on actionable next steps
"""


def _load_schema_file(
    persona_dir: str, filename: str, default: str,
    config_path: str = "",
) -> str:
    """Load a schema markdown file from the persona directory.

    Resolution order:
    1. Explicit path from mcp.json config (``config_path``)
    2. ``<persona_dir>/<filename>`` (convention-based)
    3. Built-in default string
    """
    # 1. Explicit config path
    if config_path:
        content = _load_instructions(config_path)
        if content:
            return content

    # 2. Convention: file in persona directory
    if persona_dir:
        candidate = os.path.join(persona_dir, filename)
        if os.path.isfile(candidate):
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    return f.read().strip()
            except OSError as e:
                logger.warning("Failed to read %s: %s", candidate, e)

    # 3. Default
    return default


def _extract_functions(
    mod: Any, names: list[str] | None, source: str
) -> list[Callable]:
    """Collect public functions from a module, optionally filtered by name."""
    all_funcs: dict[str, Callable] = {
        n: obj
        for n, obj in inspect.getmembers(mod, inspect.isfunction)
        if not n.startswith("_")
    }
    if names:
        tools = []
        for fn in names:
            if fn in all_funcs:
                tools.append(all_funcs[fn])
            else:
                logger.warning("Function '%s' not found in %s", fn, source)
        return tools
    return list(all_funcs.values())


def _load_tools_from_module(
    module_path: str, function_names: list[str] | None = None
) -> list[Callable]:
    """Import a dotted module path and collect public functions."""
    try:
        mod = importlib.import_module(module_path)
    except ModuleNotFoundError:
        logger.warning("Tools module not found: %s", module_path)
        return []
    return _extract_functions(mod, function_names, module_path)


def _load_tools_from_file(
    file_path: str, function_names: list[str] | None = None
) -> list[Callable]:
    """Load tools from an absolute path to a .py file.

    Used for external personas where the tools live in a 3rd-party repo
    that is not (and should not need to be) an installed Python package.
    """
    if not os.path.isfile(file_path):
        logger.warning("Tools file not found: %s", file_path)
        return []
    mod_name = f"_persona_tools_{abs(hash(file_path))}"
    spec = importlib.util.spec_from_file_location(mod_name, file_path)
    if spec is None or spec.loader is None:
        logger.warning("Could not create module spec for: %s", file_path)
        return []
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return _extract_functions(mod, function_names, file_path)


def _resolve_mcp_servers(
    server_names: list[str], server_pool: dict[str, dict]
) -> dict[str, dict]:
    """Map persona server references to configs from the shared pool."""
    resolved: dict[str, dict] = {}
    for name in server_names:
        if name in server_pool:
            resolved[name] = dict(server_pool[name])
        else:
            logger.warning("MCP server '%s' not found in mcpServers pool", name)
    return resolved


# ──────────────────────────────────────────────────────────────────────
# External persona discovery
# ──────────────────────────────────────────────────────────────────────

def _check_persona_json(
    directory: str, result: dict[str, tuple[dict, str]]
) -> None:
    """If *directory* contains a ``persona.json``, register it in *result*."""
    manifest_path = os.path.join(directory, "persona.json")
    if not os.path.isfile(manifest_path):
        return
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        name = data.get("name") or os.path.basename(directory)
        result[name] = (data, directory)
        logger.debug("Discovered external persona '%s' at %s", name, directory)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read persona.json at %s: %s", directory, exc)


def _discover_external_personas(
    paths: list[str], mcp_dir: str
) -> dict[str, tuple[dict, str]]:
    """Scan ``personasPaths`` entries for ``persona.json`` manifests.

    Each path entry in the list can be:
    - An absolute filesystem path
    - A path relative to the directory containing ``mcp.json``

    Within each resolved directory the loader checks:
    - The directory itself (single-persona repo layout)
    - Every immediate subdirectory (multi-persona repo layout)

    Returns a mapping of ``{persona_name: (manifest_dict, persona_dir)}``.
    """
    found: dict[str, tuple[dict, str]] = {}
    for raw_path in paths:
        base = (
            raw_path
            if os.path.isabs(raw_path)
            else os.path.normpath(os.path.join(mcp_dir, raw_path))
        )
        if not os.path.isdir(base):
            logger.warning("personasPath not found or not a directory: %s", base)
            continue
        _check_persona_json(base, found)
        for entry in os.scandir(base):
            if entry.is_dir(follow_symlinks=True):
                _check_persona_json(entry.path, found)
    return found


# ──────────────────────────────────────────────────────────────────────
# Builtin fallback
# ──────────────────────────────────────────────────────────────────────

_BUILTIN_DEFAULT = Persona(
    name="default",
    description="Second-brain knowledge agent",
    instructions=(
        "You are a knowledge-management assistant. Your responsibilities:\n"
        "1. Capture and organize new knowledge as atomic notes\n"
        "2. Discover connections between ideas\n"
        "3. Periodically review and synthesize your knowledge base\n"
        "4. Identify gaps and suggest areas to explore\n"
        "When asked to return JSON, return ONLY valid JSON with no extra text."
    ),
    heartbeat_task=(
        "Review the existing notes in the brain summary above. Then do ONE of:\n"
        "(a) Identify a GAP or missing connection between existing notes and "
        "generate a practical insight that bridges them.\n"
        "(b) Synthesize or refine an existing note with more depth or nuance.\n"
        "(c) Generate a PRACTICAL, actionable insight relevant to the user's "
        "current domain and work.\n"
        "DO NOT repeat topics already covered. Check the brain summary carefully.\n"
        "Prefer concrete, specific insights over abstract/theoretical ones."
    ),
    extension_tools=[],
    inherit_base_tools=True,
    tools=merge_base_and_extension_tools(
        get_base_tools(), [], inherit_base=True,
    ),
)


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────

def load_persona(name: str, config_path: str = _MCP_JSON) -> Persona:
    """Load a persona by name.

    Resolution order:
      1. Inline definition in ``mcp.json`` ``personas`` block
      2. External ``persona.json`` discovered from ``personasPaths``
      3. Built-in default
    """
    if not os.path.isfile(config_path):
        logger.warning("mcp.json not found at %s; using built-in default", config_path)
        return _BUILTIN_DEFAULT

    mcp_dir = os.path.dirname(os.path.abspath(config_path))
    data = _read_json(config_path)
    server_pool = data.get("mcpServers", {})
    inline = data.get("personas", {})

    # 1. Inline wins
    if name in inline:
        return _build_inline_persona(name, inline[name], server_pool, mcp_dir)

    # 2. External
    external = _discover_external_personas(data.get("personasPaths", []), mcp_dir)
    if name in external:
        manifest, persona_dir = external[name]
        return _build_external_persona(name, manifest, persona_dir, server_pool)

    logger.warning("Persona '%s' not found; falling back to default", name)
    if "default" in inline:
        return _build_inline_persona("default", inline["default"], server_pool, mcp_dir)
    if "default" in external:
        manifest, persona_dir = external["default"]
        return _build_external_persona("default", manifest, persona_dir, server_pool)
    return _BUILTIN_DEFAULT


def list_personas(config_path: str = _MCP_JSON) -> list[str]:
    """Return all available persona names (inline + external)."""
    if not os.path.isfile(config_path):
        return ["default"]
    mcp_dir = os.path.dirname(os.path.abspath(config_path))
    data = _read_json(config_path)
    names = list(data.get("personas", {}).keys())
    external = _discover_external_personas(data.get("personasPaths", []), mcp_dir)
    for n in external:
        if n not in names:
            names.append(n)
    return names


def _build_inline_persona(
    name: str, entry: dict, server_pool: dict, base_dir: str
) -> Persona:
    """Build a Persona from an inline mcp.json entry.
    Instruction paths are resolved relative to base_dir (the mcp.json directory).
    """
    instructions_path = entry.get("instructions", "")
    instructions = _load_instructions(instructions_path, base_dir)
    if not instructions:
        instructions = entry.get("description", "")

    # Derive persona directory from the instructions path for schema discovery
    persona_dir = ""
    if instructions_path:
        full = (
            instructions_path
            if os.path.isabs(instructions_path)
            else os.path.normpath(os.path.join(base_dir, instructions_path))
        )
        persona_dir = os.path.dirname(full)

    extension: list[Callable] = []
    tools_cfg = entry.get("tools")
    if tools_cfg and isinstance(tools_cfg, dict):
        module = tools_cfg.get("module", "")
        if module:
            extension = _load_tools_from_module(module, tools_cfg.get("functions"))
            logger.info("Persona '%s': loaded %d extension tools from module %s", name, len(extension), module)

    inherit = bool(entry.get("inheritBaseTools", entry.get("inherit_base_tools", True)))
    base_n = len(get_base_tools())
    merged = merge_base_and_extension_tools(
        get_base_tools(), extension, inherit_base=inherit,
    )
    logger.info(
        "Persona '%s': tools base=%d extension=%d merged=%d inherit_base_tools=%s",
        name, base_n, len(extension), len(merged), inherit,
    )

    mcp_servers = _resolve_servers(entry, server_pool, name)

    # Load governance schemas
    brain_schema = _load_schema_file(
        persona_dir, "BRAIN.md", _DEFAULT_BRAIN_SCHEMA,
        config_path=entry.get("brainSchema", ""),
    )
    hb_strategy = entry.get("heartbeatStrategy", {})
    heartbeat_schema = _load_schema_file(
        persona_dir, "HEARTBEAT.md", _DEFAULT_HEARTBEAT_SCHEMA,
        config_path=hb_strategy.get("file", "") if isinstance(hb_strategy, dict) else "",
    )

    return Persona(
        name=name,
        description=entry.get("description", ""),
        instructions=instructions,
        heartbeat_task=entry.get("heartbeatTask", "").strip(),
        extension_tools=list(extension),
        inherit_base_tools=inherit,
        tools=merged,
        mcp_servers=mcp_servers,
        workflow=entry.get("workflow", "second_brain"),
        steps=entry.get("steps") or None,
        stepwise=bool(entry.get("stepwise", False)),
        roster=entry.get("roster", []),
        schedule=entry.get("schedule", "round_robin"),
        consolidation_interval=int(entry.get("consolidation", {}).get("interval", 5)),
        consolidation_threshold=int(entry.get("consolidation", {}).get("threshold", 10)),
        lint_wiki_interval=int(entry.get("lint", {}).get("interval", 20)),
        dream_enabled=bool(entry.get("dream", {}).get("enabled", True)),
        dream_idle_streak_min=int(entry.get("dream", {}).get("idleStreakMin", 3)),
        dream_max_age_days=int(entry.get("dream", {}).get("maxAgeDays", 30)),
        prompt_dir=entry.get("promptDir", ""),
        done_marker=entry.get("doneMarker", "[TASK_COMPLETE]"),
        blocked_marker=entry.get("blockedMarker", "[TASK_BLOCKED]"),
        max_turns=int(entry.get("maxTurns", 20)),
        brain_schema=brain_schema,
        heartbeat_schema=heartbeat_schema,
        decisions_schema=_load_schema_file(persona_dir, "DECISIONS.md", ""),
        decision_policy=_resolve_decision_policy(entry.get("decisions")),
        capture_policy=_resolve_capture_policy(entry),
    )


def _build_external_persona(
    name: str, manifest: dict, persona_dir: str, server_pool: dict
) -> Persona:
    """Build a Persona from an external ``persona.json`` manifest.

    Instruction paths are resolved relative to the persona directory.
    Tools can be loaded from:
    - ``tools.file``   — path to a ``.py`` file relative to the persona dir
    - ``tools.module`` — dotted import path (package must be installed)
    """
    instructions = _load_instructions(
        manifest.get("instructions", "instructions.md"), persona_dir
    )
    if not instructions:
        instructions = manifest.get("description", "")

    extension: list[Callable] = []
    tools_cfg = manifest.get("tools")
    if tools_cfg and isinstance(tools_cfg, dict):
        functions = tools_cfg.get("functions")
        file_rel = tools_cfg.get("file", "")
        module_path = tools_cfg.get("module", "")
        if file_rel:
            abs_file = (
                file_rel
                if os.path.isabs(file_rel)
                else os.path.normpath(os.path.join(persona_dir, file_rel))
            )
            extension = _load_tools_from_file(abs_file, functions)
            logger.info(
                "Persona '%s': loaded %d extension tools from file %s", name, len(extension), abs_file,
            )
        elif module_path:
            extension = _load_tools_from_module(module_path, functions)
            logger.info(
                "Persona '%s': loaded %d extension tools from module %s",
                name, len(extension), module_path,
            )

    inherit = bool(manifest.get("inheritBaseTools", manifest.get("inherit_base_tools", True)))
    base_n = len(get_base_tools())
    merged = merge_base_and_extension_tools(
        get_base_tools(), extension, inherit_base=inherit,
    )
    logger.info(
        "Persona '%s': tools base=%d extension=%d merged=%d inherit_base_tools=%s",
        name, base_n, len(extension), len(merged), inherit,
    )

    mcp_servers = _resolve_servers(manifest, server_pool, name)

    # Load governance schemas
    brain_schema = _load_schema_file(
        persona_dir, "BRAIN.md", _DEFAULT_BRAIN_SCHEMA,
        config_path=manifest.get("brainSchema", ""),
    )
    hb_strategy = manifest.get("heartbeatStrategy", {})
    heartbeat_schema = _load_schema_file(
        persona_dir, "HEARTBEAT.md", _DEFAULT_HEARTBEAT_SCHEMA,
        config_path=hb_strategy.get("file", "") if isinstance(hb_strategy, dict) else "",
    )

    return Persona(
        name=name,
        description=manifest.get("description", ""),
        instructions=instructions,
        heartbeat_task=manifest.get("heartbeatTask", "").strip(),
        extension_tools=list(extension),
        inherit_base_tools=inherit,
        tools=merged,
        mcp_servers=mcp_servers,
        workflow=manifest.get("workflow", "second_brain"),
        steps=manifest.get("steps") or None,
        stepwise=bool(manifest.get("stepwise", False)),
        roster=manifest.get("roster", []),
        schedule=manifest.get("schedule", "round_robin"),
        consolidation_interval=int(manifest.get("consolidation", {}).get("interval", 5)),
        consolidation_threshold=int(manifest.get("consolidation", {}).get("threshold", 10)),
        lint_wiki_interval=int(manifest.get("lint", {}).get("interval", 20)),
        dream_enabled=bool(manifest.get("dream", {}).get("enabled", True)),
        dream_idle_streak_min=int(manifest.get("dream", {}).get("idleStreakMin", 3)),
        dream_max_age_days=int(manifest.get("dream", {}).get("maxAgeDays", 30)),
        prompt_dir=manifest.get("promptDir", ""),
        done_marker=manifest.get("doneMarker", "[TASK_COMPLETE]"),
        blocked_marker=manifest.get("blockedMarker", "[TASK_BLOCKED]"),
        max_turns=int(manifest.get("maxTurns", 20)),
        brain_schema=brain_schema,
        heartbeat_schema=heartbeat_schema,
        decisions_schema=_load_schema_file(persona_dir, "DECISIONS.md", ""),
        decision_policy=_resolve_decision_policy(manifest.get("decisions")),
        capture_policy=_resolve_capture_policy(manifest),
    )


def _resolve_servers(
    entry: dict, server_pool: dict, persona_name: str
) -> dict[str, dict] | None:
    """Resolve mcpServers references for a persona entry or manifest."""
    refs = entry.get("mcpServers", [])
    if not refs:
        return None
    resolved = _resolve_mcp_servers(refs, server_pool)
    if resolved:
        logger.info(
            "Persona '%s': attached MCP server(s): %s",
            persona_name,
            ", ".join(resolved.keys()),
        )
    return resolved or None
