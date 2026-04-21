"""Tools for the workspace_observer persona.

Observe the user's actual workspace activity: git history, file changes,
TODOs, and work patterns.  All tools are **read-only** — nothing is
written back to the user's project files.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import asyncio
from pathlib import Path
from typing import Annotated

import event_watcher

WORKSPACE = os.environ.get("NATL_WORKSPACE", ".")

# Markers we look for when scanning for action items
_TODO_PATTERN = re.compile(
    r"\b(TODO|FIXME|HACK|XXX|WARN|NOTE)\b[:\s]*(.*)", re.IGNORECASE
)
_MAX_SCAN_FILES = 2000


# ──────────────────────────────────────────────────────────────────────
# Event queue  (shared format with codebase_learner)
# ──────────────────────────────────────────────────────────────────────

def drain_events() -> str:
    """Read and clear all pending workspace events since the last heartbeat.

    Events are newline-delimited JSON objects appended by file watchers
    and git hooks between heartbeats.
    """
    queue: asyncio.PriorityQueue[tuple[int, int, str, dict]] = asyncio.PriorityQueue()
    drained = event_watcher.drain_pending_events(queue)
    if drained <= 0:
        return "No pending events."
    records: list[str] = []
    while not queue.empty():
        try:
            priority, _seq, event_type, payload = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        records.append(
            json.dumps(
                {
                    "priority": priority,
                    "event_type": event_type,
                    "payload": payload,
                },
                ensure_ascii=False,
            )
        )
    return ("\n".join(records))[:6000] or "No pending events."


# ──────────────────────────────────────────────────────────────────────
# Git observation
# ──────────────────────────────────────────────────────────────────────

def read_git_log(
    count: Annotated[int, "Number of recent commits to show"] = 10,
) -> str:
    """Read recent git commits with file-change stats."""
    try:
        result = subprocess.run(
            ["git", "log", f"-{min(count, 50)}", "--oneline", "--stat"],
            capture_output=True,
            cwd=WORKSPACE,
            timeout=10,
        )
        stdout = result.stdout.decode("utf-8", errors="replace")
        return stdout[:4000] or "(no commits)"
    except FileNotFoundError:
        return "git is not installed or not on PATH"
    except subprocess.TimeoutExpired:
        return "git log timed out"
    except Exception as e:
        return f"Git log failed: {e}"


def run_git_log(
    count: Annotated[int, "Number of recent commits to show"] = 10,
) -> str:
    """Alias for :func:`read_git_log` — some clients expect this tool name."""
    return read_git_log(count)


def read_git_diff(
    ref: Annotated[str, "Git ref to diff against (e.g. HEAD~1, main)"] = "HEAD~1",
) -> str:
    """Read a git diff against a reference to see what changed."""
    safe_ref = "".join(c for c in ref if c.isalnum() or c in "~^.-_/")
    if not safe_ref:
        return "Invalid git ref"
    try:
        result = subprocess.run(
            ["git", "diff", safe_ref, "--stat", "-p"],
            capture_output=True,
            cwd=WORKSPACE,
            timeout=10,
        )
        stdout = result.stdout.decode("utf-8", errors="replace")
        return stdout[:6000] or "(no diff)"
    except FileNotFoundError:
        return "git is not installed or not on PATH"
    except subprocess.TimeoutExpired:
        return "git diff timed out"
    except Exception as e:
        return f"Git diff failed: {e}"


def read_git_branch() -> str:
    """Return the current branch name and a short list of recent branches."""
    try:
        current = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, cwd=WORKSPACE, timeout=5,
        )
        recent = subprocess.run(
            ["git", "branch", "--sort=-committerdate", "--format=%(refname:short)"],
            capture_output=True, cwd=WORKSPACE, timeout=5,
        )
        cur_branch = current.stdout.decode("utf-8", errors="replace").strip()
        branches = recent.stdout.decode("utf-8", errors="replace").strip().splitlines()[:10]
        return (
            f"Current branch: {cur_branch}\n"
            f"Recent branches:\n" + "\n".join(f"  {b}" for b in branches)
        )
    except Exception as e:
        return f"Could not read branches: {e}"


def analyse_current_git_commit() -> str:
    """Inspect the latest commit (same output shape as ``read_git_log(1)``)."""
    return read_git_log(1)


# ──────────────────────────────────────────────────────────────────────
# Step-name compatibility (some hosts list scheduler step labels as tools)
# ──────────────────────────────────────────────────────────────────────


def gather() -> str:
    """Scheduler step label ``gather`` — use drain_events, read_git_log, read_git_branch, list_recently_modified."""
    return (
        "For the gather step, call drain_events, read_git_log, read_git_branch, list_recently_modified; "
        "then write your bullet summary as the step output."
    )


def analyse() -> str:
    """Scheduler step label ``analyse`` — use read_git_diff, scan_todos, read_file, read_git_log; then return step JSON."""
    return (
        "For the analyse step, call read_git_diff / scan_todos / read_file / read_git_log as needed; "
        "then return the JSON object required by the analyse step prompt."
    )


def connect() -> str:
    """Scheduler step label ``connect`` — link notes using IDs from ``{brain}`` in the prompt."""
    return (
        "For the connect step, use note IDs from the brain summary in your instructions; "
        'reply with JSON {"from","to","reason"} or {"skip":true}.'
    )


def observer_step_gather() -> str:
    """Same guidance as :func:`gather` — use if the host omits short tool names."""
    return gather()


def observer_step_analyse() -> str:
    """Same guidance as :func:`analyse` — use if the host omits short tool names."""
    return analyse()


def observer_step_connect() -> str:
    """Same guidance as :func:`connect` — use if the host blocks the name ``connect``."""
    return connect()


def analyse_capture() -> str:
    """When the UI shows ``analyse_capture`` — still the analyse step + brain capture; use git/file tools then return JSON."""
    return (
        "This is the analyse step with storeToBrain: use read_git_diff, scan_todos, read_file, read_git_log; "
        "then return the JSON object (content, tags, evidence, confidence) required by the prompt."
    )


# ──────────────────────────────────────────────────────────────────────
# Workspace scanning
# ──────────────────────────────────────────────────────────────────────

def scan_todos(
    extensions: Annotated[str, "Comma-separated file extensions to scan, e.g. py,ts,js"] = "py,ts,js,jsx,tsx",
    max_results: Annotated[int, "Maximum number of TODO items to return"] = 30,
) -> str:
    """Scan workspace files for TODO/FIXME/HACK/XXX comments."""
    ws = Path(WORKSPACE)
    if not ws.is_dir():
        return f"Workspace directory not found: {WORKSPACE}"

    exts = {f".{e.strip()}" for e in extensions.split(",")}
    hits: list[str] = []
    scanned_files = 0

    for root, dirs, files in os.walk(ws):
        # Skip common non-source directories
        dirs[:] = [
            d for d in dirs
            if d not in {"node_modules", ".git", "__pycache__", "venv", ".venv", "dist", "build"}
        ]
        for fname in files:
            if scanned_files >= _MAX_SCAN_FILES:
                break
            if Path(fname).suffix not in exts:
                continue
            fpath = Path(root) / fname
            scanned_files += 1
            try:
                for lineno, line in enumerate(
                    fpath.read_text(encoding="utf-8", errors="ignore").splitlines(),
                    start=1,
                ):
                    m = _TODO_PATTERN.search(line)
                    if m:
                        rel = fpath.relative_to(ws)
                        hits.append(f"{rel}:{lineno}  {m.group(1)}: {m.group(2).strip()}")
                        if len(hits) >= max_results:
                            break
            except OSError:
                continue
            if len(hits) >= max_results:
                break
        if len(hits) >= max_results:
            break

    if not hits:
        return "No TODO/FIXME/HACK items found."
    if scanned_files >= _MAX_SCAN_FILES:
        hits.append(f"... scan capped at {_MAX_SCAN_FILES} files")
    return "\n".join(hits)


def list_recently_modified(
    count: Annotated[int, "Number of files to return"] = 15,
) -> str:
    """List workspace files ordered by modification time (most recent first)."""
    ws = Path(WORKSPACE)
    if not ws.is_dir():
        return f"Workspace directory not found: {WORKSPACE}"

    files: list[tuple[float, Path]] = []
    scanned_files = 0
    for root, dirs, fnames in os.walk(ws):
        dirs[:] = [
            d for d in dirs
            if d not in {"node_modules", ".git", "__pycache__", "venv", ".venv", "dist", "build"}
        ]
        for fname in fnames:
            if scanned_files >= _MAX_SCAN_FILES:
                break
            fp = Path(root) / fname
            scanned_files += 1
            try:
                files.append((fp.stat().st_mtime, fp))
            except OSError:
                continue
        if scanned_files >= _MAX_SCAN_FILES:
            break

    files.sort(reverse=True)
    lines = []
    for mtime, fp in files[: min(count, 50)]:
        from datetime import datetime
        ts = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        rel = fp.relative_to(ws)
        lines.append(f"{ts}  {rel}")
    if scanned_files >= _MAX_SCAN_FILES:
        lines.append(f"... scan capped at {_MAX_SCAN_FILES} files")
    return "\n".join(lines) or "(empty workspace)"


# ──────────────────────────────────────────────────────────────────────
# Deep analysis tools (used during workspace audits / reports)
# ──────────────────────────────────────────────────────────────────────

def read_file(
    path: Annotated[str, "Relative path to the file within the workspace"],
    max_lines: Annotated[int, "Maximum number of lines to read"] = 200,
) -> str:
    """Read a file's contents (read-only). Use for inspecting source code,
    config files, documentation, or test files during an audit."""
    ws = Path(WORKSPACE)
    target = ws / path
    # Safety: ensure path doesn't escape workspace
    try:
        target.resolve().relative_to(ws.resolve())
    except ValueError:
        return f"Error: path escapes workspace: {path}"
    if not target.is_file():
        return f"File not found: {path}"
    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        truncated = lines[:max_lines]
        result = "\n".join(f"{i+1:4d}  {line}" for i, line in enumerate(truncated))
        if len(lines) > max_lines:
            result += f"\n... ({len(lines) - max_lines} more lines)"
        return result
    except OSError as e:
        return f"Error reading {path}: {e}"


def list_directory(
    path: Annotated[str, "Relative directory path (use '.' for root)"] = ".",
    pattern: Annotated[str, "Glob pattern to filter files, e.g. '*.py'"] = "*",
) -> str:
    """List files in a directory, optionally filtered by glob pattern.
    Useful for discovering project structure during an audit."""
    ws = Path(WORKSPACE)
    target = ws / path
    try:
        target.resolve().relative_to(ws.resolve())
    except ValueError:
        return f"Error: path escapes workspace: {path}"
    if not target.is_dir():
        return f"Directory not found: {path}"

    entries: list[str] = []
    for item in sorted(target.glob(pattern)):
        if item.name.startswith(".") and item.name not in (".env.example",):
            continue
        if item.name in {"node_modules", "__pycache__", ".git", "venv", ".venv"}:
            continue
        rel = item.relative_to(ws)
        marker = "DIR " if item.is_dir() else "    "
        entries.append(f"{marker}{rel}")
    return "\n".join(entries[:100]) or "(empty)"


def check_imports(
    path: Annotated[str, "Relative path to a Python file"],
) -> str:
    """Check whether all imports in a Python file resolve correctly.
    Returns a list of broken/missing imports."""
    ws = Path(WORKSPACE)
    target = ws / path
    if not target.is_file():
        return f"File not found: {path}"

    import ast
    try:
        source = target.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=path)
    except SyntaxError as e:
        return f"Syntax error in {path}: {e}"

    issues: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _check_module(alias.name, path, node.lineno, issues)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                _check_module(node.module, path, node.lineno, issues)

    if not issues:
        return f"All imports in {path} resolve correctly."
    return "\n".join(issues)


def _check_module(module: str, filepath: str, lineno: int, issues: list[str]) -> None:
    """Try to import a module; if it fails, log the issue."""
    import importlib
    top = module.split(".")[0]
    # Skip stdlib and known third-party
    try:
        importlib.import_module(top)
    except ImportError:
        issues.append(f"  {filepath}:{lineno}  MISSING: {module}")
    except Exception:
        pass  # some modules have side effects on import


def search_codebase(
    query: Annotated[str, "Text or regex pattern to search for"],
    extensions: Annotated[str, "Comma-separated file extensions, e.g. 'py,ts'"] = "py",
    max_results: Annotated[int, "Maximum matches to return"] = 30,
) -> str:
    """Search for a text pattern across workspace files. Useful for finding
    dead code, inconsistencies, hardcoded values, or usage of a function."""
    ws = Path(WORKSPACE)
    exts = {f".{e.strip()}" for e in extensions.split(",")}
    pattern = re.compile(query, re.IGNORECASE)
    hits: list[str] = []

    for root, dirs, files in os.walk(ws):
        dirs[:] = [
            d for d in dirs
            if d not in {"node_modules", ".git", "__pycache__", "venv", ".venv", "dist", "build", "data"}
        ]
        for fname in files:
            if Path(fname).suffix not in exts:
                continue
            fpath = Path(root) / fname
            try:
                for lineno, line in enumerate(
                    fpath.read_text(encoding="utf-8", errors="ignore").splitlines(),
                    start=1,
                ):
                    if pattern.search(line):
                        rel = fpath.relative_to(ws)
                        hits.append(f"{rel}:{lineno}  {line.strip()[:120]}")
                        if len(hits) >= max_results:
                            return "\n".join(hits)
            except OSError:
                continue

    return "\n".join(hits) if hits else f"No matches for '{query}'"


def run_tests(
    args: Annotated[str, "Extra pytest arguments, e.g. '--tb=short -q'"] = "-q --tb=line --no-header",
) -> str:
    """Run the project's test suite and return the summary. Read-only
    observation of test health — does not modify any files."""
    try:
        result = subprocess.run(
            ["python", "-m", "pytest"] + args.split(),
            capture_output=True,
            cwd=WORKSPACE,
            timeout=120,
        )
        out = result.stdout.decode("utf-8", errors="replace")
        err = result.stderr.decode("utf-8", errors="replace")
        combined = out + ("\n" + err if err else "")
        return combined[-4000:] or "(no output)"
    except FileNotFoundError:
        return "pytest not installed"
    except subprocess.TimeoutExpired:
        return "Tests timed out after 120s"
    except Exception as e:
        return f"Test run failed: {e}"


def analyze_module_structure() -> str:
    """Analyze the project's Python module structure: which .py files exist,
    their sizes, and import relationships. Good for architecture overview."""
    ws = Path(WORKSPACE)
    modules: list[dict] = []

    for py_file in sorted(ws.glob("*.py")):
        try:
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            lines = content.splitlines()
            imports = [
                l.strip() for l in lines
                if l.strip().startswith(("import ", "from "))
                and not l.strip().startswith("#")
            ]
            # Extract local imports (not stdlib/third-party)
            local_imports = []
            for imp in imports:
                parts = imp.replace("from ", "").replace("import ", "").split()[0].split(".")
                top = parts[0]
                if (ws / f"{top}.py").exists() or (ws / top).is_dir():
                    local_imports.append(top)

            modules.append({
                "file": py_file.name,
                "lines": len(lines),
                "imports": len(imports),
                "local_deps": sorted(set(local_imports)),
            })
        except OSError:
            continue

    if not modules:
        return "No Python modules found in workspace root."

    lines = [f"{'Module':<25s} {'Lines':>6s}  {'Imports':>7s}  Local Dependencies"]
    lines.append("-" * 80)
    for m in modules:
        deps = ", ".join(m["local_deps"]) if m["local_deps"] else "(none)"
        lines.append(f"{m['file']:<25s} {m['lines']:>6d}  {m['imports']:>7d}  {deps}")
    return "\n".join(lines)


def check_docs_vs_implementation() -> str:
    """Compare docs/ folder contents against actual implementation.
    Scans documentation for feature mentions and checks if corresponding
    code exists. Useful for finding planned-but-not-started features."""
    ws = Path(WORKSPACE)
    docs_dir = ws / "docs"
    if not docs_dir.is_dir():
        return "No docs/ directory found."

    report: list[str] = []
    for doc_file in sorted(docs_dir.glob("*.md")):
        try:
            content = doc_file.read_text(encoding="utf-8", errors="ignore")
            # Extract headings and status markers
            headings = re.findall(r"^#+\s+(.+)$", content, re.MULTILINE)
            not_started = re.findall(r"(?:not started|TODO|planned|future)", content, re.IGNORECASE)
            done_markers = re.findall(r"(?:✅|done|completed|implemented)", content, re.IGNORECASE)
            partial_markers = re.findall(r"(?:⚠️|partial|in.progress|started)", content, re.IGNORECASE)

            status = f"Done:{len(done_markers)} Partial:{len(partial_markers)} Planned:{len(not_started)}"
            report.append(f"\n{doc_file.name} ({len(headings)} sections, {status})")
            for h in headings[:8]:
                report.append(f"  - {h[:80]}")
        except OSError:
            continue

    return "\n".join(report) if report else "No documentation files found."
