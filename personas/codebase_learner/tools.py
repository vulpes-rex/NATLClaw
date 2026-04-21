"""Tools for the codebase_learner persona.

These complement the CodeNav MCP server (AST navigation) with local
operations: reading the git log, draining the file-change event queue,
and writing the CODEBASE_CONTEXT.md output file.
"""

from __future__ import annotations

import json
import os
import subprocess
import asyncio
from pathlib import Path
from typing import Annotated

import event_watcher

WORKSPACE = os.environ.get("NATL_WORKSPACE", ".")


# ──────────────────────────────────────────────────────────────────────
# Event queue
# ──────────────────────────────────────────────────────────────────────

def drain_events() -> str:
    """Read and clear all pending codebase events since the last heartbeat.

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
            ["git", "log", f"-{count}", "--oneline", "--stat"],
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


def analyse_current_git_commit() -> str:
    """Inspect the latest commit (same as ``read_git_log(1)``)."""
    return read_git_log(1)


def ingest() -> str:
    """Scheduler step ``ingest`` — use drain_events, read_git_log, codenav list_files/get_file_structure."""
    return (
        "For the ingest step, use drain_events and read_git_log, then codenav tools as listed in the prompt; "
        "write your bullet summary as the step output."
    )


def analyse() -> str:
    """Scheduler step ``analyse`` — use codenav get_symbols, get_call_graph, find_references; then JSON."""
    return (
        "For the analyse step, use codenav tools per the prompt, then return the JSON object required."
    )


def connect() -> str:
    """Scheduler step ``connect`` — link two notes using IDs from ``{brain}``."""
    return (
        "For the connect step, use note IDs from the brain summary; "
        'reply with JSON {"from","to","reason"}.'
    )


def read_git_diff(
    ref: Annotated[str, "Git ref to diff against (e.g. HEAD~1, main)"] = "HEAD~1",
) -> str:
    """Read a git diff against a reference to see what changed."""
    # Sanitise ref — block shell metacharacters
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


# ──────────────────────────────────────────────────────────────────────
# Context file output
# ──────────────────────────────────────────────────────────────────────

def write_context_file(
    content: Annotated[str, "Full markdown content for CODEBASE_CONTEXT.md"],
) -> str:
    """Write CODEBASE_CONTEXT.md to the workspace root for Copilot to consume.

    This file is auto-generated each heartbeat and contains the agent's
    current understanding of the codebase: architecture, patterns,
    conventions, and dependencies.
    """
    target = Path(WORKSPACE) / "CODEBASE_CONTEXT.md"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Written {len(content)} chars to {target}"
    except OSError as e:
        return f"Error writing context file: {e}"


def read_context_file() -> str:
    """Read the current CODEBASE_CONTEXT.md if it exists."""
    target = Path(WORKSPACE) / "CODEBASE_CONTEXT.md"
    if not target.exists():
        return "(no context file yet)"
    try:
        return target.read_text(encoding="utf-8")[:8000]
    except OSError as e:
        return f"Error reading context file: {e}"
