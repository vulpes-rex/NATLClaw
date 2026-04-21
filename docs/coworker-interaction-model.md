# NATLClaw — Coworker Interaction Model

*Snapshot: 2026-04-15. Describes the current implementation, not aspirational design.*

---

## Overview

The coworker interaction model has two orthogonal channels: **tasks** (units of work) and **messages** (notifications and status). Both are file-persisted, scheduler-driven, and exposed via REST API and CLI.

```
Human / External agent
        │
        │  natl task add / POST /api/tasks
        ▼
    tasks.json  ──────────────────────────────► Scheduler
        │                                           │
        │  heartbeat pick-up + run_task_heartbeat   │
        ▼                                           │
   Task lifecycle                                   │
   (pending → assigned → in_progress               │
    → completed | blocked | failed)                 │
        │                                           │
        │  messaging.py emit_* helpers              │
        ▼                                           │
   outbox.json ◄────────────────────────── Agent output
        │
        │  natl inbox / GET /api/inbox
        ▼
   Human reads
```

**Direction is currently one-directional**: the outbox carries messages *from* agents *to* the human. The only human-to-agent path is `natl task answer` / `POST /api/tasks/{id}/answer`, which requires a blocked task.

---

## Channel 1: Tasks (`tasks.py`, `data/tasks.json`)

### Dataclass

```python
@dataclass
class Task:
    id: str                        # t<6-hex>
    title: str
    description: str
    priority: str                  # low | medium | high | urgent
    status: str                    # pending | assigned | in_progress
                                   # | blocked | completed | failed
    assigned_to: str               # persona name (set by scheduler)
    created_by: str = "developer"  # always "developer" today
    created_at: str
    started_at: str | None
    completed_at: str | None

    # Dependencies & routing
    depends_on: list[str]          # task IDs that must complete first
    target_persona: str            # route to specific persona ("" = any)
    file_locks: list[str]          # files claimed during execution

    # Work tracking
    heartbeats_spent: int
    max_heartbeats: int = 10       # auto-timeout threshold
    progress_notes: list[str]      # rolling log of heartbeat notes
    deliverables: list[str]        # file paths or note:ID refs

    # Q&A (single-turn per block)
    questions: list[dict]          # {question, timestamp, heartbeat}
    answers: list[dict]            # {answer, timestamp}
```

### Lifecycle

```
pending ──── assign_task(persona) ──► assigned
                                           │
                                    start_task()
                                           │
                                           ▼
                                      in_progress
                                      /    |    \
                           block_task()   │   complete_task()
                               │         │           │
                               ▼         │           ▼
                           blocked   advance_task  completed
                               │
                         answer_task()  (developer unblocks)
                               │
                               ▼
                           assigned  (scheduler picks up again)
```

Auto-timeout: `auto_timeout_tasks()` runs every heartbeat; tasks exceeding `max_heartbeats` are failed automatically.

Anti-starvation: `_effective_priority_rank()` promotes tasks by age: +1 after 6h, +2 after 24h, +3 after 72h.

### Dependency resolution

`dependencies_met(task, tasks)` checks that all `depends_on` IDs are `completed`. Pending tasks with unmet deps are excluded from the scheduler pick-up queue.

### File locks

`active_file_locks(tasks)` returns `{normalized_path: task_id}` for all in-progress tasks. `check_file_conflicts(task, tasks)` uses this to detect contention. The coordinator skips sub-personas that would conflict.

### Routing

`get_pending_tasks(tasks, persona_name)` excludes tasks whose `target_persona` does not match. Tasks with `target_persona=""` are available to any persona.

### CLI surface

```
natl task add "title" [--desc TEXT] [--priority low|medium|high|urgent]
                       [--max-heartbeats N] [--depends-on t123,t456]
                       [--target-persona NAME] [--file-locks path1,path2]
natl task list [--status STATUS]
natl task status TASK_ID
natl task answer TASK_ID "answer text"
natl task cancel TASK_ID [--reason TEXT]
natl task retry TASK_ID
```

### REST API

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/tasks` | List tasks (filter: `?status=`) |
| POST | `/api/tasks` | Create task |
| GET | `/api/tasks/{id}` | Get task detail |
| POST | `/api/tasks/{id}/answer` | Answer a blocked task |
| POST | `/api/tasks/{id}/cancel` | Cancel |
| POST | `/api/tasks/{id}/retry` | Retry failed/blocked |
| GET | `/api/tasks/board` | Task board (active, pending, blocked, locks) |

---

## Channel 2: Messages (`messaging.py`, `data/outbox.json`)

### Dataclass

```python
@dataclass
class Message:
    id: str                        # m<6-hex>
    type: str                      # status | question | alert | handoff | fyi
    urgency: str                   # low | normal | high | urgent
    title: str
    body: str
    status: str                    # unread | read | dismissed
    requires_response: bool        # True for "question" type messages

    # Context linking
    task_id: str                   # related task (if any)
    persona: str                   # which persona generated this
    heartbeat: int

    # Timestamps
    created_at: str
    read_at: str | None
    dismissed_at: str | None

    # Structured payload (type-specific data)
    # May include brain_note_ids: list[str] for relevance feedback
    payload: dict
```

### Message types

| Type | Urgency | Use | `requires_response` |
|---|---|---|---|
| `status` | low | Task started | False |
| `question` | high | Task blocked, needs developer input | True |
| `alert` | high/normal | Proactive warning, error spike | False |
| `handoff` | normal | Task completed, deliverables summary | False |
| `fyi` | low | Daily digest, brain insight | False |

### Lifecycle

```
unread → read → dismissed
```

`prune_old_messages()` removes old dismissed messages (default: 7-day cutoff, 200-message cap).

### Deduplication

`append_message()` fingerprints messages on `(type, task_id, title, body, urgency)`. A duplicate across `unread|read` status is silently dropped.

### Relevance feedback loop

When a developer marks a message read or dismisses it, the scheduler applies relevance feedback to any brain notes cited in `payload.brain_note_ids`:
- **Read**: `apply_relevance_feedback(brain, nid, relevant=True, reason="inbox_read")` — boosts the note
- **Dismiss**: demotes the note

This is the primary preference-learning signal currently implemented.

### Emit helpers (all in `messaging.py`)

| Function | Creates message of type |
|---|---|
| `emit_task_started` | `status` |
| `emit_task_completed` | `handoff` |
| `emit_task_blocked` | `question` |
| `emit_task_failed` | `alert` |
| `emit_task_timed_out` | `alert` |
| `emit_alert` | `alert` |
| `emit_escalation_alert` | `alert` with `escalation_type` in payload |
| `emit_fyi` | `fyi` |

### CLI surface

```
natl inbox list [--show-read]
natl inbox show MSG_ID          # marks read
natl inbox dismiss MSG_ID | all
natl inbox clear
```

### REST API

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/inbox` | List messages (filter: `?status=`, `?type=`) |
| GET | `/api/inbox/{id}` | Get message detail |
| POST | `/api/inbox/{id}/read` | Mark read + apply relevance feedback |
| POST | `/api/inbox/{id}/dismiss` | Dismiss + apply negative feedback |
| POST | `/api/inbox/clear` | Clear all dismissed |

---

## Channel 3: Coordinator (multi-persona orchestration, `workflow.py`)

### Modes

| Schedule | Behavior |
|---|---|
| `round_robin` | Runs next persona in roster each heartbeat, cycles |
| `all` | Runs every persona each heartbeat, then synthesises |
| `task_routed` | Runs only personas with active/pending work; falls back to round_robin |

### Delegation

After running sub-personas, the coordinator synthesis step produces a natural-language summary. If the output contains a `{"delegate": [...]}` JSON block, `_process_coordinator_delegations()` creates routed tasks targeting specific sub-personas.

```json
{"delegate": [
  {"persona": "codebase_learner", "task": "Summarise recent auth changes", "files": ["auth.py"]}
]}
```

File lock conflicts are checked before running each sub-persona; conflicted personas are skipped for that cycle.

### What the coordinator cannot do today

- Sub-personas cannot message each other; they share only the brain and task board
- There is no shared context handoff — the coordinator synthesis summary is not injected into the receiving persona's next prompt
- A sub-persona cannot reject a task or negotiate before starting work

---

## Channel 4: Surface Ingress (`surface_ingress.py`)

Normalises external surface events (Slack, webhook, canary) into tasks or inbox messages. Applies idempotency via `data/surface_idempotency.json`. Configured via `AppConfig.surface_ingress_enabled` and `surface_ingress_allowed_adapters`.

Currently: inbound only. No outbound surface push.

---

## Scheduler integration (`scheduler.py`)

Every heartbeat the scheduler:

1. Loads tasks, outbox, brain, state
2. Auto-timeouts tasks that exceeded `max_heartbeats`
3. Picks up the highest-priority pending task (if none active) and runs `run_task_heartbeat()`
4. Runs the persona's background workflow (`run_heartbeat()`)
5. Injects brain summary, goals block, task context, and inbox summary line into prompts
6. Saves state, brain, tasks, outbox atomically
7. Logs `INBOX: N unread message(s)` if unread messages exist
8. Sleeps adaptively; wakes early on file/git/task events from `event_watcher`

The scheduler does **not** inject unread outbox messages into the agent's heartbeat prompt — it only logs a one-line count. The agent has no direct read access to inbox contents.

---

## Key gaps (as of 2026-04-15)

| Gap | Impact |
|---|---|
| No inbound message channel | Human→agent and agent→agent communication requires a task or is impossible |
| Outbox messages not injected into agent prompts | Agent cannot see or respond to its own inbox |
| No conversation threading | Q&A is single-turn per task block; no back-and-forth on non-task topics |
| No inter-agent messaging | Sub-personas share only brain + task board; no direct communication |
| No structured handoff context | Coordinator delegation is a task title only; receiving agent has no prior-work context |
| No push notifications | All consumption is pull (natl inbox, API polling) |
| `created_by` is always "developer" | No way to distinguish human vs. agent vs. external surface authorship |
