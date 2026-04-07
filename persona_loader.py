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
    # persona.instructions  — str (markdown content)
    # persona.tools         — list[Callable]
    # persona.mcp_servers   — dict[str, dict] | None
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
    tools: list[Callable[..., Any]] = field(default_factory=list)
    mcp_servers: dict[str, dict] | None = None
    workflow: str = "second_brain"
    steps: list[dict] | None = None
    stepwise: bool = False


# ──────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────

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
    description="Autonomous second-brain knowledge agent",
    instructions=(
        "You are an autonomous second-brain agent. Your responsibilities:\n"
        "1. Capture and organize new knowledge as atomic notes\n"
        "2. Discover connections between ideas\n"
        "3. Periodically review and synthesize your knowledge base\n"
        "4. Identify gaps and suggest areas to explore\n"
        "When asked to return JSON, return ONLY valid JSON with no extra text."
    ),
    heartbeat_task=(
        "Research one new insight about AI agents, autonomous systems, or "
        "knowledge management that is NOT already captured in the brain."
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
    instructions = _load_instructions(entry.get("instructions", ""), base_dir)
    if not instructions:
        instructions = entry.get("description", "")

    tools: list[Callable] = []
    tools_cfg = entry.get("tools")
    if tools_cfg and isinstance(tools_cfg, dict):
        module = tools_cfg.get("module", "")
        if module:
            tools = _load_tools_from_module(module, tools_cfg.get("functions"))
            logger.info("Persona '%s': loaded %d tools from module %s", name, len(tools), module)

    mcp_servers = _resolve_servers(entry, server_pool, name)

    return Persona(
        name=name,
        description=entry.get("description", ""),
        instructions=instructions,
        heartbeat_task=entry.get("heartbeatTask", "").strip(),
        tools=tools,
        mcp_servers=mcp_servers,
        workflow=entry.get("workflow", "second_brain"),
        steps=entry.get("steps") or None,
        stepwise=bool(entry.get("stepwise", False)),
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

    tools: list[Callable] = []
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
            tools = _load_tools_from_file(abs_file, functions)
            logger.info("Persona '%s': loaded %d tools from file %s", name, len(tools), abs_file)
        elif module_path:
            tools = _load_tools_from_module(module_path, functions)
            logger.info("Persona '%s': loaded %d tools from module %s", name, len(tools), module_path)

    mcp_servers = _resolve_servers(manifest, server_pool, name)

    return Persona(
        name=name,
        description=manifest.get("description", ""),
        instructions=instructions,
        heartbeat_task=manifest.get("heartbeatTask", "").strip(),
        tools=tools,
        mcp_servers=mcp_servers,
        workflow=manifest.get("workflow", "second_brain"),
        steps=manifest.get("steps") or None,
        stepwise=bool(manifest.get("stepwise", False)),
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
