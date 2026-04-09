# NATLClaw — Coworker Roadmap

## Current State (as of 2026-04-09)

**What we have:** An autonomous agent framework with 4 workflow modes, 8+ personas, a second brain (67 notes, 43 wiki pages, topic graph), CodeIntel-style FP/TP lesson calibration, adaptive scheduling, event watcher, daily digest, CLI with 12+ subcommands, 6 LLM providers, and 728 passing tests.

**What's been built since the original roadmap:**

| Roadmap item | Status | Notes |
|---|---|---|
| Move 1: Observe your work | **Done** | `workspace_observer` persona with git, diff, branch, TODO scanning, recently-modified tools. Event queue drain integrated. |
| Move 2: File/Git event queue | **Done** | `event_watcher.py` — watchdog + polling fallback, NDJSON queue, git post-commit hook installer, 24h auto-prune, `natl watch start/stop/status` |
| Move 3: Daily digest | **Done** | `daily_digest.py` — git log, task board, event queue, brain summary. `natl brief` CLI. First-run-of-day auto-detection in scheduler. |
| Move 4: Background task queue | **Not started** | No `data/tasks.json`, no `natl task` commands, no task-aware scheduler |
| Move 5: Coordinator mode | **Partially done** | Round-robin and all-at-once modes work in `workflow.py`. Missing: task dependency graph, inter-persona messaging, file locks (coordinator-mcp from CodeIntel would add this) |
| Move 6: Richer context model | **Partially done** | Brain has note_type, status, confidence, evidence fields (brain-evolution Phase 1). No project registry or active-work tracking yet. |

**Other improvements completed (from improvements.md):**

| Item | Status |
|---|---|
| 1.1 Semantic search | Not started (FAISS/ChromaDB) |
| 1.2 Tiered memory (wiki pages) | **Done** — wiki pages, consolidation, archive |
| 1.3 Note dedup + decay | **Done** — Jaccard dedup at capture, `decay_stale_notes()` |
| 1.4 Adaptive heartbeat interval | **Done** — productivity score scales interval 60-600s |
| 2.1 save_state async | **Done** — `run_in_executor` pattern |
| 2.2 File handle leak | **Done** |
| 2.3 elapsed NameError | **Done** |
| 3.1 Goal/task awareness | **Done** — `goals.py` with lifecycle, auto-expire |
| 3.2 External ingestion | **Done** — `ingest.py` with chunking, summarization, auto-tagging |
| 3.3 Brain lint | **Done** — orphan, stale, empty, dup, density checks |
| 3.4 Source citation tracking | **Done** — structured source metadata on notes |
| 3.5 Multi-persona orchestration | **Done** — coordinator mode (round_robin + all) |
| 3.6 Topic graph recall | **Done** — BFS-based topic traversal |
| 4.1 CLI | **Done** — `cli.py` with run, chat, code, brain, persona, config subcommands |
| 4.2 Structured logging/metrics | **Done** — `metrics.py` SQLite + `JsonFormatter` |
| 4.3 Config validation | **Done** |
| 4.4 Hot reload | **Done** — mtime-based persona reload in scheduler |
| 5.1 Duplicated retry logic | **Done** |
| 5.3 Prompt templates | **Done** — `prompts/` directory + `prompts.py` loader |
| 5.4 Workflow test coverage | **Done** — `test_workflow_modes.py` + `test_learning_loop.py` |

**Brain evolution (from brain-evolution.md):**

| Phase | Status |
|---|---|
| Phase 1: Metadata + Visibility | **Done** — note_type, status, confidence, evidence, `natl brain show/topics/trace` |
| Phase 2: Retrieval Quality | **Partial** — hybrid ranking (confidence, recency, type, graph). Access-frequency tracking pending. |
| Phase 3: Storage Migration | **Partial** — SQLite primary store via `brain.db`, JSON compatibility snapshot. Retrieval still rebuilds in-memory. |
| Phase 4: Knowledge Quality | Not started — contradiction detection, superseded notes, evidence requirements |

**CodeIntel integration (from codeintel-integration.md):**

| Phase | Status |
|---|---|
| Phase 1: codenav-mcp in codebase_learner | **Done** |
| Phase 2: coordinator-mcp for multi-persona | Not started |
| Phase 3: codeintel-mcp for code quality | Not started |
| Phase 4: Advanced patterns | **Partial** — FP/TP calibration done (learning.py), fingerprint dedup done. SQLite brain + multi-turn tool loop not started. |

---

## Gap Analysis → Coworker

The system has evolved from "knowledge journal" to "autonomous agent with workspace awareness." The remaining gap to "AI coworker" is primarily in **interaction and delegation**:

| Coworker trait | Current state | Remaining gap |
|---|---|---|
| **Knows what you're working on** | workspace_observer reads git, files, TODOs | No active-work tracking (current branch/feature inference) |
| **Notices things proactively** | Event watcher queues changes, heartbeat drains them | No push notifications to developer (outbox exists only conceptually) |
| **Brings up relevant info unprompted** | Daily digest, brain summary in prompts | No real-time surfacing — digest is pull-only via `natl brief` |
| **Handles delegated tasks** | Goals system tracks multi-heartbeat objectives | No task queue, no "do this for me" pattern, no structured handoff |
| **Remembers context across days** | Second brain + state persist everything | No structured project model, no preference learning from accept/reject |
| **Coordinates across domains** | Coordinator mode works (round-robin/all) | No task routing by expertise, no inter-persona handoff, no file locks |
| **Responds to events in real time** | Event watcher queues events | Scheduler is still timer-based — doesn't wake on event arrival |

---

## Remaining Moves (prioritized)

### Move 4: Task Queue + Delegation (highest remaining priority)

**Why now:** This is the single feature that transforms the agent from "a thing that runs in the background" to "a coworker I can give work to." Everything else (events, brain, personas) is infrastructure — this is the interaction surface.

**Scope:**
1. `Task` dataclass with lifecycle: `pending → assigned → in_progress → blocked → completed | failed`
2. `data/tasks.json` persistence with atomic write
3. `run_task_heartbeat()` — plan → execute → check → capture workflow
4. CLI commands: `natl task add`, `natl task list`, `natl task status`, `natl task answer`
5. Task-aware scheduler: check tasks before background work, highest priority first
6. Blocked detection: agent says `BLOCKED:` → task status changes, question queued
7. Completion handoff: deliverables list, brain note with summary

**Data model (from coworker-vision.md §3.2):**
```python
@dataclass
class Task:
    id: str
    title: str
    description: str
    priority: str = "medium"           # low | medium | high | urgent
    status: str = "pending"
    assigned_to: str = ""              # persona name
    created_by: str = "developer"
    created_at: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    heartbeats_spent: int = 0
    max_heartbeats: int = 10
    progress_notes: list[str] = field(default_factory=list)
    deliverables: list[str] = field(default_factory=list)
    questions: list[dict] = field(default_factory=list)
    answers: list[dict] = field(default_factory=list)
```

**Files:** New `tasks.py`, modify `scheduler.py`, modify `cli.py`, new `prompts/task/` templates

**Estimated effort:** 3-4 days

---

### Move 5: Outbox + Notifications (makes the coworker talk back)

**Why next:** Without an outbox, the coworker works silently. The developer has to actively check `natl status`. A real coworker taps you on the shoulder when something is done, blocked, or alarming.

**Scope:**
1. `Message` dataclass: type (status, question, alert, handoff, fyi), urgency, requires_response
2. `data/outbox.json` persistence
3. `natl inbox` CLI — read unread messages
4. `natl answer <task_id> "response"` — unblock a task
5. Notification dispatch: write to outbox + optional OS toast (Windows toast, macOS notification center)
6. Auto-message on: task completed, task blocked, proactive alert, brain maintenance summary

**Files:** New `messaging.py`, modify `cli.py`, modify `workflow.py`

**Estimated effort:** 2-3 days

---

### Move 6: Event-Driven Scheduler (makes the coworker responsive)

**Why after tasks/outbox:** The timer-based scheduler works fine for background learning, but once you can assign tasks, you want the coworker to pick them up immediately — not wait up to 2 minutes.

**Scope:**
1. Replace `asyncio.sleep(interval)` with `asyncio.wait_for(queue.get(), timeout=interval)`
2. Event sources push to an `asyncio.Queue`: file watcher events, CLI commands, task mutations
3. Priority queue: urgent task > system alert > new task > background heartbeat
4. Scheduler wakes **instantly** when an event arrives, but still ticks at heartbeat interval for idle work

**Key change in scheduler.py:**
```python
# Current: sleeps regardless of events
await asyncio.sleep(interval)

# Target: wakes on event OR timer, whichever comes first
try:
    event = await asyncio.wait_for(event_queue.get(), timeout=interval)
    event_queue.put_nowait(event)  # put back for processing
except asyncio.TimeoutError:
    pass  # normal heartbeat
```

**Files:** Modify `scheduler.py`, modify `event_watcher.py` (push to asyncio queue instead of file)

**Estimated effort:** 2-3 days

---

### Move 7: Project Context Model (makes the coworker understand your project)

**Why later:** The brain accumulates notes about code, but has no structured awareness of "this is a Python project with pytest, the main entry point is main.py, the test command is `pytest`." This structured context makes every other feature better.

**Scope:**
1. `data/projects.json` — `{repo_path, language, framework, test_cmd, build_cmd, branch, last_activity}`
2. Auto-populated on first heartbeat by scanning workspace (package.json, pyproject.toml, Cargo.toml, etc.)
3. Active-work inference: current branch name + recent commit messages → "user is working on auth refactor"
4. `natl project` CLI commands
5. Project context injected into every heartbeat prompt alongside brain summary

**Files:** New `project_context.py`, modify `scheduler.py`, modify `cli.py`

**Estimated effort:** 2-3 days

---

### Move 8: Coordinator-MCP Integration (makes the coworker a team)

**Why last among near-term:** Multi-persona coordination already works at a basic level. The CodeIntel coordinator-mcp adds proper task dependency graphs, inter-agent messaging, and file locks — but this is a maturity feature, not a capability unlock.

**Scope:**
1. Wire `coordinator-mcp` from CodeIntel into `mcp.json`
2. Replace round-robin coordinator with task-board orchestration
3. Personas register as agents, claim tasks, report results via coordinator
4. File locks prevent two personas from analyzing the same file
5. Inter-agent messaging for handoffs

**Files:** Modify `mcp.json`, modify `workflow.py` coordinator mode

**Estimated effort:** 3-5 days

---

## Longer-Term Horizon

These depend on the foundation above being solid:

### Semantic Search (brain-evolution Phase 2b)
- Embedding index (sentence-transformers + FAISS) for topic-aware retrieval
- Replace recency-based prompt injection with relevance-based
- Depends on: SQLite brain store being the primary read path

### Preference Learning
- Track which brain notes the developer acts on (vs. ignores)
- CodeIntel-style calibration applied to knowledge capture: notes that match developer behavior get boosted, notes that don't get suppressed
- Depends on: task system (provides accept/reject signal)

### Rich Interaction Surfaces
- VS Code extension / `@coworker` chat participant
- OpenClaw channel integration (Slack, Discord)
- OS notifications (beyond basic toast)
- Depends on: outbox/messaging system

### Customer Portal (Phase 7 from coworker-vision.md)
- Multi-tenant brain isolation
- Insurance personas (account_manager, claims_specialist, underwriting_assistant)
- Policy/claims data ingestion
- Calendar-driven renewal triggers
- Depends on: all of the above

---

## Build Order

| Phase | Move | Scope | Depends On | Est. Effort |
|---|---|---|---|---|
| **Next** | #4 — Task queue + delegation | Task dataclass, scheduler, CLI | Nothing | 3-4 days |
| **Then** | #5 — Outbox + notifications | Messaging, inbox CLI, OS toast | Move 4 | 2-3 days |
| **Then** | #6 — Event-driven scheduler | asyncio.wait_for, priority queue | Moves 4-5 | 2-3 days |
| **After** | #7 — Project context model | Auto-detect, active-work tracking | Moves 1-3 (done) | 2-3 days |
| **After** | #8 — Coordinator-MCP | Task board orchestration, file locks | Move 4 | 3-5 days |
| **Later** | Semantic search | Embedding index, relevance retrieval | Brain SQLite migration | 2-3 days |
| **Later** | Preference learning | Accept/reject calibration | Move 4 | 2-3 days |
| **Later** | Rich interaction | VS Code, Slack, OS notifications | Moves 4-5 | 5-10 days |
| **Later** | Customer portal | Multi-tenant, insurance personas | All above | 8-12 days |

---

## Success Criteria

The system feels like a coworker when:

1. You can say `natl task add "refactor the auth module"` and come back to results
2. It tells you when it's done, blocked, or found something alarming
3. It picks up new tasks within seconds (not minutes)
4. It remembers what you discussed yesterday and factors it into today's work
5. It knows your project structure, not just abstract concepts
6. Multiple personas coordinate on complex work without your intervention
7. It stops generating notes about abstract AI and starts generating notes about your code

**Items 1-3 are the next sprint. Items 4-5 are partially done. Items 6-7 are the medium-term target.**
