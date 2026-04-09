"""Daily digest / morning briefing generator.

Aggregates recent workspace activity, brain state, and task status
into a concise summary.  Used by ``natl brief`` and auto-triggered
on the first heartbeat of a new day.

This module is engine-level infrastructure — it must remain
domain-agnostic.  All domain knowledge comes from the persona's brain.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TASKS_PATH = Path("data") / "tasks.json"
DIGEST_DIR = Path("data") / "digests"


# ──────────────────────────────────────────────────────────────────────
# Data gathering (all pure reads, no LLM calls)
# ──────────────────────────────────────────────────────────────────────

def _git_log_since(since_iso: str | None, max_count: int = 20) -> str:
    """Return git log entries since *since_iso* (ISO-8601 timestamp).

    Falls back to last 24 hours if *since_iso* is None.
    """
    args = ["git", "log", f"--max-count={max_count}", "--oneline", "--stat"]
    if since_iso:
        args.append(f"--since={since_iso}")
    try:
        result = subprocess.run(
            args, capture_output=True, cwd=".", timeout=10,
        )
        stdout = result.stdout.decode("utf-8", errors="replace")
        return stdout.strip()[:4000] or "(no commits)"
    except Exception:
        return "(git not available)"


def _load_tasks() -> list[dict[str, Any]]:
    """Load the task board (data/tasks.json)."""
    if not TASKS_PATH.exists():
        return []
    try:
        data = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _task_summary(tasks: list[dict[str, Any]]) -> str:
    """One-paragraph summary of task board status."""
    if not tasks:
        return "No tasks on the board."
    by_status: dict[str, int] = {}
    overdue: list[str] = []
    now = datetime.now(timezone.utc).date()
    for t in tasks:
        status = t.get("status", "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        due = t.get("due")
        if due and status not in ("done", "completed", "archived"):
            try:
                due_date = datetime.fromisoformat(due).date()
                if due_date < now:
                    overdue.append(t.get("title", f"task#{t.get('id', '?')}"))
            except ValueError:
                pass
    parts = [f"{v} {k}" for k, v in sorted(by_status.items())]
    line = "Tasks: " + ", ".join(parts) + "."
    if overdue:
        line += f"  OVERDUE: {', '.join(overdue[:5])}"
    return line


def _brain_summary_for_digest(brain: Any) -> str:
    """Compact brain state summary (no LLM needed)."""
    notes = getattr(brain, "notes", {})
    pages = getattr(brain, "pages", {})
    connections = getattr(brain, "connections", [])

    # Recent notes (last 5)
    recent = sorted(
        notes.values(),
        key=lambda n: n.get("created_at", ""),
        reverse=True,
    )[:5]

    lines = [f"Brain: {len(notes)} notes, {len(pages)} wiki pages, {len(connections)} connections."]
    if recent:
        lines.append("Recent notes:")
        for n in recent:
            content = n.get("content", n.get("summary", ""))[:80]
            tags = ", ".join(n.get("tags", [])[:3])
            lines.append(f"  - {content}  [{tags}]")
    return "\n".join(lines)


def _event_queue_summary() -> str:
    """Summarise pending events without draining them."""
    eq = Path("data") / "event_queue.json"
    if not eq.exists():
        return "Event queue: empty."
    try:
        content = eq.read_text(encoding="utf-8").strip()
        events = [l for l in content.splitlines() if l.strip()]
        if not events:
            return "Event queue: empty."
        # Count by type
        types: dict[str, int] = {}
        for line in events:
            try:
                evt = json.loads(line)
                t = evt.get("type", "unknown")
                types[t] = types.get(t, 0) + 1
            except json.JSONDecodeError:
                pass
        parts = [f"{v} {k}" for k, v in sorted(types.items())]
        return f"Event queue: {len(events)} events ({', '.join(parts)})."
    except OSError:
        return "Event queue: unreadable."


# ──────────────────────────────────────────────────────────────────────
# Digest builder
# ──────────────────────────────────────────────────────────────────────

def build_digest(
    brain: Any,
    last_heartbeat: str | None = None,
    *,
    persona_name: str = "",
) -> str:
    """Build a daily digest string.

    Args:
        brain: BrainState object.
        last_heartbeat: ISO-8601 timestamp of the last heartbeat (for git log).
        persona_name: Active persona name (for header).

    Returns:
        Formatted digest string for console output.
    """
    now = datetime.now(timezone.utc)
    header = f"=== Daily Brief — {now.strftime('%A, %B %d %Y')} ==="
    if persona_name:
        header += f"\nActive persona: {persona_name}"

    sections: list[str] = [header, ""]

    # 1. Activity since last session
    changes_log = _git_log_since(last_heartbeat)
    if changes_log and changes_log != "(no commits)" and changes_log != "(git not available)":
        sections.append("## Recent Changes")
        sections.append(changes_log)
        sections.append("")
    elif changes_log == "(git not available)":
        sections.append("(git not available for change history)")
        sections.append("")
    else:
        sections.append("No new commits since last session.")
        sections.append("")

    # 2. Task board
    tasks = _load_tasks()
    sections.append("## Tasks")
    sections.append(_task_summary(tasks))
    sections.append("")

    # 3. Event queue
    sections.append(_event_queue_summary())
    sections.append("")

    # 4. Brain state
    sections.append("## Brain")
    sections.append(_brain_summary_for_digest(brain))
    sections.append("")

    sections.append("=" * len(header.splitlines()[0]))
    return "\n".join(sections)


def save_digest(digest: str) -> Path:
    """Write digest to ``data/digests/YYYY-MM-DD.md``. Returns the path."""
    DIGEST_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = DIGEST_DIR / f"{today}.md"
    path.write_text(digest, encoding="utf-8")
    return path


# ──────────────────────────────────────────────────────────────────────
# First-run-of-day detection
# ──────────────────────────────────────────────────────────────────────

def is_first_run_today(last_heartbeat: str | None) -> bool:
    """Return True if *last_heartbeat* was on a different calendar day (UTC)."""
    if not last_heartbeat:
        return True
    try:
        last = datetime.fromisoformat(last_heartbeat)
        today = datetime.now(timezone.utc).date()
        return last.date() < today
    except (ValueError, TypeError):
        return True
