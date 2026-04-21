# Move A — Bidirectional Inbox

*Plan written 2026-04-15. Implements the foundation for mature coworker interaction.*

---

## Problem

All communication today is agent→human (outbox). The only human→agent path is `answer_task()`, which requires a blocked task as a prerequisite. This means:

- A human cannot send a contextual message to the agent without framing it as a task
- Agents cannot message each other except by creating tasks
- There is no concept of a conversation thread
- The agent cannot see its own outbox — it has no awareness of what it has communicated

---

## Goal

Make messages flow both directions through the same `Message` dataclass and `outbox.json` store, with four new fields and a new `inbox.json` file for inbound messages.

After Move A:

```
Human / External agent
        │
        │  natl msg send / POST /api/messages
        ▼
    inbox.json  ──────── scheduler reads at heartbeat ──► injected into prompt
                                                               │
                                                          agent responds
                                                               │
                                                               ▼
                                                          outbox.json  (reply)
                                                               │
                                                        natl inbox / GET /api/inbox
                                                               │
                                                          Human reads
```

---

## Scope (what Move A does and does not include)

**In scope:**
- Extend `Message` with `sender`, `addressed_to`, `thread_id`, `reply_to` fields
- Separate `inbox.json` for inbound messages (human/agent→agent)
- `POST /api/messages` and `natl msg send` for creating inbound messages
- Scheduler prompt injection of unread inbound messages per persona
- Reply extraction from heartbeat output — agent can write a reply block that becomes an outbound message
- Updated CLI `natl inbox` to show direction

**Out of scope (Move B and later):**
- Task negotiation states (accept/reject/redirect)
- Structured handoff context payloads
- Push notification hooks
- Threading UI in the web dashboard

---

## A1 — Extend the `Message` dataclass

**File:** [messaging.py](../messaging.py)

Add four fields at the end of `Message`, all with safe defaults so existing `outbox.json` files load without migration:

```python
@dataclass
class Message:
    # ... existing fields unchanged ...

    # Move A: bidirectional fields
    sender: str = "agent"          # "agent" | "developer" | persona-name | surface-name
    addressed_to: str = ""         # persona-name | "coordinator" | "" (broadcast to human)
    thread_id: str = ""            # groups related messages (share ID or msg ID of root)
    reply_to: str = ""             # message ID this is a reply to
```

**Convention:**
- `sender="agent"`, `addressed_to=""` → outbound (current default, no change)
- `sender="developer"`, `addressed_to=<persona>` → inbound from human to agent
- `sender=<persona-name>`, `addressed_to=<persona-name>` → inter-agent
- `thread_id` is set to the root message's ID on all replies; the root message sets it to its own ID

**Fingerprint change:** Update `_message_fingerprint()` to include `sender` so a human reply to an agent message doesn't dedup against the agent's original.

```python
def _message_fingerprint(message: Message) -> tuple:
    return (
        message.type,
        message.task_id,
        message.title.strip(),
        message.body.strip(),
        message.urgency,
        message.sender,       # NEW
    )
```

**New emit helper:** `emit_inbound_message()` for creating human→agent messages, parallel to `emit_fyi()`:

```python
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
    """Create an inbound message from developer or external agent to a persona."""
    msg = create_message(
        "fyi",
        title=title or body[:80],
        body=body,
        urgency=urgency,
        task_id=task_id,
        payload=payload or {},
    )
    msg.sender = sender
    msg.addressed_to = addressed_to
    msg.reply_to = reply_to
    msg.thread_id = thread_id or msg.id   # root sets thread_id = own id
    return msg
```

**Query helpers to add:**

```python
def get_inbound(messages: list[Message], addressed_to: str = "") -> list[Message]:
    """Return unread inbound messages, optionally filtered to a persona."""
    inbound = [m for m in messages if m.sender != "agent" and m.status == "unread"]
    if addressed_to:
        inbound = [m for m in inbound if m.addressed_to in ("", addressed_to)]
    return sorted(inbound, key=_urgency_key)

def get_thread(messages: list[Message], thread_id: str) -> list[Message]:
    """Return all messages in a thread, ordered by creation time."""
    return sorted(
        [m for m in messages if m.thread_id == thread_id],
        key=lambda m: m.created_at,
    )
```

---

## A2 — Separate inbox.json for inbound messages

**File:** [messaging.py](../messaging.py) — add alongside existing outbox helpers

Inbound messages live in `data/inbox.json`, separate from the outbound `data/outbox.json`. This keeps backward compatibility: existing `load_outbox()` / `save_outbox()` are unchanged.

```python
INBOX_FILE = os.path.join("data", "inbox.json")

def _inbox_path(state_file: str | None = None) -> str:
    if state_file:
        return os.path.join(os.path.dirname(state_file), "inbox.json")
    return INBOX_FILE

async def load_inbox(state_file: str | None = None) -> list[Message]:
    """Load inbound messages from inbox.json."""
    path = _inbox_path(state_file)
    # same implementation pattern as load_outbox()

async def save_inbox(messages: list[Message], state_file: str | None = None) -> None:
    """Save inbound messages atomically."""
    # same implementation pattern as save_outbox()

async def append_and_save_inbox(
    message: Message, state_file: str | None = None
) -> None:
    """Load, append (with dedup), and save in one call — for API and CLI use."""
    messages = await load_inbox(state_file)
    if append_message(messages, message):
        await save_inbox(messages, state_file)
```

**Why separate files?**
- `outbox.json` is the established format; its consumers (CLI, API) don't need to change
- The scheduler needs to query inbound-only messages efficiently — a separate file makes this a simple load, not a filter scan
- Pruning policies differ: outbox prunes dismissed after 7 days; inbox can prune after the agent has processed the message

---

## A3 — API endpoint for sending inbound messages

**File:** [api_server.py](../api_server.py)

Add a `POST /api/messages` endpoint. This is the programmatic entry point for humans and external agents.

```python
class MessageSendRequest(BaseModel):
    body: str
    sender: str = "developer"
    addressed_to: str = ""          # persona name, "" = broadcast
    title: str = ""
    urgency: str = "normal"         # low | normal | high | urgent
    reply_to: str = ""              # message ID being replied to
    thread_id: str = ""
    task_id: str = ""
    payload: dict = Field(default_factory=dict)

@app.post("/api/messages")
async def api_send_message(req: MessageSendRequest):
    """Send an inbound message to a persona (or broadcast to all)."""
    from messaging import emit_inbound_message, append_and_save_inbox, enqueue_inbox_event

    msg = emit_inbound_message(
        body=req.body,
        sender=req.sender,
        addressed_to=req.addressed_to,
        title=req.title,
        urgency=req.urgency,
        reply_to=req.reply_to,
        thread_id=req.thread_id,
        task_id=req.task_id,
        payload=req.payload,
    )
    await append_and_save_inbox(msg, config.state_file)

    # Wake the scheduler so the agent sees the message promptly
    from event_watcher import enqueue_event
    enqueue_event("message", {"message_id": msg.id, "addressed_to": msg.addressed_to})

    return {"id": msg.id, "thread_id": msg.thread_id, "status": "delivered"}
```

Add a `GET /api/messages` endpoint to retrieve inbound messages (mirrors `GET /api/inbox`):

```python
@app.get("/api/messages")
async def api_list_messages(
    status: str = Query("all"),
    addressed_to: str = Query(""),
):
    from messaging import load_inbox
    messages = await load_inbox(config.state_file)
    if status != "all":
        messages = [m for m in messages if m.status == status]
    if addressed_to:
        messages = [m for m in messages if m.addressed_to in ("", addressed_to)]
    return [asdict(m) for m in messages]
```

Add thread endpoint:

```python
@app.get("/api/messages/thread/{thread_id}")
async def api_get_thread(thread_id: str):
    """Return all messages (inbound + outbound) in a thread."""
    from messaging import load_inbox, load_outbox, get_thread
    inbound = await load_inbox(config.state_file)
    outbound = await load_outbox(config.state_file)
    thread = get_thread(inbound + outbound, thread_id)
    return [asdict(m) for m in thread]
```

---

## A4 — CLI `natl msg send`

**File:** [cli.py](../cli.py)

Add a `msg` subcommand group with `send` and `list`:

```
natl msg send "body text"  [--to PERSONA] [--reply-to MSG_ID]
                            [--urgency low|normal|high|urgent]
                            [--title TEXT] [--task TASK_ID]
natl msg list [--to PERSONA] [--status unread|read|all]
natl msg thread THREAD_ID
```

The `send` command:
1. Calls `emit_inbound_message()` with `sender="developer"`
2. Calls `append_and_save_inbox(msg, state_file)`
3. Enqueues a `message` event to wake the scheduler
4. Prints `Sent: {msg.id} (thread: {msg.thread_id})`

Update `natl inbox list` to accept a `--direction inbound|outbound|all` flag so the combined view is still accessible.

---

## A5 — Scheduler prompt injection

**File:** [scheduler.py](../scheduler.py)

At the start of each heartbeat cycle (after loading tasks and outbox), also load `inbox.json` and inject unread messages addressed to the active persona into the heartbeat prompt context.

**Where to inject:** The scheduler currently builds the heartbeat prompt in `run_heartbeat()` and `run_task_heartbeat()`. A new `build_inbound_message_block()` helper formats the pending messages:

```python
def build_inbound_message_block(
    messages: list[Message], persona_name: str, max_messages: int = 5
) -> str:
    """Format unread inbound messages for prompt injection."""
    pending = get_inbound(messages, addressed_to=persona_name)[:max_messages]
    if not pending:
        return ""
    lines = ["== MESSAGES FOR YOU =="]
    for m in pending:
        sender_label = m.sender or "developer"
        reply_hint = f" (re: {m.reply_to})" if m.reply_to else ""
        lines.append(f"[{m.id}]{reply_hint} From {sender_label}: {m.title}")
        if m.body and m.body != m.title:
            lines.append(f"  {m.body[:300]}")
    lines.append(
        "\nIf you want to reply, include: REPLY TO {msg_id}: your reply text"
    )
    return "\n".join(lines)
```

**Where in the scheduler loop to add this:**

In [scheduler.py](../scheduler.py), around line 825 where the inbox summary is logged — load inbox and pass the block to the workflow:

```python
# Load inbound messages for prompt injection
inbound_messages = await _load_inbox(config.state_file)   # new retry-wrapped loader
inbound_block = build_inbound_message_block(inbound_messages, persona.name)
```

Then pass `inbound_block` through to `run_heartbeat()` and `run_task_heartbeat()` in [workflow.py](../workflow.py) where prompt context is assembled.

**Signature change for `run_heartbeat` and `run_task_heartbeat`:**
Add `inbound_block: str = ""` parameter. Inject it near the top of any step prompt that the agent should see (status check is the natural place).

---

## A6 — Reply extraction

**File:** [workflow.py](../workflow.py)

After `_run_step()` completes for the status-check step, scan the agent's output for reply blocks:

```python
def _extract_replies(text: str) -> list[dict]:
    """Parse REPLY TO {msg_id}: text blocks from agent output.

    Format the agent is instructed to use:
        REPLY TO m1a2b3: Here is my response to your question.
    """
    import re
    pattern = re.compile(
        r"REPLY TO ([a-z0-9]+):\s*(.+?)(?=REPLY TO [a-z0-9]+:|$)",
        re.DOTALL | re.IGNORECASE,
    )
    replies = []
    for match in pattern.finditer(text):
        msg_id = match.group(1).strip()
        body = match.group(2).strip()
        if msg_id and body:
            replies.append({"reply_to": msg_id, "body": body})
    return replies
```

After extracting replies, create outbound messages and mark the originals as read:

```python
async def _process_agent_replies(
    replies: list[dict],
    inbox_messages: list[Message],
    outbox: list[Message],
    persona_name: str,
    state_file: str,
) -> None:
    from messaging import (
        emit_inbound_message, find_message, mark_read,
        save_inbox, save_outbox
    )
    for r in replies:
        original = find_message(inbox_messages, r["reply_to"])
        thread_id = original.thread_id if original else r["reply_to"]
        reply_msg = create_message(
            "fyi",
            title=f"Reply to {r['reply_to']}",
            body=r["body"],
            persona=persona_name,
        )
        reply_msg.sender = persona_name
        reply_msg.addressed_to = original.sender if original else "developer"
        reply_msg.reply_to = r["reply_to"]
        reply_msg.thread_id = thread_id
        append_message(outbox, reply_msg)
        if original:
            mark_read(original)
    if replies:
        await save_outbox(outbox, state_file)
        await save_inbox(inbox_messages, state_file)
```

---

## A7 — Updated `natl inbox` display

**File:** [messaging.py](../messaging.py) — update `format_inbox()`

Add direction indicator to the inbox format:

```python
def format_inbox(messages: list[Message], *, show_read: bool = False) -> str:
    ...
    for m in sorted(filtered, key=_urgency_key):
        icon = _TYPE_ICON.get(m.type, "?")
        badge = _URGENCY_BADGE.get(m.urgency, "")
        read_marker = "*" if m.status == "unread" else "."
        direction = "←" if m.sender == "agent" else "→"   # NEW
        line = f"  {read_marker} {direction} [{icon}] {m.id}  {m.title}"
        ...
```

---

## Files changed

| File | Change |
|---|---|
| [messaging.py](../messaging.py) | Add 4 fields to `Message`; update fingerprint; add `emit_inbound_message`, `get_inbound`, `get_thread`, `load_inbox`, `save_inbox`, `append_and_save_inbox`; update `format_inbox` |
| [api_server.py](../api_server.py) | Add `MessageSendRequest`, `POST /api/messages`, `GET /api/messages`, `GET /api/messages/thread/{id}` |
| [cli.py](../cli.py) | Add `msg send`, `msg list`, `msg thread` subcommands; add `--direction` flag to `inbox list` |
| [scheduler.py](../scheduler.py) | Load `inbox.json` each heartbeat; pass `inbound_block` to `run_heartbeat`; add retry-wrapped `_load_inbox` |
| [workflow.py](../workflow.py) | Accept `inbound_block` parameter; inject into step prompts; add `_extract_replies`, `_process_agent_replies` |
| [tasks.py](../tasks.py) | Add `created_by` population from API (no schema change — field already exists) |

---

## Data model migration

No migration required. New `Message` fields default to `""` or `"agent"`. Existing `outbox.json` files load cleanly through the existing forward-compatibility filter:

```python
filtered = {k: v for k, v in entry.items() if k in Message.__dataclass_fields__}
```

`inbox.json` is a new file; it starts empty.

---

## Testing checklist

- [ ] `Message` loads from outbox.json missing new fields (backward compat)
- [ ] `emit_inbound_message()` sets `thread_id = msg.id` when not supplied
- [ ] `get_inbound()` filters correctly by `addressed_to`
- [ ] `get_thread()` returns inbound + outbound in creation order
- [ ] `POST /api/messages` returns `{id, thread_id}` and writes `inbox.json`
- [ ] `natl msg send "hello" --to workspace_observer` writes inbox entry and wakes scheduler
- [ ] Scheduler injects `== MESSAGES FOR YOU ==` block when inbox has unread messages for active persona
- [ ] `_extract_replies()` parses single and multiple REPLY TO blocks
- [ ] Agent reply creates outbound message in outbox with correct `reply_to` and `thread_id`
- [ ] Original inbound message is marked read after reply is extracted
- [ ] `natl inbox list` shows direction arrow
- [ ] `GET /api/messages/thread/{id}` returns messages from both files

---

## Build order within Move A

| Step | Effort | Dependency |
|---|---|---|
| A1: Extend `Message` + new helpers in messaging.py | 2h | none |
| A2: `inbox.json` load/save | 1h | A1 |
| A3: API endpoints | 1.5h | A2 |
| A4: CLI `natl msg` | 1h | A2 |
| A5: Scheduler injection | 1.5h | A2 |
| A6: Reply extraction in workflow.py | 2h | A5 |
| A7: Updated inbox display | 0.5h | A1 |
| Tests | 2h | all above |

Total: ~11h of focused implementation.
