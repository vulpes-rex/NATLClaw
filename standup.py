"""Standup protocol — per-persona daily standup entries and team report.

At standup time (default 9:00 AM local), each active persona generates a
``StandupEntry`` from its current task state and recent brain notes.  The
entries are collected into a formatted team report, saved to disk, and posted
to the inbox so the developer can read it without touching the CLI.

Standup data sources
--------------------
- ``yesterday`` — tasks completed in the last 24 h
- ``today``     — tasks currently in_progress or assigned
- ``blockers``  — tasks in blocked state
- ``three_amigos`` — pending tasks with unanswered questions (flag for
                     PM + Dev + QA alignment before work starts)
- ``background_work`` — recent brain notes (when no tasks were active
                        yesterday, the persona reports knowledge work instead)
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from second_brain import BrainState
    from tasks import Task

logger = logging.getLogger(__name__)

STANDUP_DIR = Path("data") / "standup"


# ── Data models ────────────────────────────────────────────────────────


@dataclass
class ThreeAmigosFlag:
    """A pending task that needs PM + Dev + QA alignment before dev starts."""

    task_id: str
    title: str
    ado_ref: str = ""      # "ADO #4851" when ado_id is set, else ""
    question: str = ""     # First unanswered question text, if any


@dataclass
class StandupEntry:
    """A single persona's standup entry for one day."""

    persona: str
    date: str                                       # "YYYY-MM-DD"
    generated_at: str = ""                          # ISO-8601 timestamp

    # Core sections
    yesterday: list[str] = field(default_factory=list)
    today: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    three_amigos: list[dict] = field(default_factory=list)  # ThreeAmigosFlag as dict

    # Fallback when no tasks were active yesterday
    background_work: list[str] = field(default_factory=list)


@dataclass
class StandupReport:
    """The full team standup for one day."""

    date: str
    generated_at: str
    entries: list[dict] = field(default_factory=list)   # StandupEntry as dict
    formatted: str = ""                                  # Human-readable text


# ── Generation helpers ─────────────────────────────────────────────────


def _ado_ref(task: "Task") -> str:
    """Return 'ADO #NNNN' when the task is linked to ADO, else ''."""
    ado_id = getattr(task, "ado_id", 0)
    return f"ADO #{ado_id}" if ado_id else ""


def _task_label(task: "Task") -> str:
    """Short label for a task: 'ADO #4821 — Title' or just 'Title'."""
    ref = _ado_ref(task)
    return f"{ref} - {task.title}" if ref else task.title


def _completed_since(task: "Task", since: datetime) -> bool:
    """True if the task was completed after *since*."""
    ts = getattr(task, "completed_at", None)
    if not ts:
        return False
    try:
        ts = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= since
    except (ValueError, TypeError):
        return False


def _unanswered_questions(task: "Task") -> list[str]:
    """Return texts of questions that haven't been answered yet."""
    answered_ids: set[str] = {
        a.get("question_id", "") for a in getattr(task, "answers", [])
    }
    return [
        q.get("text", "").strip()
        for q in getattr(task, "questions", [])
        if q.get("id", "") not in answered_ids and q.get("text", "").strip()
    ]


# ── Core generation ────────────────────────────────────────────────────


def generate_standup(
    persona_name: str,
    tasks: "list[Task]",
    brain: "BrainState",
    *,
    now: datetime | None = None,
) -> StandupEntry:
    """Build a ``StandupEntry`` from the persona's current task + brain state.

    Parameters
    ----------
    persona_name:
        The persona this entry is for (e.g. ``"workspace_observer"``).
    tasks:
        All tasks (will be filtered to those relevant to this persona).
    brain:
        The persona's second brain (used for background knowledge work).
    now:
        Override "now" for deterministic testing.  Defaults to UTC now.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    yesterday_cutoff = now - timedelta(hours=24)
    date_str = now.strftime("%Y-%m-%d")

    # Filter to tasks belonging to this persona (or unassigned)
    my_tasks = [
        t for t in tasks
        if getattr(t, "assigned_to", "") in ("", persona_name)
        or getattr(t, "target_persona", "") == persona_name
    ]

    # ── Yesterday ──────────────────────────────────────────────────────
    yesterday_items: list[str] = []
    for t in my_tasks:
        if t.status == "completed" and _completed_since(t, yesterday_cutoff):
            label = _task_label(t)
            note = f"Completed {label}"
            # Attach first deliverable as context if available
            deliverables = getattr(t, "deliverables", [])
            if deliverables:
                note += f" ({deliverables[-1][:80]})"
            yesterday_items.append(note)

    # ── Today ──────────────────────────────────────────────────────────
    today_items: list[str] = []
    for t in my_tasks:
        if t.status in ("in_progress", "assigned"):
            label = _task_label(t)
            today_items.append(f"Working on {label}")

    # ── Blockers ───────────────────────────────────────────────────────
    blocker_items: list[str] = []
    for t in my_tasks:
        if t.status == "blocked":
            label = _task_label(t)
            # Surface the first unanswered question as the block reason
            questions = _unanswered_questions(t)
            reason = questions[0][:120] if questions else "Awaiting input"
            blocker_items.append(f"{label}: {reason}")

    # ── Three amigos ───────────────────────────────────────────────────
    # Flag tasks that are still pending and have open questions — they need
    # PM + Dev + QA alignment before implementation begins.
    three_amigos: list[dict] = []
    for t in my_tasks:
        if t.status == "pending":
            questions = _unanswered_questions(t)
            if questions:
                flag = ThreeAmigosFlag(
                    task_id=t.id,
                    title=t.title,
                    ado_ref=_ado_ref(t),
                    question=questions[0][:200],
                )
                three_amigos.append(asdict(flag))

    # ── Background knowledge work (fallback) ───────────────────────────
    background: list[str] = []
    if not yesterday_items and not today_items:
        # No active tasks — report recent brain notes instead
        try:
            from second_brain import get_recent_notes
            recent = get_recent_notes(brain, count=5)
            for note in recent:
                title = note.get("title") or note.get("content", "")[:60]
                if title:
                    background.append(f"Reviewed: {title}")
        except Exception:
            pass
        if not background:
            background.append("Background knowledge consolidation")

    return StandupEntry(
        persona=persona_name,
        date=date_str,
        generated_at=now.isoformat(),
        yesterday=yesterday_items,
        today=today_items,
        blockers=blocker_items,
        three_amigos=three_amigos,
        background_work=background,
    )


# ── Formatting ─────────────────────────────────────────────────────────


def format_entry(entry: StandupEntry) -> str:
    """Format a single persona entry as a standup-style block."""
    lines: list[str] = [f"[persona: {entry.persona}]"]

    def _section(label: str, items: list[str], fallback: str = "None") -> None:
        if not items:
            lines.append(f"{label:<11}{fallback}")
            return
        first, *rest = items
        lines.append(f"{label:<11}{first}")
        for item in rest:
            lines.append(f"{'':11}{item}")

    _section("Yesterday:  ", entry.yesterday or entry.background_work, "No active tasks")
    _section("Today:      ", entry.today, "No pending work")
    _section("Blockers:   ", entry.blockers, "None")

    if entry.three_amigos:
        for flag in entry.three_amigos:
            ref = flag.get("ado_ref") or flag.get("task_id", "")
            title = flag.get("title", "")
            question = flag.get("question", "")
            label = f"{ref} ({title})" if ref else title
            lines.append(f"{'Three amigos:':<11}{label}")
            if question:
                lines.append(f"{'':11}  -> {question}")

    return "\n".join(lines)


def format_report(entries: list[StandupEntry]) -> str:
    """Format all persona entries into a full team standup report."""
    if not entries:
        return "(No standup entries generated.)"

    date = entries[0].date if entries else ""
    header = f"=== Daily Standup — {date} ===\n"
    blocks = "\n\n".join(format_entry(e) for e in entries)
    return header + "\n" + blocks


# ── Persistence ────────────────────────────────────────────────────────


def _standup_path(date: str) -> Path:
    return STANDUP_DIR / f"{date}.json"


def save_standup(report: StandupReport) -> Path:
    """Persist a ``StandupReport`` to ``data/standup/YYYY-MM-DD.json``."""
    STANDUP_DIR.mkdir(parents=True, exist_ok=True)
    path = _standup_path(report.date)
    path.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    logger.info("[standup] Saved to %s", path)
    return path


def load_standup(date: str) -> StandupReport | None:
    """Load the ``StandupReport`` for *date* (``"YYYY-MM-DD"``).

    Returns ``None`` when no standup exists for that date.
    """
    path = _standup_path(date)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = [
            StandupEntry(**{k: v for k, v in e.items() if k in StandupEntry.__dataclass_fields__})
            for e in data.get("entries", [])
        ]
        return StandupReport(
            date=data.get("date", date),
            generated_at=data.get("generated_at", ""),
            entries=data.get("entries", []),
            formatted=data.get("formatted", ""),
        )
    except Exception as exc:
        logger.warning("[standup] Failed to load %s: %s", path, exc)
        return None


def load_standup_today() -> StandupReport | None:
    """Load today's standup report, if it exists."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return load_standup(today)


# ── Scheduler hook ─────────────────────────────────────────────────────


def is_standup_time(
    last_standup_at: str | None,
    *,
    standup_hour: int = 9,
    now: datetime | None = None,
) -> bool:
    """Return True when standup should run now.

    Standup fires once per day at or after *standup_hour* (local time).
    If *last_standup_at* is already today's date, it's already been run.

    Parameters
    ----------
    last_standup_at:
        ISO-8601 timestamp of the last standup generation (or None).
    standup_hour:
        Hour of day (local time) at which standup should fire.
    now:
        Override current time for testing.
    """
    if now is None:
        now = datetime.now()  # local time intentionally

    if now.hour < standup_hour:
        return False

    if not last_standup_at:
        return True

    try:
        last = last_standup_at[:10]  # "YYYY-MM-DD"
        today = now.strftime("%Y-%m-%d")
        return last != today
    except Exception:
        return True


# ── Convenience: build + save report from running system ───────────────


def run_standup(
    persona_name: str,
    tasks: "list[Task]",
    brain: "BrainState",
    *,
    now: datetime | None = None,
) -> StandupReport:
    """Generate and save a standup report for *persona_name*.

    This is the one-shot entry point used by the scheduler and the CLI.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    entry = generate_standup(persona_name, tasks, brain, now=now)
    formatted = format_report([entry])

    report = StandupReport(
        date=entry.date,
        generated_at=entry.generated_at,
        entries=[asdict(entry)],
        formatted=formatted,
    )
    save_standup(report)
    return report


def run_team_standup(
    persona_names: list[str],
    tasks: "list[Task]",
    brain: "BrainState",
    *,
    now: datetime | None = None,
) -> StandupReport:
    """Generate and save a multi-persona standup report.

    Each persona in *persona_names* gets its own entry, filtered to its tasks.
    The result is one consolidated ``StandupReport`` saved to disk.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    entries: list[StandupEntry] = [
        generate_standup(name, tasks, brain, now=now) for name in persona_names
    ]
    formatted = format_report(entries)
    date_str = entries[0].date if entries else now.strftime("%Y-%m-%d")

    report = StandupReport(
        date=date_str,
        generated_at=now.isoformat(),
        entries=[asdict(e) for e in entries],
        formatted=formatted,
    )
    save_standup(report)
    return report
