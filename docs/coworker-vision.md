# NATLClaw — The AI Coworker

## Vision

NATLClaw is an **AI coworker** — an autonomous agent you interact with the same way you'd interact with a human colleague. You assign it tasks, it works on them independently, reports back, asks questions when stuck, and builds institutional knowledge about your project over time.

It's not a chatbot you prompt. It's not an autocomplete engine. It's a coworker that happens to be an AI.

---

## 1  What Makes a Coworker

A human coworker does things that no current AI tool does:

| Human coworker behavior | Current AI tools | NATLClaw goal |
|---|---|---|
| You assign them a task and they go do it | You type a prompt and wait for output | Agent picks up tasks from a queue and works autonomously |
| They remember what you talked about yesterday | Session resets every conversation | Second brain persists across all interactions |
| They learn your project over weeks | Re-indexes on every query | Heartbeat loop continuously builds understanding |
| They come to you with questions | Hallucinates when uncertain | Asks for clarification, blocks on ambiguity |
| They report status without being asked | Silent until prompted | Proactive status updates at natural checkpoints |
| They flag problems they notice | Only responds to what you ask | Background observation catches issues early |
| They hand work back to you when done | Output disappears into chat history | Structured handoff with deliverables and context |
| They get better at their job over time | Same capability forever | Learning engine captures patterns and preferences |
| They have a role and expertise | Generic assistant | Persona system with domain-specific knowledge |
| They coordinate with other coworkers | Isolated single-agent | Multi-persona orchestration |

NATLClaw already has several of these pieces. The gap is in **interaction** — the ability to talk to it, assign work, and receive work back.

---

## 2  Interaction Model

### 2.1  The conversation metaphor

You interact with a human coworker through a mix of:
- **Synchronous conversation** — "Hey, can you look at this?" (Slack/Teams DM)
- **Task assignment** — "Can you refactor the auth module this sprint?" (ticket)
- **Status check-in** — "How's that auth refactor going?" (standup)
- **Proactive update** — "Hey, I found a bug in the payment flow" (they come to you)
- **Handoff** — "Auth refactor is done, PR is up, here's what changed" (deliverable)

NATLClaw needs all five modes.

### 2.2  Inbound channel (you → agent)

The developer needs a way to communicate with the agent. Options, from simplest to richest:

**A. Task file (simplest)**

Drop a `TASKS.md` or `data/tasks.json` file that the agent reads each heartbeat:

```json
[
    {
        "id": "t001",
        "from": "developer",
        "message": "Refactor the auth module to use the Result<T,E> pattern",
        "priority": "high",
        "status": "pending",
        "created_at": "2026-04-07T10:00:00Z"
    }
]
```

The agent picks up pending tasks, works on them across heartbeats, and updates status.

**B. CLI chat**

```bash
natl say "Can you review the payment module for security issues?"
natl ask "What patterns have you noticed in the hooks directory?"
natl assign "Write tests for src/utils/result.ts" --priority high
natl status                    # What are you working on?
natl inbox                     # Any messages from the agent?
```

Messages go into a queue. The agent processes them at the next heartbeat (or immediately if idle).

**C. VS Code panel / chat integration**

A sidebar panel or chat participant (`@coworker`) in VS Code that lets you type naturally:

```
You: Can you look at the auth flow and suggest improvements?
NATLClaw: I'll review it this heartbeat cycle. I'll check back in ~2 minutes.
--- (agent works autonomously) ---
NATLClaw: Done. Found 3 things:
  1. Token refresh has a race condition (src/auth/refresh.ts#L44)
  2. The error handler swallows network errors silently
  3. No CSRF protection on the login endpoint
  I've written these up as notes n0045-n0047. Want me to create a fix for any of these?
```

**D. Messaging channel (OpenClaw integration)**

If integrated with OpenClaw, the coworker becomes reachable on Slack, Discord, or any other channel — exactly like a human coworker.

### 2.3  Outbound channel (agent → you)

The agent needs a way to report back. This is the most neglected aspect of current AI tools.

**Notification mechanisms:**
- Write to `data/outbox.json` — the CLI's `natl inbox` reads it
- OS notification (toast on Windows, notification center on macOS)
- Append to a `COWORKER_LOG.md` in the project root
- Send a message via OpenClaw channel (Slack, Discord, etc.)
- VS Code notification / chat message

**When the agent should speak up:**
- Task completed → handoff with deliverables
- Task blocked → question for the developer
- Problem discovered → proactive alert
- Status milestone → "halfway through the auth refactor"
- Uncertainty → "I'm not sure if you want X or Y — which?"

---

## 3  Task System

### 3.1  Task lifecycle

```
┌──────────┐     assign     ┌───────────┐     heartbeat    ┌─────────────┐
│  Pending  │ ──────────────▶│  Assigned  │ ───────────────▶│ In Progress │
└──────────┘                └───────────┘                  └──────┬──────┘
                                                                  │
                                          ┌───────────────────────┼───────────────────┐
                                          │                       │                   │
                                          ▼                       ▼                   ▼
                                   ┌────────────┐         ┌────────────┐       ┌───────────┐
                                   │  Completed  │         │   Blocked  │       │  Failed   │
                                   │  (handoff)  │         │ (question) │       │  (error)  │
                                   └────────────┘         └────────────┘       └───────────┘
```

### 3.2  Task dataclass

```python
@dataclass
class Task:
    """A unit of work assigned to the agent."""
    id: str
    title: str
    description: str
    priority: str = "medium"           # low | medium | high | urgent
    status: str = "pending"            # pending | assigned | in_progress | blocked
                                       # | completed | failed
    assigned_to: str = ""              # persona name
    created_by: str = "developer"
    created_at: str = ""
    started_at: str | None = None
    completed_at: str | None = None

    # Work tracking
    heartbeats_spent: int = 0
    max_heartbeats: int = 10           # timeout after N heartbeats
    progress_notes: list[str] = field(default_factory=list)
    deliverables: list[str] = field(default_factory=list)  # file paths, note IDs

    # Communication
    questions: list[dict] = field(default_factory=list)    # agent → developer
    answers: list[dict] = field(default_factory=list)      # developer → agent
    blockers: list[str] = field(default_factory=list)
```

### 3.3  Event-driven, task-aware scheduler

The scheduler no longer sleeps for a fixed interval. It uses the **hybrid trigger system** (see §5) to respond to events in real time while still maintaining a background heartbeat for knowledge work:

```python
async def run_scheduler(config: AppConfig) -> None:
    persona = load_persona(config.persona)
    event_bus = EventBus(config.message_bus_url)      # Redis / Azure Service Bus
    api_queue = asyncio.Queue()                        # API gateway → scheduler

    # Subscribe to event channels
    await event_bus.subscribe("tasks.*", api_queue.put)
    await event_bus.subscribe("messages.*", api_queue.put)
    await event_bus.subscribe("system.*", api_queue.put)

    while True:
        state = await load_state(config.state_file)
        brain = await load_brain(config.state_file)
        tasks = await load_tasks("data/tasks.json")

        # Phase 1: Drain the event queue (event-driven triggers)
        while not api_queue.empty():
            event = await api_queue.get()
            await handle_event(event, state, brain, tasks, persona)

        # Phase 2: Work on assigned/pending tasks
        active_task = get_active_task(tasks, persona.name)
        if active_task:
            await run_task_heartbeat(agent, state, brain, active_task, persona)
        else:
            pending = get_pending_tasks(tasks)
            if pending:
                task = pending[0]  # highest priority
                task.status = "assigned"
                task.assigned_to = persona.name
                await run_task_heartbeat(agent, state, brain, task, persona)
            else:
                # Phase 3: No tasks or events — background work
                await run_heartbeat(agent, state, brain, config, persona)

        await save_tasks(tasks, "data/tasks.json")

        # Adaptive sleep: short if events pending, full interval otherwise
        try:
            event = await asyncio.wait_for(
                api_queue.get(), timeout=config.heartbeat_interval_sec
            )
            api_queue.put_nowait(event)   # put it back for next cycle
        except asyncio.TimeoutError:
            pass  # normal heartbeat interval elapsed
```

The key change: `asyncio.wait_for` replaces `asyncio.sleep`. The scheduler wakes **immediately** when an event arrives (a new task, a developer message, a system alert) but still ticks at the heartbeat interval for background work. This is what transforms the agent from a cron job into a responsive coworker.

### 3.4  Task heartbeat workflow

When working on a task, the heartbeat follows a different flow:

```python
async def run_task_heartbeat(agent, state, brain, task, persona):
    """One heartbeat cycle dedicated to a task."""
    task.status = "in_progress"
    task.heartbeats_spent += 1

    # Step 1: Context — what do I know about this task?
    context = build_task_context(task, brain, state)

    # Step 2: Plan — what should I do this cycle?
    plan = await _run_step(agent, "task_plan",
        f"You are working on: {task.title}\n{task.description}\n\n"
        f"Progress so far: {task.progress_notes}\n"
        f"Heartbeat {task.heartbeats_spent}/{task.max_heartbeats}\n\n"
        f"What is the single most important thing to do this cycle?",
        state)

    # Step 3: Execute — do the work
    result = await _run_step(agent, "task_execute",
        f"Execute this plan:\n{plan}\n\n"
        f"Use your tools to do the actual work. "
        f"If you need information from the developer, say BLOCKED: <question>.",
        state)

    # Step 4: Check — am I done, stuck, or continuing?
    if "BLOCKED:" in result:
        task.status = "blocked"
        question = result.split("BLOCKED:", 1)[1].strip()
        task.questions.append({
            "question": question,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "heartbeat": state.execution_count,
        })
        notify_developer(f"🔒 Blocked on '{task.title}': {question}")
    elif is_task_complete(result, task):
        task.status = "completed"
        task.completed_at = datetime.now(timezone.utc).isoformat()
        notify_developer(f"✅ Completed '{task.title}'. Deliverables: {task.deliverables}")
    else:
        task.progress_notes.append(result[:300])

    # Step 5: Capture — store what was learned
    await _distil_to_brain(agent, state, brain, f"task_{task.id}", result)
```

---

## 4  Communication Protocol

### 4.1  Agent-to-developer messages

The agent writes to an outbox that the developer can check:

```python
@dataclass
class Message:
    """A message from the agent to the developer."""
    id: str
    type: str       # "status" | "question" | "alert" | "handoff" | "fyi"
    subject: str
    body: str
    task_id: str | None = None
    urgency: str = "normal"    # low | normal | high | urgent
    requires_response: bool = False
    timestamp: str = ""
    read: bool = False

# Examples:
Message(type="handoff", subject="Auth refactor complete",
        body="Refactored 3 files to use Result<T,E>. PR-ready changes in:\n"
             "- src/auth/login.ts (was try/catch, now Result)\n"
             "- src/auth/refresh.ts (fixed race condition)\n"
             "- src/auth/types.ts (added AuthError type)\n"
             "Tests: 12 new, all passing.",
        task_id="t001")

Message(type="question", subject="Auth refactor: which error type?",
        body="The codebase has both AppError and HttpError. "
             "Which should auth errors extend?",
        task_id="t001", requires_response=True)

Message(type="alert", subject="Possible security issue in payment flow",
        body="While reviewing auth, I noticed src/api/payment.ts "
             "sends the full card number in the request body (line 44). "
             "This should use a tokenised reference instead.",
        urgency="high")

Message(type="fyi", subject="Brain maintenance",
        body="Consolidated 12 atomic notes into 3 wiki pages. "
             "Archived 5 stale notes. Brain health: good.",
        urgency="low")
```

### 4.2  Developer-to-agent messages

```python
@dataclass
class Instruction:
    """A message from the developer to the agent."""
    id: str
    type: str       # "task" | "answer" | "feedback" | "directive"
    content: str
    task_id: str | None = None      # if answering a question
    message_id: str | None = None   # if responding to a message
    timestamp: str = ""
    processed: bool = False

# Examples:
Instruction(type="task",
            content="Review src/api/ for any endpoints missing auth middleware")

Instruction(type="answer",
            content="Use AppError. HttpError is legacy, we're migrating away from it.",
            task_id="t001")

Instruction(type="feedback",
            content="Good catch on the payment issue. Prioritise fixing that.",
            message_id="m012")

Instruction(type="directive",
            content="From now on, always check for console.log statements in code reviews.")
```

### 4.3  Conversation state

The agent maintains a conversation history in the brain, so it remembers what you've discussed:

```python
brain.notes["conv_001"] = {
    "id": "conv_001",
    "note_type": "conversation",
    "content": "Developer wants auth errors to extend AppError, not HttpError. "
               "HttpError is legacy and being migrated away.",
    "source": {"type": "developer_answer", "task_id": "t001"},
    "tags": ["directive", "error-handling", "auth"],
    "category": "areas",
}
```

These conversation notes are searchable and can inform future tasks — just like remembering a conversation with a coworker.

---

## 5  Hybrid Trigger System

A human coworker doesn't work on a fixed 2-minute timer. They respond to messages immediately, check their calendar for deadlines, notice when something breaks, and fill quiet time with background work. NATLClaw's **hybrid trigger system** replicates this by combining three trigger modes into one cohesive architecture.

Full technical specification: `docs/comprehensive-hybrid-trigger-architecture.md`

### 5.1  Three trigger modes

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        Trigger Sources                                  │
│                                                                         │
│  TIME-BASED              EVENT-DRIVEN             REQUEST-DRIVEN        │
│  (Heartbeat)             (Message Bus)            (API Gateway)         │
│                                                                         │
│  ┌────────────────┐      ┌────────────────┐      ┌────────────────┐    │
│  │ Background      │      │ Developer msg  │      │ CLI command    │    │
│  │ learning        │      │ Task assigned  │      │ Portal request │    │
│  │ Brain maint.    │      │ File changed   │      │ Webhook call   │    │
│  │ Initiative scan │      │ Git push       │      │ VS Code action │    │
│  │ Knowledge decay │      │ Deadline hit   │      │ OpenClaw msg   │    │
│  └───────┬────────┘      │ System alert   │      └───────┬────────┘    │
│          │               │ Build failed   │              │             │
│          │               └───────┬────────┘              │             │
│          │                       │                       │             │
│          └───────────────────────┼───────────────────────┘             │
│                                  │                                      │
│                                  ▼                                      │
│                    ┌──────────────────────────┐                         │
│                    │  Central Message Bus      │                         │
│                    │  (Redis / Azure Svc Bus)  │                         │
│                    └────────────┬─────────────┘                         │
│                                 │                                       │
│                                 ▼                                       │
│                    ┌──────────────────────────┐                         │
│                    │  Event-Driven Scheduler   │                         │
│                    │  (§3.3)                   │                         │
│                    └──────────────────────────┘                         │
└──────────────────────────────────────────────────────────────────────────┘
```

### 5.2  How each mode maps to coworker behavior

| Trigger mode | Human analogy | NATLClaw implementation |
|---|---|---|
| **Time-based** (heartbeat) | "I'll use my quiet time to organise my notes and review the codebase" | Fixed-interval heartbeat (`scheduler.py`). Runs background learning, brain maintenance, initiative scans. |
| **Event-driven** (bus) | "Ping — I just got assigned a new task / the build broke" | Message bus pushes events directly into the scheduler queue. Agent wakes instantly. |
| **Request-driven** (API) | "Hey, can you look at this real quick?" | API gateway / CLI / portal / VS Code sends a request. Agent responds within seconds. |

A human coworker seamlessly switches between all three modes throughout the day. So does NATLClaw.

### 5.3  Integration layer: Power Automate + n8n + Custom Agents

The hybrid architecture uses the right tool for each integration:

| Layer | Tool | Coworker role |
|---|---|---|
| **Microsoft ecosystem** | Power Automate | Calendar events → deadline triggers. Teams messages → developer communication. SharePoint docs → knowledge ingestion. Outlook → email-based task assignment. |
| **API / webhook integrations** | n8n (self-hosted) | Git push → codebase change event. CI/CD status → build alerts. External APIs → data enrichment. Jira/Linear → task sync. |
| **AI intelligence** | NATLClaw custom agents | All three modes above feed into the scheduler. The agent decides priority, applies reasoning, and acts. |

### 5.4  Event handling

When an event arrives through the bus, the scheduler routes it based on type:

```python
async def handle_event(event: dict, state, brain, tasks, persona):
    """Route an incoming event to the appropriate handler."""
    event_type = event.get("type", "unknown")

    match event_type:
        case "task.created":
            # New task assigned — add to queue, may pre-empt current work
            task = Task.from_event(event)
            tasks.append(task)
            if task.priority == "urgent":
                await notify_developer(f"⚡ Picked up urgent task: {task.title}")

        case "message.developer":
            # Developer said something — process as instruction
            instruction = Instruction.from_event(event)
            if instruction.task_id:
                await unblock_task(tasks, instruction)
            else:
                await process_instruction(instruction, state, brain)

        case "system.file_changed":
            # File changed — update codebase knowledge
            await ingest_file_change(event["data"]["path"], brain)

        case "system.build_failed":
            # CI failure — alert developer and capture the error
            await capture_build_failure(event, brain)
            await notify_developer(
                f"🔴 Build failed: {event['data'].get('summary', 'unknown error')}"
            )

        case "calendar.deadline":
            # Deadline approaching — Power Automate trigger
            await surface_deadline(event, tasks, brain)

        case "portal.request":
            # Customer portal request (insurance use case)
            await handle_portal_request(event, state, brain)

        case _:
            brain.add_note(
                f"Unhandled event: {event_type}",
                note_type="event",
                tags=["unhandled", event_type],
            )
```

### 5.5  Adaptive priority

Not all events are equal. The scheduler maintains a **priority queue** that interleaves events with ongoing task work:

```
Priority order:
  1. urgent task / urgent message       → drop everything
  2. event: build failed / system alert  → handle before next heartbeat
  3. high-priority task in progress      → continue current work
  4. event: new task / developer message → handle at next cycle boundary
  5. medium/low tasks                    → queue behind active work
  6. background heartbeat                → only when nothing else is pending
```

This mirrors how a human coworker triages: if the production server goes down, you stop refactoring the auth module.

### 5.6  The message bus as nervous system

The central message bus (Redis Streams, Azure Service Bus, or RabbitMQ) is the coworker's **nervous system** — it connects all stimuli to the brain:

- **Power Automate** publishes calendar events, email notifications, SharePoint changes
- **n8n** publishes webhook events, CI/CD status, API responses
- **CLI / VS Code / Portal** publish developer and customer requests
- **The scheduler** consumes everything from the bus and acts on it

Without the bus, the agent is a cron job that wakes up, looks around, and goes back to sleep. With the bus, it's a coworker sitting at their desk, always ready to respond.

---

## 6  Coworker Behaviors

### 6.1  Initiative

A good coworker doesn't just do what's assigned — they proactively flag things:

```python
# In the background learning heartbeat (when no tasks are pending):
INITIATIVE_PROMPTS = [
    "While reviewing the codebase, flag any security concerns, "
    "missing error handling, or code that contradicts known conventions.",

    "Check if any recently changed files broke existing patterns "
    "or introduced inconsistencies with the architecture notes in the brain.",

    "Look for TODO/FIXME/HACK comments added in the last 24 hours "
    "and assess whether any are urgent enough to raise to the developer.",
]
```

When the agent finds something noteworthy, it writes a message to the outbox rather than waiting to be asked.

### 6.2  Memory across tasks

When you assign a new task, the agent pulls relevant knowledge from past tasks and conversations:

```
Developer: "Add rate limiting to the API endpoints"

Agent thinks: I know from task t001 that we use AppError for domain errors.
              From note n0023, the API layer is in src/api/ with a central client.ts.
              From the developer's directive, they want middleware-based solutions.
              From note n0034, there's already an auth middleware pattern in src/middleware/.

Agent: "I'll add a rate limit middleware following the pattern in src/middleware/auth.ts.
        I'll use AppError for rate limit violations (429). Should I apply it to all
        endpoints or just public ones?"
```

This is the second brain's real value — not just storing notes, but making the agent a coworker who *remembers*.

### 6.3  Learning on the job

Each completed task makes the agent better at the next one:

```python
# After completing a task, the agent reflects:
reflection_prompt = (
    f"You just completed task '{task.title}'.\n"
    f"Progress notes: {task.progress_notes}\n"
    f"Questions you asked: {task.questions}\n"
    f"Developer feedback: {task.answers}\n\n"
    f"What did you learn that will help with future tasks? "
    f"Capture patterns, preferences, or domain knowledge."
)
```

This is the feedback loop: tasks generate knowledge, knowledge improves future tasks.

### 6.4  Knowing when to ask vs. decide

A junior coworker asks about everything. A senior one makes reasonable decisions and only escalates when it matters. The agent should have a **confidence threshold**:

```python
# In the task execution step:
DECISION_PROMPT = (
    "If you are confident in the approach (matches known patterns, "
    "conventions, and developer preferences), proceed without asking.\n\n"
    "If you are uncertain (no matching pattern, contradictory conventions, "
    "or the decision has significant impact), BLOCK and ask the developer.\n\n"
    "Threshold: if you'd bet less than 80% that the developer would approve "
    "your choice, ask first."
)
```

Over time, as the brain accumulates more conventions and preferences, the agent becomes more autonomous — just like a coworker who's been on the team longer.

---

## 7  Persona as Role

The persona system maps directly to coworker roles:

| Persona | Coworker role | Working style |
|---|---|---|
| `default` | Junior generalist | Asks a lot of questions, learns fast |
| `python_developer` | Senior Python dev | Reviews code, writes tests, suggests refactors |
| `react_developer` | Frontend specialist | Builds components, reviews UI code, checks accessibility |
| `researcher` | Research analyst | Deep dives on topics, writes summaries, finds papers |
| `project_manager` | PM | Tracks tasks, identifies risks, writes status updates |
| `devops_engineer` | DevOps | Monitors infrastructure, checks Docker health, writes pipelines |
| `codebase_learner` | New hire onboarding | Reads everything, builds understanding, asks questions |

You could even run multiple personas as a **team** — assign the auth refactor to `python_developer`, have `project_manager` track progress, and let `researcher` investigate best practices for the approach.

---

## 8  Day in the Life

### Morning (you start working)

```
$ natl status
🤖 NATLClaw (python_developer persona)
   Status: idle (no active tasks)
   Last heartbeat: 6 hours ago
   Brain: 47 notes, 23 connections
   Inbox: 1 message

$ natl inbox
📫 [fyi] Brain maintenance (6h ago)
   Consolidated 8 atomic notes about React patterns into 2 wiki pages.
   Archived 3 stale notes. Brain health: good.

$ natl assign "Review the new checkout flow in src/features/checkout/ for edge cases"
✅ Task t003 created (priority: medium)
   Agent will pick it up at the next heartbeat.
```

### Mid-morning (agent is working)

```
$ natl status
🤖 Working on: t003 "Review checkout flow for edge cases"
   Heartbeat 2/10 | Progress: reviewed CartContext and CheckoutForm
   No blockers.
```

### Before lunch (agent has a question)

```
🔔 NATLClaw notification:
   🔒 Blocked on "Review checkout flow": The checkout form has two
   validation paths — client-side Zod and server-side Express validator.
   They have different rules for phone numbers. Which is canonical?

$ natl answer t003 "Server-side is canonical. Client-side should match it."
✅ Answer sent. Agent will unblock at next heartbeat.
```

### Afternoon (task complete)

```
🔔 NATLClaw notification:
   ✅ Completed "Review checkout flow for edge cases"

$ natl inbox
📫 [handoff] Checkout flow review complete (5m ago)
   Found 4 edge cases:
   1. Empty cart proceeds to payment (src/features/checkout/CheckoutForm.tsx#L67)
      → Fix: add cart.items.length check before submit
   2. Phone validation mismatch (client vs server) — FIXED per your directive
   3. No loading state during payment processing (UX issue)
   4. Discount code applied twice if user double-clicks (race condition)

   Notes captured: n0048-n0051
   Suggested follow-up task: "Fix checkout edge cases 1, 3, and 4"
```

### Evening (agent runs background maintenance)

```
[INFO] No pending tasks. Running background knowledge maintenance.
[INFO] [observe] 3 files changed since last heartbeat
[INFO] [learn] New convention detected: "Checkout validation uses Zod schemas"
[INFO] [connect] Linked n0048 (empty cart edge case) ↔ n0031 (form validation pattern)
[INFO] [export] Updated CODEBASE_CONTEXT.md (92 lines)
```

---

## 9  Implementation Roadmap

### Phase 1: Task queue + CLI (makes it a coworker)

**Goal:** You can assign tasks and get results back.

1. `Task` dataclass and `data/tasks.json` persistence
2. Task-aware scheduler (check tasks before background work)
3. `run_task_heartbeat()` — plan → execute → check → capture
4. `Message` / `Instruction` dataclasses and `data/outbox.json` / `data/inbox.json`
5. CLI commands: `natl assign`, `natl status`, `natl inbox`, `natl answer`, `natl say`
6. Blocked/completed notification (write to outbox)

**Effort:** 3–4 days
**Result:** A functioning AI coworker you can assign work to and check on.

### Phase 2: Communication + memory (makes it a good coworker)

**Goal:** The agent asks smart questions and remembers conversations.

7. Confidence-based ask-vs-decide logic
8. Conversation notes in the brain (remember developer directives)
9. Task reflection step (learn from completed tasks)
10. Proactive initiative (flag issues during background heartbeats)
11. Task history and progress tracking

**Effort:** 2–3 days
**Result:** A coworker who gets better at their job over time.

### Phase 3: Hybrid trigger system (makes it responsive)

**Goal:** The coworker reacts to events in real time, not just on a timer.

12. Central message bus (Redis Streams or Azure Service Bus)
13. Event-driven scheduler — `asyncio.wait_for` replaces `asyncio.sleep`
14. `handle_event()` router for task, message, system, and calendar events
15. Priority queue with event interleaving (urgent events pre-empt)
16. Power Automate connector — calendar deadlines, email triggers, Teams messages
17. n8n connector — Git webhooks, CI/CD status, external API events
18. API gateway (FastAPI) — single entry point for CLI, portal, and VS Code

**Effort:** 5–7 days
**Result:** A coworker who responds to events instantly, not on a 2-minute delay.

### Phase 4: Codebase awareness (makes it a senior coworker)

**Goal:** The agent understands your project deeply.

19. File watcher → publishes `system.file_changed` events to the bus
20. Git hook → publishes `system.git_push` events to the bus
21. Code-aware note types (pattern, convention, architecture)
22. `CODEBASE_CONTEXT.md` export for Copilot
23. Preference model from Copilot accept/reject signals

**Effort:** 3–5 days
**Result:** A coworker who knows your codebase intimately.

### Phase 5: Team mode (makes it a team)

**Goal:** Multiple personas coordinate on complex work.

24. Multi-persona task routing (assign based on expertise)
25. Persona-to-persona handoff (researcher → developer → reviewer)
26. Coordinator persona that breaks work into sub-tasks
27. Shared brain with per-persona views

**Effort:** 5–7 days
**Result:** A team of AI coworkers with different specialisations.

### Phase 6: Rich interaction (makes it seamless)

**Goal:** Natural interaction through your existing tools.

28. VS Code extension / chat participant
29. OpenClaw channel integration (Slack, Discord, etc.)
30. OS notifications (toast, notification center)
31. Semantic search for natural-language queries to the brain

**Effort:** 5–10 days
**Result:** A coworker you interact with as naturally as a human colleague.

### Phase 7: Customer portal — commercial lines (makes it customer-facing)

**Goal:** Expose the coworker through the company portal for insureds and brokers.

32. Multi-tenant brain isolation (per-customer account brain + shared knowledge brain)
33. Portal API integration (API gateway routes portal requests to `portal.request` events)
34. Insurance personas: account_manager, claims_specialist, underwriting_assistant, risk_advisor
35. Policy/claims data ingestion (dec pages, endorsements, loss runs → brain notes)
36. Calendar-driven renewal cadence (Power Automate → 90/60/30 day triggers)
37. Customer-facing message protocol (outbox → portal notification panel)
38. Role-based access (customer sees their account; broker sees their book)

**Effort:** 8–12 days (builds on all previous phases)
**Result:** An AI insurance specialist serving customers and brokers through the portal.

See `docs/insurance-portal.md` for detailed use cases and architecture.

---

## 10  How Existing Components Map

Every piece of NATLClaw already built serves the coworker vision:

| Existing component | Coworker role |
|---|---|
| **Heartbeat loop** (`scheduler.py`) | The coworker's work cycle — checks tasks, processes events, does background work |
| **Second brain** (`second_brain.py`) | The coworker's memory — remembers everything about your project (and customer accounts) |
| **Personas** (`persona_loader.py`, `mcp.json`) | The coworker's job role and expertise |
| **Learning engine** (`learning.py`) | The coworker getting better at their job over time |
| **Workflow modes** (`workflow.py`) | Different working styles — focused task work vs. free exploration |
| **State persistence** (`state.py`) | The coworker picking up where they left off each morning |
| **MCP integration** (`agent_setup.py`) | The coworker's access to tools (Docker, file system, etc.) |
| **Context injection** (`build_context_block`) | The coworker reviewing their notes before starting work |

What's missing (by roadmap phase):

| Gap | Phase | Description |
|---|---|---|
| **Task queue** | Phase 1 | The coworker's to-do list |
| **Message system** | Phase 1–2 | The ability to talk back and forth |
| **CLI / interaction surface** | Phase 1 | A way to reach the coworker |
| **Initiative logic** | Phase 2 | The coworker proactively raising issues |
| **Ask-vs-decide** | Phase 2 | Knowing when to proceed vs. when to check with you |
| **Message bus** | Phase 3 | The nervous system connecting all event sources |
| **Event-driven scheduler** | Phase 3 | Responding to events in real time, not just polling |
| **Trigger integrations** | Phase 3 | Power Automate, n8n, API gateway |
| **Codebase awareness** | Phase 4 | File watchers and git hooks feeding the brain |
| **Multi-persona coordination** | Phase 5 | A team of coworkers with different roles |
| **Rich UI (VS Code, Slack, etc.)** | Phase 6 | Natural interaction surfaces |
| **Customer portal / multi-tenant** | Phase 7 | Customer-facing insurance specialist |

---

## 11  The Bigger Picture

The coworker metaphor is the right abstraction because it's how humans already think about delegation:

- **Chatbot:** "Generate a React component for me" → one-shot, no memory, no context
- **Copilot:** "Help me write this function" → pair programming, but resets every session
- **Coworker:** "Can you handle the auth refactor? I'll be working on the payment flow." → independent work, shared context, async communication

NATLClaw's unique position is that it's building the **institutional knowledge layer** that makes the coworker metaphor work. A human coworker is effective because they:

1. Know the codebase (second brain)
2. Know your preferences (learning engine)
3. Know what they're supposed to be doing (task system)
4. Can work without constant supervision (heartbeat loop)
5. Get better over time (knowledge accumulation)

No current AI tool provides all five. NATLClaw's architecture — heartbeat + brain + personas + learning — is the foundation for all of them. The task queue and communication layer are the final pieces that make it feel like a coworker rather than a background process.

---

## 12  Relation to Other Docs

| Document | How it connects to the coworker vision |
|---|---|
| `docs/comprehensive-hybrid-trigger-architecture.md` | **Full technical spec** for the hybrid trigger system (§5). Power Automate + n8n + custom agents, message bus, API gateway, deployment patterns. |
| `docs/insurance-portal.md` | Customer-facing use cases for commercial lines insurance (Phase 7). Per-customer brains, policy questions, cert requests, FNOL, renewal management, broker portal. |
| `docs/tiered-memory.md` | The coworker's long-term memory architecture |
| `docs/knowledge-quality.md` | Keeping the coworker's knowledge accurate and current |
| `docs/codebase-learner.md` | One specialisation: the coworker who learns your codebase |
| `docs/improvements.md` | Technical fixes needed to make the coworker reliable |
| `docs/openclaw-comparison.md` | OpenClaw provides the communication channels the coworker uses |
| `docs/second-brain-research.md` | Research on memory systems that power the coworker's recall |
