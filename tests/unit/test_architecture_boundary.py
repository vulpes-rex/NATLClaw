"""Architectural boundary tests — prevent domain-specific code from leaking into the engine.

These tests scan the ENGINE layer files for domain-specific terms that
belong in the PERSONA layer.  If a test fails here, it means someone
added coding/git/testing-specific logic to a core module.  Move it to
a persona's tools.py, instructions.md, or prompt template instead.

Engine files are domain-agnostic infrastructure:
    workflow.py, scheduler.py, second_brain.py, state.py, learning.py,
    config.py, agent_setup.py, persona_loader.py, prompts.py, goals.py,
    metrics.py, ingest.py

Persona files are domain-specific extensions:
    personas/*/instructions.md, personas/*/tools.py, prompts/<mode>/*.txt,
    mcp.json persona entries
"""
from __future__ import annotations

import os
import re

import pytest

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

# ──────────────────────────────────────────────────────────────────────
# Definitions
# ──────────────────────────────────────────────────────────────────────

# Engine layer — these files MUST remain domain-agnostic.
ENGINE_FILES = [
    "workflow.py",
    "scheduler.py",
    "second_brain.py",
    "state.py",
    "learning.py",
    "config.py",
    "agent_setup.py",
    "persona_loader.py",
    "prompts.py",
    "goals.py",
    "metrics.py",
    "event_watcher.py",
    "daily_digest.py",
    "execution_log.py",
]

# Domain-specific terms that should NOT appear in engine files.
# Each entry is (regex_pattern, description).
# Patterns are case-insensitive and match whole words (word boundaries).
BANNED_PATTERNS: list[tuple[str, str]] = [
    # Coding-specific
    (r"\bcoding[ _-]?agent\b", "coding-agent reference"),
    (r"\bcoding[ _-]?assistant\b", "coding-assistant reference"),
    (r"\bpytest\b", "pytest reference"),
    (r"\bruff\b", "ruff linter reference"),
    (r"\bmypy\b", "mypy reference"),
    (r"\brefactor\b", "refactoring reference"),
    (r"\bunit[ _-]?test\b", "unit-test reference"),
    (r"\bwrite_source_file\b", "coding tool reference"),
    (r"\bread_source_file\b", "coding tool reference"),
    (r"\brun_shell_command\b", "shell tool reference"),

    # Git-specific (OK in persona tools, not in engine)
    (r"\bgit[ _-]?log\b", "git-log reference"),
    (r"\bgit[ _-]?diff\b", "git-diff reference"),
    (r"\bgit[ _-]?commit\b", "git-commit reference"),

    # Framework-specific
    (r"\breact\b", "React framework reference"),
    (r"\btypescript\b", "TypeScript reference"),
    (r"\bdocker\b", "Docker reference"),
]

# Lines matching these patterns are EXEMPT (comments explaining the
# boundary, imports, legacy constant names kept for backward compat, etc.)
EXEMPTION_PATTERNS: list[str] = [
    r"^\s*#",          # comment lines
    r"^\s*\"\"\"",     # docstring open
    r"^\s*'''",        # docstring open (alt)
    r"BANNED_PATTERNS", # this test's own references
    r"Legacy constants", # kept for backward compat
    r"backward.compat", # explanatory notes
    r"[\"']type[\"']\s*:", # event-type dict literals (e.g. "type": "git_commit")
]


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _read_engine_file(filename: str) -> list[tuple[int, str]]:
    """Read an engine file and return (line_number, line_text) pairs."""
    path = os.path.join(_PROJECT_ROOT, filename)
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return list(enumerate(f.readlines(), start=1))


def _is_exempt(line: str) -> bool:
    """Check if a line is exempt from scanning (comments, docstrings, examples)."""
    stripped = line.strip()
    # Comment lines
    if stripped.startswith("#"):
        return True
    # Docstring lines (inside triple-quoted blocks or continuations)
    if stripped.startswith('"""') or stripped.startswith("'''"):
        return True
    # Lines that are purely docstring continuation (no code, just text)
    # Heuristic: if the line doesn't contain '=' or '(' it's likely prose
    if not any(c in line for c in ["=", "(", ")", "import "]) and stripped:
        return True
    # Explicit exemption patterns
    return any(re.search(pat, line) for pat in EXEMPTION_PATTERNS)


def _is_exempt(line: str) -> bool:
    """Check if a line matches an exemption pattern."""
    return any(re.search(pat, line) for pat in EXEMPTION_PATTERNS)


def _scan_file(filename: str) -> list[str]:
    """Scan an engine file for banned domain-specific terms.

    Skips comments (``#``), docstrings (``\"\"\"…\"\"\"``, ``'''…'''``),
    and lines matching EXEMPTION_PATTERNS.

    Returns a list of violation descriptions.
    """
    violations: list[str] = []
    lines = _read_engine_file(filename)
    in_docstring = False
    docstring_delim = None

    for lineno, line in lines:
        stripped = line.strip()

        # Track triple-quoted docstring regions
        if not in_docstring:
            for delim in ('"""', "'''"):
                if delim in stripped:
                    # Single-line docstring: opens and closes on same line
                    count = stripped.count(delim)
                    if count >= 2:
                        # Opens and closes — skip this line entirely
                        continue
                    elif count == 1:
                        in_docstring = True
                        docstring_delim = delim
                        break
            if in_docstring:
                continue
        else:
            if docstring_delim and docstring_delim in stripped:
                in_docstring = False
                docstring_delim = None
            continue

        # Skip comment lines
        if stripped.startswith("#"):
            continue

        # Skip inline comments (only scan the code portion before #)
        code_part = line.split(" #")[0] if " #" in line else line

        if _is_exempt(code_part):
            continue

        for pattern, description in BANNED_PATTERNS:
            if re.search(pattern, code_part, re.IGNORECASE):
                violations.append(
                    f"{filename}:{lineno}: {description} — {line.rstrip()}"
                )
    return violations


# ──────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────

class TestEngineBoundary:
    """Ensure engine files stay domain-agnostic."""

    @pytest.mark.parametrize("filename", ENGINE_FILES)
    def test_no_domain_specific_terms(self, filename: str):
        """Engine file must not contain domain-specific terms."""
        violations = _scan_file(filename)
        if violations:
            msg = (
                f"\nDomain-specific code detected in engine file '{filename}'.\n"
                f"Move this logic to a persona (tools.py, instructions.md, "
                f"or prompt template).\n\n"
                + "\n".join(f"  {v}" for v in violations)
            )
            pytest.fail(msg)

    def test_engine_files_exist(self):
        """All declared engine files should exist in the project."""
        missing = [
            f for f in ENGINE_FILES
            if not os.path.isfile(os.path.join(_PROJECT_ROOT, f))
        ]
        assert not missing, f"Engine files missing from project: {missing}"

    def test_prompt_templates_use_placeholders(self):
        """Prompt templates must use {done_marker}/{blocked_marker} not hardcoded values."""
        prompts_dir = os.path.join(_PROJECT_ROOT, "prompts")
        violations: list[str] = []
        for root, _dirs, files in os.walk(prompts_dir):
            for fname in files:
                if not fname.endswith(".txt"):
                    continue
                path = os.path.join(root, fname)
                rel = os.path.relpath(path, _PROJECT_ROOT)
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                # Check for hardcoded markers (should use {done_marker} etc.)
                if "[TASK_COMPLETE]" in content:
                    violations.append(
                        f"{rel}: hardcoded [TASK_COMPLETE] — use {{done_marker}}"
                    )
                if "[TASK_BLOCKED]" in content:
                    violations.append(
                        f"{rel}: hardcoded [TASK_BLOCKED] — use {{blocked_marker}}"
                    )
        if violations:
            pytest.fail(
                "\nHardcoded completion markers in prompt templates.\n"
                "Use {done_marker} / {blocked_marker} placeholders instead.\n\n"
                + "\n".join(f"  {v}" for v in violations)
            )

    def test_cli_cmd_code_uses_persona_config(self):
        """cmd_code must read markers/turns/prompts from persona, not constants."""
        path = os.path.join(_PROJECT_ROOT, "cli.py")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        # Find the cmd_code function body
        start = content.find("def cmd_code(")
        assert start != -1, "cmd_code function not found"
        # Find the next top-level def (dedented to column 0)
        end = content.find("\ndef ", start + 1)
        body = content[start:end] if end != -1 else content[start:]

        violations: list[str] = []

        # Should NOT reference module-level constants directly in logic
        if "_DONE_MARKER" in body:
            violations.append(
                "cmd_code references _DONE_MARKER — use persona.done_marker"
            )
        if "_BLOCKED_MARKER" in body:
            violations.append(
                "cmd_code references _BLOCKED_MARKER — use persona.blocked_marker"
            )
        if "_DEFAULT_MAX_TURNS" in body:
            violations.append(
                "cmd_code references _DEFAULT_MAX_TURNS — use persona.max_turns"
            )

        # Should use persona.prompt_dir, not hardcoded "coding_agent"
        hardcoded_prompt = re.findall(
            r'load_prompt\(\s*["\']coding_agent["\']', body
        )
        if hardcoded_prompt:
            violations.append(
                "cmd_code hardcodes 'coding_agent' in load_prompt — use prompt_mode"
            )

        if violations:
            pytest.fail(
                "\ncmd_code still has hardcoded domain references.\n\n"
                + "\n".join(f"  {v}" for v in violations)
            )


class TestPersonaLayerIntegrity:
    """Ensure new personas can be added without touching engine files."""

    def test_persona_dataclass_has_extension_fields(self):
        """Persona must expose prompt_dir, done_marker, blocked_marker, max_turns."""
        from persona_loader import Persona
        p = Persona(name="test", description="test", instructions="test")
        assert hasattr(p, "prompt_dir")
        assert hasattr(p, "done_marker")
        assert hasattr(p, "blocked_marker")
        assert hasattr(p, "max_turns")

    def test_default_persona_is_domain_agnostic(self):
        """The builtin default persona must not reference specific domains."""
        from persona_loader import _BUILTIN_DEFAULT

        text = (
            _BUILTIN_DEFAULT.instructions + "\n"
            + _BUILTIN_DEFAULT.heartbeat_task + "\n"
            + _BUILTIN_DEFAULT.description
        ).lower()

        domain_terms = [
            "software engineering",
            "python",
            "react",
            "typescript",
            "docker",
            "git",
            "coding",
        ]
        found = [t for t in domain_terms if t in text]
        assert not found, (
            f"Default persona references specific domains: {found}. "
            f"The default must be domain-agnostic."
        )
