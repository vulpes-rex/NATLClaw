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
from pathlib import Path
from typing import Annotated

WORKSPACE = os.environ.get("NATL_WORKSPACE", ".")
EVENT_QUEUE_PATH = os.path.join("data", "event_queue.json")

# Markers we look for when scanning for action items
_TODO_PATTERN = re.compile(
    r"\b(TODO|FIXME|HACK|XXX|WARN|NOTE)\b[:\s]*(.*)", re.IGNORECASE
)


# ──────────────────────────────────────────────────────────────────────
# Event queue  (shared format with codebase_learner)
# ──────────────────────────────────────────────────────────────────────

def drain_events() -> str:
    """Read and clear all pending workspace events since the last heartbeat.

    Events are newline-delimited JSON objects appended by file watchers
    and git hooks between heartbeats.
    """
    if not os.path.exists(EVENT_QUEUE_PATH):
        return "No pending events."
    try:
        with open(EVENT_QUEUE_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        os.remove(EVENT_QUEUE_PATH)
        return content[:6000] or "No pending events."
    except OSError as e:
        return f"Error reading event queue: {e}"


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

    for root, dirs, files in os.walk(ws):
        # Skip common non-source directories
        dirs[:] = [
            d for d in dirs
            if d not in {"node_modules", ".git", "__pycache__", "venv", ".venv", "dist", "build"}
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
    return "\n".join(hits)


def list_recently_modified(
    count: Annotated[int, "Number of files to return"] = 15,
) -> str:
    """List workspace files ordered by modification time (most recent first)."""
    ws = Path(WORKSPACE)
    if not ws.is_dir():
        return f"Workspace directory not found: {WORKSPACE}"

    files: list[tuple[float, Path]] = []
    for root, dirs, fnames in os.walk(ws):
        dirs[:] = [
            d for d in dirs
            if d not in {"node_modules", ".git", "__pycache__", "venv", ".venv", "dist", "build"}
        ]
        for fname in fnames:
            fp = Path(root) / fname
            try:
                files.append((fp.stat().st_mtime, fp))
            except OSError:
                continue

    files.sort(reverse=True)
    lines = []
    for mtime, fp in files[: min(count, 50)]:
        from datetime import datetime
        ts = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        rel = fp.relative_to(ws)
        lines.append(f"{ts}  {rel}")
    return "\n".join(lines) or "(empty workspace)"
