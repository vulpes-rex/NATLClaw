"""Task queue for the coworker interaction model.

Tasks are units of work assigned to the agent by the developer.
They persist in ``data/tasks.json`` and are checked every heartbeat —
the scheduler runs task work before background knowledge cycles.

Lifecycle::

    pending → assigned → in_progress → completed | blocked | failed

The developer creates tasks via ``natl task add``.  The scheduler
picks up pending tasks, assigns them to the active persona, and runs
``run_task_heartbeat()`` cycles until the task is done or blocked.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

TASKS_FILE = os.path.join("data", "tasks.json")
TERMINAL_TASK_STATUSES = frozenset({"completed", "failed"})


class TaskTransitionError(ValueError):
    """Raised when a task lifecycle transition is invalid."""


def _require_status(task: "Task", *, allowed: tuple[str, ...], action: str) -> None:
    """Validate task status for a transition."""
    if task.status not in allowed:
        allowed_str = ", ".join(allowed)
        raise TaskTransitionError(
            f"Cannot {action} task {task.id} from status={task.status}; "
            f"allowed statuses: {allowed_str}"
        )

# ── Data model ─────────────────────────────────────────────────────────


@dataclass
class Task:
    """A unit of work assigned to the agent."""

    id: str = ""
    title: str = ""
    description: str = ""
    priority: str = "medium"  # low | medium | high | urgent
    status: str = "pending"   # pending | assigned | in_progress
                              # | blocked | completed | failed
    assigned_to: str = ""     # persona name
    created_by: str = "developer"
    created_at: str = ""
    started_at: str | None = None
    completed_at: str | None = None

    # Work tracking
    heartbeats_spent: int = 0
    max_heartbeats: int = 10
    progress_notes: list[str] = field(default_factory=list)
    deliverables: list[str] = field(default_factory=list)

    # Communication
    questions: list[dict] = field(default_factory=list)
    answers: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"t{uuid.uuid4().hex[:6]}"
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


# ── Priority ordering ──────────────────────────────────────────────────

_PRIORITY_RANK = {"urgent": 0, "high": 1, "medium": 2, "low": 3}


def _task_age_hours(task: Task) -> float:
    """Best-effort task age in hours (0 when timestamp is missing/invalid)."""
    created = (task.created_at or "").strip()
    if not created:
        return 0.0
    # Support legacy "Z" timestamps as UTC.
    created = created.replace("Z", "+00:00")
    try:
        created_dt = datetime.fromisoformat(created)
    except (ValueError, TypeError):
        return 0.0
    if created_dt.tzinfo is None:
        created_dt = created_dt.replace(tzinfo=timezone.utc)
    age_sec = (datetime.now(timezone.utc) - created_dt).total_seconds()
    return max(0.0, age_sec / 3600.0)


def _effective_priority_rank(task: Task) -> int:
    """Priority rank with deterministic anti-starvation age promotion."""
    base_rank = _PRIORITY_RANK.get(task.priority, 9)
    age_h = _task_age_hours(task)
    # Promote old tasks to avoid indefinite starvation under mixed loads.
    # 6h: +1 level, 24h: +2 levels, 72h: +3 levels (can reach urgent tier).
    promotion = 0
    if age_h >= 72:
        promotion = 3
    elif age_h >= 24:
        promotion = 2
    elif age_h >= 6:
        promotion = 1
    return max(0, base_rank - promotion)


def _priority_key(task: Task) -> tuple[int, str, int]:
    """Sort key: effective rank, oldest first, then base rank."""
    return (
        _effective_priority_rank(task),
        task.created_at,
        _PRIORITY_RANK.get(task.priority, 9),
    )


# ── Persistence ────────────────────────────────────────────────────────


def _tasks_path(state_file: str | None = None) -> str:
    """Resolve the tasks.json path next to the state file."""
    if state_file:
        return os.path.join(os.path.dirname(state_file), "tasks.json")
    return TASKS_FILE


async def load_tasks(state_file: str | None = None) -> list[Task]:
    """Load tasks from disk. Returns empty list if file missing."""
    path = _tasks_path(state_file)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, list):
            return []
        tasks = []
        for entry in raw:
            # Filter to known Task fields for forward compatibility
            filtered = {
                k: v for k, v in entry.items()
                if k in Task.__dataclass_fields__
            }
            tasks.append(Task(**filtered))
        return tasks
    except (json.JSONDecodeError, OSError, TypeError) as e:
        logger.warning("Failed to load tasks from %s: %s", path, e)
        return []


async def save_tasks(
    tasks: list[Task], state_file: str | None = None
) -> None:
    """Save tasks atomically (write tmp then rename)."""
    path = _tasks_path(state_file)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    dir_name = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump([asdict(t) for t in tasks], f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ── Task operations ────────────────────────────────────────────────────


def create_task(
    title: str,
    description: str = "",
    priority: str = "medium",
    max_heartbeats: int = 10,
) -> Task:
    """Create a new task. Caller must append to the task list and save."""
    return Task(
        title=title,
        description=description or title,
        priority=priority,
        max_heartbeats=max_heartbeats,
    )


def get_pending_tasks(tasks: list[Task]) -> list[Task]:
    """Return pending tasks sorted by priority (highest first)."""
    pending = [t for t in tasks if t.status == "pending"]
    return sorted(pending, key=_priority_key)


def get_active_task(tasks: list[Task], persona_name: str = "") -> Task | None:
    """Return the in-progress or assigned task for this persona (if any)."""
    for t in tasks:
        if t.status in ("in_progress", "assigned"):
            if not persona_name or t.assigned_to == persona_name:
                return t
    return None


def get_blocked_tasks(tasks: list[Task]) -> list[Task]:
    """Return tasks that are blocked and waiting for developer answers."""
    return [t for t in tasks if t.status == "blocked"]


def find_task(tasks: list[Task], task_id: str) -> Task | None:
    """Find a task by ID."""
    for t in tasks:
        if t.id == task_id:
            return t
    return None


def assign_task(task: Task, persona_name: str) -> None:
    """Assign a pending task to a persona."""
    _require_status(task, allowed=("pending",), action="assign")
    task.status = "assigned"
    task.assigned_to = persona_name
    task.started_at = datetime.now(timezone.utc).isoformat()


def start_task(task: Task) -> None:
    """Move an assigned task to in_progress."""
    _require_status(task, allowed=("assigned", "in_progress"), action="start")
    task.status = "in_progress"
    if not task.started_at:
        task.started_at = datetime.now(timezone.utc).isoformat()


def advance_task(task: Task, note: str) -> None:
    """Record one heartbeat of progress on a task."""
    _require_status(task, allowed=("in_progress",), action="advance")
    task.heartbeats_spent += 1
    if note:
        task.progress_notes.append(note[:500])


def block_task(task: Task, question: str, heartbeat_number: int = 0) -> None:
    """Mark a task as blocked with a question for the developer."""
    _require_status(task, allowed=("in_progress",), action="block")
    task.status = "blocked"
    task.questions.append({
        "question": question,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "heartbeat": heartbeat_number,
    })


def answer_task(task: Task, answer: str) -> None:
    """Provide a developer answer to unblock a task."""
    _require_status(task, allowed=("blocked",), action="answer")
    task.answers.append({
        "answer": answer,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    # Unblock — scheduler will pick it up next cycle
    task.status = "assigned"


def complete_task(task: Task, deliverables: list[str] | None = None) -> None:
    """Mark a task as completed."""
    _require_status(task, allowed=("in_progress",), action="complete")
    task.status = "completed"
    task.completed_at = datetime.now(timezone.utc).isoformat()
    if deliverables:
        task.deliverables.extend(deliverables)


def fail_task(task: Task, reason: str = "") -> None:
    """Mark a task as failed."""
    if task.status in TERMINAL_TASK_STATUSES:
        raise TaskTransitionError(
            f"Cannot fail task {task.id} from status={task.status}; task is already terminal"
        )
    task.status = "failed"
    task.completed_at = datetime.now(timezone.utc).isoformat()
    if reason:
        task.progress_notes.append(f"FAILED: {reason[:500]}")


def cancel_task(task: Task, reason: str = "") -> None:
    """Cancel a task that is not yet completed or failed."""
    if task.status in TERMINAL_TASK_STATUSES:
        raise TaskTransitionError(
            f"Cannot cancel task {task.id} from status={task.status}; task is already terminal"
        )
    task.status = "failed"
    task.completed_at = datetime.now(timezone.utc).isoformat()
    label = f"CANCELLED: {reason[:500]}" if reason else "CANCELLED by developer"
    task.progress_notes.append(label)


def retry_task(task: Task) -> None:
    """Reset a failed or blocked task so it can be picked up again.

    Clears heartbeat count and resets status to pending so the scheduler
    will assign it on the next cycle.
    """
    _require_status(task, allowed=("failed", "blocked"), action="retry")
    task.status = "pending"
    task.assigned_to = ""
    task.started_at = None
    task.completed_at = None
    task.heartbeats_spent = 0
    task.progress_notes.append("RETRIED by developer")


def auto_timeout_tasks(tasks: list[Task]) -> list[str]:
    """Fail tasks that have exceeded max_heartbeats. Returns list of timed-out IDs."""
    timed_out = []
    for t in tasks:
        if t.status in ("in_progress", "assigned") and t.heartbeats_spent >= t.max_heartbeats:
            fail_task(t, f"Timed out after {t.heartbeats_spent} heartbeats")
            timed_out.append(t.id)
    return timed_out


# ── Display helpers ────────────────────────────────────────────────────


def format_task_list(tasks: list[Task], status_filter: str = "all") -> str:
    """Format tasks for CLI display."""
    filtered = tasks
    if status_filter != "all":
        filtered = [t for t in tasks if t.status == status_filter]
    if not filtered:
        return "(no tasks)"

    lines = []
    for t in filtered:
        icon = {
            "pending": " ",
            "assigned": "→",
            "in_progress": "▶",
            "blocked": "⏸",
            "completed": "✓",
            "failed": "✗",
        }.get(t.status, "?")
        prio = {"urgent": "!!!", "high": "!!", "medium": "!", "low": ""}.get(t.priority, "")
        line = f"  {icon} [{t.id}] {t.title}"
        if prio:
            line += f"  {prio}"
        if t.status == "in_progress":
            line += f"  ({t.heartbeats_spent}/{t.max_heartbeats} heartbeats)"
        if t.status == "blocked" and t.questions:
            last_q = t.questions[-1].get("question", "")[:60]
            line += f"  Q: {last_q}"
        if t.assigned_to:
            line += f"  @{t.assigned_to}"
        lines.append(line)
    return "\n".join(lines)


def format_task_detail(task: Task) -> str:
    """Format a single task for detailed CLI display."""
    lines = [
        f"Task: {task.id}",
        f"Title: {task.title}",
        f"Description: {task.description}",
        f"Status: {task.status}",
        f"Priority: {task.priority}",
        f"Assigned to: {task.assigned_to or '(unassigned)'}",
        f"Created: {task.created_at}",
        f"Started: {task.started_at or '-'}",
        f"Completed: {task.completed_at or '-'}",
        f"Progress: {task.heartbeats_spent}/{task.max_heartbeats} heartbeats",
    ]
    if task.progress_notes:
        lines.append(f"\nProgress notes ({len(task.progress_notes)}):")
        for note in task.progress_notes[-5:]:
            lines.append(f"  - {note[:200]}")
    if task.deliverables:
        lines.append(f"\nDeliverables:")
        for d in task.deliverables:
            lines.append(f"  - {d}")
    if task.questions:
        lines.append(f"\nQuestions ({len(task.questions)}):")
        for q in task.questions[-3:]:
            lines.append(f"  Q: {q.get('question', '')[:200]}")
    if task.answers:
        lines.append(f"\nAnswers ({len(task.answers)}):")
        for a in task.answers[-3:]:
            lines.append(f"  A: {a.get('answer', '')[:200]}")
    return "\n".join(lines)


def build_task_context(task: Task) -> str:
    """Build a context string about the current task for prompt injection."""
    lines = [
        "== ACTIVE TASK ==",
        f"ID: {task.id}",
        f"Title: {task.title}",
        f"Description: {task.description}",
        f"Priority: {task.priority}",
        f"Heartbeat: {task.heartbeats_spent + 1}/{task.max_heartbeats}",
    ]
    if task.progress_notes:
        lines.append(f"\nProgress so far:")
        for note in task.progress_notes[-3:]:
            lines.append(f"  - {note[:200]}")
    if task.answers:
        lines.append(f"\nDeveloper answers:")
        for a in task.answers[-3:]:
            lines.append(f"  - {a.get('answer', '')[:200]}")
    return "\n".join(lines)
