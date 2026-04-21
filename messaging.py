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
INBOX_FILE = os.path.join("data", "inbox.json")

# Cited brain note IDs (for inbox dismiss → relevance feedback). Also accepts legacy ``note_ids``.
BRAIN_NOTE_IDS_KEY = "brain_note_ids"

# ── Data model ─────────────────────────────────────────────────────────


@dataclass
class Message:
    """A notification or communication between agent, developer, and other agents.

    Direction convention
    --------------------
    outbound (agent → human):  ``sender="agent"`` (or persona name), ``addressed_to=""``
    inbound  (human → agent):  ``sender="developer"`` (or surface), ``addressed_to=<persona>``
    inter-agent:               ``sender=<persona>``, ``addressed_to=<persona>``

    Threading
    ---------
    ``thread_id`` groups related messages.  The root message sets ``thread_id`` to its own
    ``id``; all replies copy the root's ``thread_id``.  ``reply_to`` holds the specific
    message being replied to.
    """

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

    # Structured payload (type-specific data). May include ``brain_note_ids`` (list of note ids)
    # so dismissing the message can demote those notes via ``apply_relevance_feedback``.
    payload: dict = field(default_factory=dict)

    # Move A: bidirectional fields (all default to "" for backward compat with outbox.json)
    sender: str = "agent"         # "agent" | "developer" | persona-name | surface-name
    addressed_to: str = ""        # persona-name | "coordinator" | "" (broadcast to human)
    thread_id: str = ""           # groups related messages; root sets this to its own id
    reply_to: str = ""            # message ID this is a direct reply to

    # Move C: conversation protocol type
    conversation_type: str = ""   # clarification | three_amigos | handoff | escalation | broadcast | ""

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


def _message_fingerprint(message: Message) -> tuple[str, str, str, str, str, str]:
    """Stable fingerprint for deduping semantically identical messages."""
    return (
        message.type,
        message.task_id,
        message.title.strip(),
        message.body.strip(),
        message.urgency,
        message.sender,   # prevents human replies deduping against agent originals
    )


# ── Persistence ────────────────────────────────────────────────────────


def _outbox_path(state_file: str | None = None) -> str:
    """Resolve the outbox.json path next to the state file."""
    if state_file:
        return os.path.join(os.path.dirname(state_file), "outbox.json")
    return OUTBOX_FILE


def _inbox_path(state_file: str | None = None) -> str:
    """Resolve the inbox.json path next to the state file."""
    if state_file:
        return os.path.join(os.path.dirname(state_file), "inbox.json")
    return INBOX_FILE


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


async def load_inbox(state_file: str | None = None) -> list[Message]:
    """Load inbound messages from inbox.json.  Returns empty list if file missing."""
    path = _inbox_path(state_file)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, list):
            return []
        messages = []
        for entry in raw:
            filtered = {
                k: v for k, v in entry.items()
                if k in Message.__dataclass_fields__
            }
            messages.append(Message(**filtered))
        return messages
    except (json.JSONDecodeError, OSError, TypeError) as e:
        logger.warning("Failed to load inbox from %s: %s", path, e)
        return []


async def save_inbox(
    messages: list[Message], state_file: str | None = None
) -> None:
    """Save inbound messages atomically (write tmp then rename)."""
    path = _inbox_path(state_file)
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


async def append_and_save_inbox(
    message: Message, state_file: str | None = None
) -> bool:
    """Load inbox, append *message* (with dedup), and save.  Returns True if appended."""
    messages = await load_inbox(state_file)
    appended = append_message(messages, message)
    if appended:
        await save_inbox(messages, state_file)
    return appended


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


def _merge_payload_brain_note_ids(payload: dict, brain_note_ids: list[str] | None) -> dict:
    """Attach ``BRAIN_NOTE_IDS_KEY`` to a copy of *payload*, merging with any existing list."""
    if not brain_note_ids:
        return payload
    merged = dict(payload)
    cur = merged.get(BRAIN_NOTE_IDS_KEY)
    cleaned_new = [x.strip() for x in brain_note_ids if x and str(x).strip()]
    if not cleaned_new:
        return payload
    if isinstance(cur, list):
        combined = [str(x).strip() for x in cur if str(x).strip()] + cleaned_new
    else:
        combined = cleaned_new
    # Preserve order, dedupe
    seen: set[str] = set()
    deduped: list[str] = []
    for x in combined:
        if x in seen:
            continue
        seen.add(x)
        deduped.append(x)
    merged[BRAIN_NOTE_IDS_KEY] = deduped
    return merged


def merge_brain_note_ids(payload: dict | None, note_ids: list[str] | None) -> dict:
    """Merge *note_ids* into a copy of *payload* (for attaching citations to outbox messages)."""
    return _merge_payload_brain_note_ids(dict(payload or {}), note_ids)


def brain_note_ids_from_message(message: Message) -> list[str]:
    """Return cited brain note ids from ``payload`` (``brain_note_ids`` and legacy ``note_ids``)."""
    payload = message.payload or {}
    out: list[str] = []
    seen: set[str] = set()
    for key in (BRAIN_NOTE_IDS_KEY, "note_ids"):
        raw = payload.get(key)
        if raw is None:
            continue
        if isinstance(raw, str):
            candidates = [raw]
        elif isinstance(raw, list):
            candidates = [str(x) for x in raw]
        else:
            continue
        for x in candidates:
            x = x.strip()
            if not x or x in seen:
                continue
            seen.add(x)
            out.append(x)
    return out


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
    payload: dict = {
        "deliverables": task.deliverables[:20],
        "heartbeats_spent": task.heartbeats_spent,
        "severity": "normal",
    }
    note_refs: list[str] = []
    for d in task.deliverables[:40]:
        if isinstance(d, str) and d.startswith("note:"):
            nid = d.split(":", 1)[1].strip()
            if nid:
                note_refs.append(nid)
    payload = _merge_payload_brain_note_ids(payload, note_refs or None)
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
        payload=payload,
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


def emit_task_redirected(
    task, to_persona: str, reason: str = "", *, persona: str = "", heartbeat: int = 0,
) -> Message:
    """Create a 'task redirected' status message (Move B negotiation)."""
    body = f"Task {task.id} redirected to @{to_persona}."
    if reason:
        body += f" Reason: {reason[:300]}"
    return create_message(
        "status",
        title=f"Task redirected: {task.title} → @{to_persona}",
        body=body,
        urgency="normal",
        task_id=task.id,
        persona=persona,
        heartbeat=heartbeat,
        payload={"redirected_to": to_persona, "reason": reason},
    )


def emit_alert(
    title: str,
    body: str = "",
    *,
    urgency: str = "normal",
    persona: str = "",
    heartbeat: int = 0,
    payload: dict | None = None,
    brain_note_ids: list[str] | None = None,
) -> Message:
    """Create a general alert message (brain maintenance, error spike, etc.)."""
    pl = _merge_payload_brain_note_ids({**(payload or {}), "severity": urgency}, brain_note_ids)
    return create_message(
        "alert",
        title=title,
        body=body,
        urgency=urgency,
        persona=persona,
        heartbeat=heartbeat,
        payload=pl,
    )


def emit_escalation_alert(
    escalation_type: str,
    title: str,
    body: str = "",
    *,
    severity: str = "normal",
    persona: str = "",
    heartbeat: int = 0,
    payload: dict | None = None,
    brain_note_ids: list[str] | None = None,
) -> Message:
    """Create a deterministic escalation alert from observer/engine signals."""
    urgency = "high" if severity == "high" else "normal"
    pl = {
        **(payload or {}),
        "escalation_type": escalation_type,
        "severity": severity,
    }
    pl = _merge_payload_brain_note_ids(pl, brain_note_ids)
    return create_message(
        "alert",
        title=title,
        body=body,
        urgency=urgency,
        persona=persona,
        heartbeat=heartbeat,
        payload=pl,
    )


def emit_fyi(
    title: str,
    body: str = "",
    *,
    persona: str = "",
    heartbeat: int = 0,
    payload: dict | None = None,
    brain_note_ids: list[str] | None = None,
) -> Message:
    """Create an informational FYI message."""
    pl = _merge_payload_brain_note_ids({**(payload or {}), "severity": "low"}, brain_note_ids)
    return create_message(
        "fyi",
        title=title,
        body=body,
        urgency="low",
        persona=persona,
        heartbeat=heartbeat,
        payload=pl,
    )


def emit_handoff(
    task,
    handoff_context: "HandoffContext",
    *,
    addressed_to: str = "",
    sender: str = "",
    persona: str = "",
    heartbeat: int = 0,
) -> "Message":
    """Create an FYI message announcing a structured coordinator handoff (Move D).

    Posted when a coordinator delegates a task with an attached HandoffContext
    so the receiving persona (and developer inbox) can see the delegation details.
    Uses Move A routing fields: ``sender`` identifies the coordinator,
    ``addressed_to`` identifies the receiving persona.
    """
    summary = handoff_context.summary or f"Task {task.id} delegated to @{addressed_to}"
    body = handoff_context.to_prompt_block() or summary
    msg = create_message(
        "fyi",
        title=f"Handoff: {task.title} → @{addressed_to}",
        body=body,
        urgency="normal",
        task_id=task.id,
        persona=persona,
        heartbeat=heartbeat,
        payload=handoff_context.to_dict(),
    )
    msg.sender = sender or "coordinator"
    msg.addressed_to = addressed_to
    msg.thread_id = msg.id  # Each handoff starts a new thread
    return msg


def emit_inbound_message(
    body: str,
    *,
    sender: str = "developer",
    addressed_to: str = "",
    title: str = "",
    urgency: str = "normal",
    reply_to: str = "",
    thread_id: str = "",
    task_id: str = "",
    payload: dict | None = None,
) -> Message:
    """Create an inbound message from a developer or external agent to a persona.

    Sets ``sender`` on the returned message and auto-populates ``thread_id``
    (new thread when omitted; continuing thread when supplied).
    """
    msg = create_message(
        "fyi",
        title=title or body[:80],
        body=body,
        urgency=urgency,
        task_id=task_id,
        payload=dict(payload or {}),
    )
    msg.sender = sender
    msg.addressed_to = addressed_to
    msg.reply_to = reply_to
    # Root message: thread_id = own id.  Reply: inherit supplied thread_id.
    msg.thread_id = thread_id if thread_id else msg.id
    return msg


# ── Move C: reply + three-amigos helpers ──────────────────────────────


def create_reply(
    original: Message,
    body: str,
    *,
    sender: str = "developer",
    msg_type: str = "fyi",
    urgency: str = "normal",
    conversation_type: str = "",
    payload: dict | None = None,
) -> Message:
    """Create a reply message inheriting the original's thread.

    - ``thread_id`` is copied from the original (continuing the thread).
    - ``reply_to`` is set to ``original.id``.
    - ``addressed_to`` is set to ``original.sender`` (reply to whoever sent it).
    """
    msg = create_message(
        msg_type,
        title=f"Re: {original.title}"[:120],
        body=body,
        urgency=urgency,
        task_id=original.task_id,
        payload=dict(payload or {}),
    )
    msg.sender = sender
    msg.addressed_to = original.sender  # reply to the original sender
    msg.thread_id = original.thread_id if original.thread_id else original.id
    msg.reply_to = original.id
    msg.conversation_type = conversation_type
    return msg


def emit_three_amigos(
    task: object,
    question: str,
    *,
    persona: str = "",
    heartbeat: int = 0,
) -> "Message":
    """Create a three-amigos alignment request for a task.

    The three-amigos pattern signals that a task needs PM + Dev + QA
    alignment before implementation begins.  The task is blocked until
    the developer answers via ``natl reply <msg_id>`` or ``natl task answer``.
    """
    msg = create_message(
        "question",
        title=f"Three amigos needed: {getattr(task, 'title', str(task))}",
        body=(
            f"Task '{getattr(task, 'title', '')}' needs PM + Dev + QA alignment "
            f"before implementation begins.\n\nOpen question:\n{question}"
        ),
        urgency="high",
        task_id=getattr(task, "id", ""),
        persona=persona,
        heartbeat=heartbeat,
        requires_response=True,
        payload={
            "question": question,
            "pattern": "three_amigos",
        },
    )
    msg.conversation_type = "three_amigos"
    msg.sender = persona or "agent"
    msg.thread_id = msg.id  # starts a new thread
    return msg


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


def get_inbound(messages: list[Message], addressed_to: str = "") -> list[Message]:
    """Return unread inbound messages, optionally filtered to a specific persona.

    A message is inbound when its ``sender`` is not ``"agent"``.  When
    *addressed_to* is given, broadcast messages (``addressed_to=""``) are
    included alongside those explicitly addressed to the persona.
    """
    inbound = [m for m in messages if m.sender != "agent" and m.status == "unread"]
    if addressed_to:
        inbound = [m for m in inbound if m.addressed_to in ("", addressed_to)]
    return sorted(inbound, key=_urgency_key)


def get_thread(messages: list[Message], thread_id: str) -> list[Message]:
    """Return all messages sharing *thread_id*, ordered by creation time."""
    return sorted(
        [m for m in messages if m.thread_id == thread_id],
        key=lambda m: m.created_at,
    )


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
        read_marker = "*" if m.status == "unread" else "."
        direction = "<-" if m.sender == "agent" else "->"
        line = f"  {read_marker} {direction} [{icon}] {m.id}  {m.title}"
        if badge:
            line += f"  {badge}"
        if m.task_id:
            line += f"  (task:{m.task_id})"
        if m.addressed_to:
            line += f"  @{m.addressed_to}"
        if m.requires_response and m.status == "unread":
            line += "  [needs response]"
        if m.reply_to:
            line += f"  re:{m.reply_to}"
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


def build_inbound_message_block(
    messages: list[Message],
    persona_name: str = "",
    max_messages: int = 5,
) -> str:
    """Format unread inbound messages for injection into a heartbeat prompt.

    Returns an empty string when there are no relevant inbound messages so
    callers can conditionally append it without extra branching.
    """
    pending = get_inbound(messages, addressed_to=persona_name)[:max_messages]
    if not pending:
        return ""
    lines = ["== MESSAGES FOR YOU =="]
    for m in pending:
        sender_label = m.sender or "developer"
        reply_hint = f" (re: {m.reply_to})" if m.reply_to else ""
        lines.append(f"[{m.id}]{reply_hint} From {sender_label}: {m.title}")
        if m.body and m.body.strip() != m.title.strip():
            for body_line in m.body.splitlines()[:6]:
                lines.append(f"  {body_line}")
    lines.append(
        "\nIf you want to reply, write: REPLY TO <msg_id>: your reply text"
    )
    return "\n".join(lines)
