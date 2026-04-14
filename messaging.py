"""Outbox messaging system for the coworker interaction model.

Messages are notifications from the agent to the developer — status
updates, questions, alerts, and handoff summaries.  They persist in
``data/outbox.json`` (sibling to the state file) and are read via
``natl inbox``.

Message types::

    status   — task started, completed, failed, timed-out
    question — agent needs developer input (task blocked)
    alert    — proactive warning (brain maintenance, error spike)
    handoff  — task deliverables summary
    fyi      — informational (daily digest, brain insight)

Lifecycle::

    unread → read → dismissed

The scheduler appends messages during heartbeats.  The developer reads
them via ``natl inbox`` and optionally dismisses them.
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

OUTBOX_FILE = os.path.join("data", "outbox.json")

# ── Data model ─────────────────────────────────────────────────────────


@dataclass
class Message:
    """A notification from the agent to the developer."""

    id: str = ""
    type: str = "status"          # status | question | alert | handoff | fyi
    urgency: str = "normal"       # low | normal | high | urgent
    title: str = ""
    body: str = ""
    status: str = "unread"        # unread | read | dismissed
    requires_response: bool = False

    # Context linking
    task_id: str = ""             # related task (if any)
    persona: str = ""             # which persona generated this
    heartbeat: int = 0            # heartbeat number when created

    # Timestamps
    created_at: str = ""
    read_at: str | None = None
    dismissed_at: str | None = None

    # Structured payload (type-specific data)
    payload: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"m{uuid.uuid4().hex[:6]}"
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


# ── Urgency ordering ──────────────────────────────────────────────────

_URGENCY_RANK = {"urgent": 0, "high": 1, "normal": 2, "low": 3}


def _urgency_key(msg: Message) -> tuple[int, str]:
    """Sort key: higher urgency first, then oldest first."""
    return (_URGENCY_RANK.get(msg.urgency, 9), msg.created_at)


def _message_fingerprint(message: Message) -> tuple[str, str, str, str, str]:
    """Stable fingerprint for deduping semantically identical messages."""
    return (
        message.type,
        message.task_id,
        message.title.strip(),
        message.body.strip(),
        message.urgency,
    )


# ── Persistence ────────────────────────────────────────────────────────


def _outbox_path(state_file: str | None = None) -> str:
    """Resolve the outbox.json path next to the state file."""
    if state_file:
        return os.path.join(os.path.dirname(state_file), "outbox.json")
    return OUTBOX_FILE


async def load_outbox(state_file: str | None = None) -> list[Message]:
    """Load messages from disk.  Returns empty list if file missing."""
    path = _outbox_path(state_file)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, list):
            return []
        messages = []
        for entry in raw:
            # Filter to known Message fields for forward compatibility
            filtered = {
                k: v for k, v in entry.items()
                if k in Message.__dataclass_fields__
            }
            messages.append(Message(**filtered))
        return messages
    except (json.JSONDecodeError, OSError, TypeError) as e:
        logger.warning("Failed to load outbox from %s: %s", path, e)
        return []


async def save_outbox(
    messages: list[Message], state_file: str | None = None
) -> None:
    """Save messages atomically (write tmp then rename)."""
    path = _outbox_path(state_file)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    dir_name = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(
                [asdict(m) for m in messages],
                f, indent=2, ensure_ascii=False,
            )
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ── Message creation helpers ───────────────────────────────────────────


def create_message(
    msg_type: str,
    title: str,
    body: str = "",
    *,
    urgency: str = "normal",
    task_id: str = "",
    persona: str = "",
    heartbeat: int = 0,
    requires_response: bool = False,
    payload: dict | None = None,
) -> Message:
    """Create a new message.  Caller appends to outbox and saves."""
    return Message(
        type=msg_type,
        urgency=urgency,
        title=title,
        body=body,
        task_id=task_id,
        persona=persona,
        heartbeat=heartbeat,
        requires_response=requires_response,
        payload=payload or {},
    )


def append_message(
    messages: list[Message],
    message: Message,
    *,
    dedupe_statuses: tuple[str, ...] = ("unread", "read"),
) -> bool:
    """Append message unless an equivalent active message already exists.

    Returns True when appended, False when deduped.
    """
    fp = _message_fingerprint(message)
    for existing in messages:
        if existing.status in dedupe_statuses and _message_fingerprint(existing) == fp:
            return False
    messages.append(message)
    return True


def extend_messages(
    messages: list[Message],
    new_messages: list[Message],
    *,
    dedupe_statuses: tuple[str, ...] = ("unread", "read"),
) -> int:
    """Append multiple messages with dedupe. Returns number appended."""
    appended = 0
    for msg in new_messages:
        if append_message(messages, msg, dedupe_statuses=dedupe_statuses):
            appended += 1
    return appended


def emit_task_completed(
    task, *, persona: str = "", heartbeat: int = 0,
) -> Message:
    """Create a 'task completed' status message."""
    deliverables = ", ".join(task.deliverables[:5]) if task.deliverables else "(none)"
    return create_message(
        "handoff",
        title=f"Task completed: {task.title}",
        body=(
            f"Task {task.id} finished in {task.heartbeats_spent} heartbeat(s).\n"
            f"Deliverables: {deliverables}"
        ),
        urgency="normal",
        task_id=task.id,
        persona=persona,
        heartbeat=heartbeat,
        payload={
            "deliverables": task.deliverables[:20],
            "heartbeats_spent": task.heartbeats_spent,
            "severity": "normal",
        },
    )


def emit_task_blocked(
    task, question: str, *, persona: str = "", heartbeat: int = 0,
) -> Message:
    """Create a 'task blocked' question message."""
    return create_message(
        "question",
        title=f"Task blocked: {task.title}",
        body=f"Task {task.id} needs your input:\n\n{question}",
        urgency="high",
        task_id=task.id,
        persona=persona,
        heartbeat=heartbeat,
        requires_response=True,
        payload={"question": question, "severity": "high"},
    )


def emit_task_failed(
    task, reason: str = "", *, persona: str = "", heartbeat: int = 0,
) -> Message:
    """Create a 'task failed' alert message."""
    return create_message(
        "alert",
        title=f"Task failed: {task.title}",
        body=f"Task {task.id} failed after {task.heartbeats_spent} heartbeat(s).\n{reason}",
        urgency="high",
        task_id=task.id,
        persona=persona,
        heartbeat=heartbeat,
        payload={
            "reason": reason,
            "heartbeats_spent": task.heartbeats_spent,
            "severity": "high",
        },
    )


def emit_task_started(
    task, *, persona: str = "", heartbeat: int = 0,
) -> Message:
    """Create a 'task started' status message."""
    return create_message(
        "status",
        title=f"Task started: {task.title}",
        body=f"Task {task.id} assigned to {task.assigned_to or persona} (priority={task.priority}).",
        urgency="low",
        task_id=task.id,
        persona=persona,
        heartbeat=heartbeat,
        payload={"severity": "low"},
    )


def emit_task_timed_out(
    task, *, persona: str = "", heartbeat: int = 0,
) -> Message:
    """Create a 'task timed out' alert message."""
    return create_message(
        "alert",
        title=f"Task timed out: {task.title}",
        body=(
            f"Task {task.id} exceeded its limit of {task.max_heartbeats} heartbeats "
            f"and was automatically failed."
        ),
        urgency="high",
        task_id=task.id,
        persona=persona,
        heartbeat=heartbeat,
        payload={"max_heartbeats": task.max_heartbeats, "severity": "high"},
    )


def emit_alert(
    title: str,
    body: str = "",
    *,
    urgency: str = "normal",
    persona: str = "",
    heartbeat: int = 0,
    payload: dict | None = None,
) -> Message:
    """Create a general alert message (brain maintenance, error spike, etc.)."""
    return create_message(
        "alert",
        title=title,
        body=body,
        urgency=urgency,
        persona=persona,
        heartbeat=heartbeat,
        payload={**(payload or {}), "severity": urgency},
    )


def emit_fyi(
    title: str,
    body: str = "",
    *,
    persona: str = "",
    heartbeat: int = 0,
    payload: dict | None = None,
) -> Message:
    """Create an informational FYI message."""
    return create_message(
        "fyi",
        title=title,
        body=body,
        urgency="low",
        persona=persona,
        heartbeat=heartbeat,
        payload={**(payload or {}), "severity": "low"},
    )


# ── Query helpers ──────────────────────────────────────────────────────


def get_unread(messages: list[Message]) -> list[Message]:
    """Return unread messages sorted by urgency then time."""
    unread = [m for m in messages if m.status == "unread"]
    return sorted(unread, key=_urgency_key)


def get_by_type(messages: list[Message], msg_type: str) -> list[Message]:
    """Filter messages by type."""
    return [m for m in messages if m.type == msg_type]


def get_requiring_response(messages: list[Message]) -> list[Message]:
    """Return unread messages that need a developer response."""
    return [
        m for m in messages
        if m.status == "unread" and m.requires_response
    ]


def find_message(messages: list[Message], msg_id: str) -> Message | None:
    """Find a message by ID."""
    for m in messages:
        if m.id == msg_id:
            return m
    return None


# ── State transitions ──────────────────────────────────────────────────


def mark_read(message: Message) -> None:
    """Mark a message as read."""
    if message.status == "unread":
        message.status = "read"
        message.read_at = datetime.now(timezone.utc).isoformat()


def mark_dismissed(message: Message) -> None:
    """Dismiss a message (removes from active inbox view)."""
    message.status = "dismissed"
    message.dismissed_at = datetime.now(timezone.utc).isoformat()


def dismiss_all_read(messages: list[Message]) -> int:
    """Dismiss all read messages.  Returns count dismissed."""
    count = 0
    for m in messages:
        if m.status == "read":
            mark_dismissed(m)
            count += 1
    return count


def prune_old_messages(
    messages: list[Message], max_age_days: int = 7, max_count: int = 200,
) -> int:
    """Remove old dismissed messages to keep outbox small.  Returns count pruned."""
    cutoff = datetime.now(timezone.utc).isoformat()
    # Only prune dismissed messages
    prunable = [
        m for m in messages
        if m.status == "dismissed"
        and m.dismissed_at
        and m.dismissed_at < cutoff
    ]
    # Also prune if over max_count
    if len(messages) > max_count:
        overflow = len(messages) - max_count
        prunable = sorted(prunable, key=lambda m: m.created_at)[:max(overflow, len(prunable))]

    pruned_ids = {m.id for m in prunable}
    original_len = len(messages)
    messages[:] = [m for m in messages if m.id not in pruned_ids]
    return original_len - len(messages)


# ── Display helpers ────────────────────────────────────────────────────

_TYPE_ICON = {
    "status": " ",
    "question": "?",
    "alert": "!",
    "handoff": "->",
    "fyi": "i",
}

_URGENCY_BADGE = {
    "urgent": "!!!",
    "high": "!!",
    "normal": "",
    "low": "",
}


def format_inbox(messages: list[Message], *, show_read: bool = False) -> str:
    """Format messages for CLI inbox display."""
    filtered = messages
    if not show_read:
        filtered = [m for m in messages if m.status != "dismissed"]
    if not filtered:
        return "(no messages)"

    lines = []
    for m in sorted(filtered, key=_urgency_key):
        icon = _TYPE_ICON.get(m.type, "?")
        badge = _URGENCY_BADGE.get(m.urgency, "")
        read_marker = " " if m.status == "unread" else "."
        line = f"  {read_marker} [{icon}] {m.id}  {m.title}"
        if badge:
            line += f"  {badge}"
        if m.task_id:
            line += f"  (task:{m.task_id})"
        if m.requires_response and m.status == "unread":
            line += "  [needs response]"
        lines.append(line)
    return "\n".join(lines)


def format_message_detail(message: Message) -> str:
    """Format a single message for detailed display."""
    lines = [
        f"Message: {message.id}",
        f"Type: {message.type}",
        f"Urgency: {message.urgency}",
        f"Status: {message.status}",
        f"Title: {message.title}",
        f"Created: {message.created_at}",
    ]
    if message.read_at:
        lines.append(f"Read: {message.read_at}")
    if message.task_id:
        lines.append(f"Task: {message.task_id}")
    if message.persona:
        lines.append(f"Persona: {message.persona}")
    if message.requires_response:
        lines.append("Requires response: yes")
    lines.append("")
    lines.append(message.body or "(no body)")
    if message.payload:
        lines.append("")
        lines.append("Payload:")
        for k, v in message.payload.items():
            lines.append(f"  {k}: {v}")
    return "\n".join(lines)


def build_inbox_summary(messages: list[Message]) -> str:
    """Build a one-line summary for prompt injection or status display."""
    unread = get_unread(messages)
    if not unread:
        return ""
    questions = sum(1 for m in unread if m.requires_response)
    alerts = sum(1 for m in unread if m.type == "alert")
    parts = [f"{len(unread)} unread message(s)"]
    if questions:
        parts.append(f"{questions} need response")
    if alerts:
        parts.append(f"{alerts} alert(s)")
    return "INBOX: " + ", ".join(parts)
