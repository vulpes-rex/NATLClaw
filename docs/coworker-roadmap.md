# NATLClaw — Coworker Roadmap

## Current State (as of 2026-04-13)

**What we have:** An autonomous agent framework with multiple workflow modes, personas, a second brain (notes, wiki pages, topic graph), CodeIntel-style calibration, adaptive scheduling, event watcher, daily digest, CLI subcommands, several LLM providers, integration tests for core pipelines, and a large unit test suite.

**What's been built (high level):**


| Roadmap item                       | Status          | Notes                                                                                                                                                                                                                                                                                         |
| ---------------------------------- | --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Move 1: Observe your work          | **Done**        | `workspace_observer` persona with git, diff, branch, TODO scanning, recently-modified tools; decision policy + evidence-gated capture; Phase 4 overlap contradiction handling (see `workflow.py` / `second_brain.py`).                                                                        |
| Move 2: File/Git event queue       | **Done**        | `event_watcher.py` — watchdog + polling fallback, NDJSON pending file + in-process queue, git post-commit hook installer, `natl watch start/stop/status`                                                                                                                                      |
| Move 3: Daily digest               | **Done**        | `daily_digest.py`, `natl brief`, first-run-of-day handling in scheduler                                                                                                                                                                                                                       |
| Move 4: Background task queue      | **Done**        | `tasks.py` — lifecycle, scheduler integration, `natl task …`, `run_task_heartbeat()`                                                                                                                                                                                                          |
| Move 5: Outbox + notifications     | **Done**        | `messaging.py`, outbox persistence, `natl inbox …`, scheduler wiring                                                                                                                                                                                                                          |
| Move 5.5: HTTP API                 | **Done**        | `api_server.py` (FastAPI) — tasks, brain, personas, scheduler, reports; `cli.py serve`                                                                                                                                                                                                        |
| Move 6: Event-driven scheduler     | **Done**        | `scheduler.py` — `_wait_for_event_or_timeout`, wake on file/git/task events + bounded batching                                                                                                                                                                                                |
| Move 7: Project context            | **Done**        | `project_context.py` — detect stack, `get_active_work_snapshot`, injection in scheduler prompts + state                                                                                                                                                                                       |
| Move 8 (roadmap): Coordinator mode | **Mostly done** | Round-robin / all-at-once / task-routed scheduling; task dependency graph (`depends_on`); inter-persona routing (`target_persona`); file soft-locks (`file_locks`); task board block in coordinator synthesis; coordinator delegation parsing. Missing: external coordinator-mcp integration. |
| Richer context / brain             | **Partial**     | Phase 1–3 features in place; Phase 4 (contradictions / superseded) partially implemented for `workspace_observer` evidence overlaps                                                                                                                                                           |


**Other improvements (from improvements.md):** Tiered memory, dedup/decay, adaptive heartbeat, goals, ingest, brain lint, citation metadata, coordinator orchestration modes, metrics, prompts — **Done** where listed in source doc.

**Brain evolution:**


| Phase                          | Status                                                                                                                                                                                                                       |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Phase 1: Metadata + Visibility | **Done**                                                                                                                                                                                                                     |
| Phase 2: Retrieval Quality     | **Partial** — hybrid ranking + API search; **heartbeat prompts** now use `relevance_query` from active work + `search_notes_from_store` / `_notes_for_relevance_query` when configured (`BRAIN_SUMMARY_`* env / `AppConfig`) |
| Phase 3: Storage Migration     | **Partial** — SQLite + JSON snapshot                                                                                                                                                                                         |
| Phase 4: Knowledge Quality     | **Partial** — `record_contradiction` + observer overlap reconciliation (content divergence + **evidence overlap gating**, idempotent links); broader rules TBD                                                               |


**CodeIntel integration:**


| Phase                      | Status          |
| -------------------------- | --------------- |
| Phase 1: codenav-mcp       | **Done**        |
| Phase 2: coordinator-mcp   | **Not started** |
| Phase 3: codeintel-mcp     | **Not started** |
| Phase 4: Advanced patterns | **Partial**     |


---

## Gap Analysis → Coworker (updated)


| Coworker trait                         | Current state                                                            | Remaining gap                                                                                                                                                                                     |
| -------------------------------------- | ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Knows what you're working on**       | `workspace_observer` + `project_context` active-work snapshot in prompts | Finer-grained feature inference (optional ML / labels)                                                                                                                                            |
| **Notices things proactively**         | Events wake scheduler; inbox + outbox; `notification_dispatch.py` — webhooks + OS toast + urgency filtering | VS Code participant, Slack; real-time surfacing beyond heartbeat                                                                                                                                   |
| **Brings up relevant info unprompted** | Digest, brain-in-prompt                                                  | Real-time surfacing beyond heartbeat + inbox                                                                                                                                                      |
| **Handles delegated tasks**            | Full task queue + CLI + API; `depends_on`, `target_persona`, file locks, task board, delegation parsing | coordinator-mcp for cross-process handoffs                                                                                                                                                        |
| **Remembers context across days**      | Brain + state                                                            | Preference learning: inbox **dismiss** / **read** (CLI + API) adjust cited notes via `apply_relevance_feedback`; decision-engine outbox messages carry `brain_note_ids` linking the decision note |
| **Coordinates across domains**         | Coordinator modes                                                        | coordinator-mcp, locks, handoffs                                                                                                                                                                  |
| **Responds quickly to work events**    | Event-wake scheduler                                                     | External triggers (n8n) use API — scale/ops, not core gap                                                                                                                                         |
| **Communicates bidirectionally**       | **Moves A–D shipped (2026-04-16)**: `inbox.json` for human→agent messages; `natl msg send`; `POST /api/messages`; scheduler injects unread messages into heartbeat prompts; agent can `REPLY TO <id>:` in any step; push webhooks fire on inbound (`dispatch_message`); task negotiation (`negotiating` state, accept/redirect/clarify); structured `HandoffContext` on coordinator delegations; `emit_handoff` FYI message | Threading UI; VS Code / Slack surfaces |


---

## Recommended next major vertical (pick one)

**Recorded priority (2026-04-13)** — default sequencing until the team reorders:

1. **Keep Phase 2b retrieval healthy** — optional `pip install -e ".[semantic]"`, tune `BRAIN_SUMMARY_`* / active-work queries; this is already in the scheduler path.
2. **Coordinator-MCP (CodeIntel Phase 2)** — wire external coordinator-mcp when real file contention or cross-process handoffs become the bottleneck; core coordinator maturity (task deps, persona routing, file locks, task board, delegation parsing) is now **done**.
3. **Proactive notifications** — after or in parallel if **human latency** (seeing inbox) hurts more than agent coordination: webhooks, OS toasts, or editor surfaces on top of existing outbox/API.

Revisit when pain shifts: retrieval regressions → emphasize (1); merge conflicts / multiple agents → (2); missed alerts → (3).

Historical leverage notes:

1. **Semantic search / hybrid retrieval (brain-evolution Phase 2b)** — **In progress:** scheduler heartbeat injects notes ranked for **current active work** (see `format_active_work_search_query`, `build_brain_summary_from_store` `relevance_query`). Install optional `pip install -e ".[semantic]"` for vectors; otherwise lexical hybrid still applies.
2. **Coordinator-MCP (CodeIntel Phase 2)** — Multi-persona coordination and file contention.
3. **Proactive notifications** — OS toasts or VS Code driven by outbox/API.

---

## Implemented moves (archive — detail kept for history)

Moves **4–8 (core)** are shipped. Key files: `tasks.py`, `messaging.py`, `api_server.py`, `scheduler.py`, `project_context.py`, `workflow.py` (coordinator).

---

## Remaining near-term moves

### Move 8 remaining: Coordinator-MCP (external)

**Scope:** Wire the external coordinator-mcp server (CodeIntel Phase 2) for true cross-process file locking and handoffs. **Files:** `mcp.json`, persona manifests. Depends on CodeIntel package availability.

**Already done in this session:** `depends_on`, `target_persona`, `file_locks` on `Task`; `get_pending_tasks` persona-routing; `check_file_conflicts`; `_build_task_board_block`; `task_routed` schedule mode; delegation JSON parsing in coordinator synthesis; `GET /api/tasks/board`; `--depends-on` / `--target` CLI flags.

---

### Longer-term horizon

- **Semantic search** — Embedding index (e.g. sentence-transformers + FAISS), relevance-ranked prompt injection.
- **Preference learning** — Done: inbox dismiss/read → brain relevance feedback via `preference_feedback.py`; `POST /api/inbox/{id}/read` and `/dismiss` wire feedback.
- **Rich surfaces** — VS Code participant, Slack/OpenClaw, OS notifications.
- **Customer portal / multi-tenant** — See `docs/coworker-vision.md` Phase 7.

---

## Build Order (current)

| Phase      | Move                                  | Scope                                                          | Depends On        | Est. Effort |
| ---------- | ------------------------------------- | -------------------------------------------------------------- | ----------------- | ----------- |
| **Done**   | #4 — Task queue                       | `tasks.py`, CLI, scheduler                                     | —                 | —           |
| **Done**   | #5 — Outbox                           | `messaging.py`, inbox                                          | #4                | —           |
| **Done**   | #5.5 — HTTP API                       | `api_server.py`, `cli.py serve`                                | #4–5              | —           |
| **Done**   | #6 — Event-driven scheduler           | Wake on queue + NDJSON drain                                   | #4–5              | —           |
| **Done**   | #7 — Project context                  | `project_context.py`, scheduler injection                      | —                 | —           |
| **Done**   | #8 (core) — Coordinator maturity      | Deps, routing, file locks, task board, delegation, task-routed | #4–7              | —           |
| **Done**   | Proactive notifications               | `notification_dispatch.py` — webhooks + OS toast + CLI/API     | #5–5.5            | —           |
| **Done**   | Move A — Bidirectional inbox          | `inbox.json`, `natl msg send`, `POST /api/messages`, scheduler injection, agent replies | #5–5.5 | — |
| **Done**   | Move B — Conversation protocol        | Task negotiation (`negotiating` state, accept/redirect/clarify), `HandoffContext`, `emit_handoff` | Move A | — |
| **Done**   | Move C — Push on inbound              | `dispatch_message` fires on `POST /api/messages`; webhook payload includes routing fields | Move A | — |
| **Done**   | Move D — Handoff emit + docs          | `emit_handoff()` in `messaging.py`; roadmap updated             | Move B            | —           |
| **Next**   | #8 (ext) — Coordinator-MCP            | Wire external coordinator-mcp server                           | CodeIntel pkg     | 1–3 days    |
| **Later**  | Preference learning (deeper)          | Task-level feedback UI, tuning                                 | #4–5              | 2–3 days    |
| **Later**  | Rich interaction                      | VS Code / Slack / OS                                           | #5–5.5            | 5–10 days   |
| **Later**  | Customer portal                       | Multi-tenant, domain personas                                  | All above         | 8–12 days   |


---

## Success Criteria

The system feels like a coworker when:

1. You can run `natl task add "…"` (or POST via API) and get completed work, blocked questions, or failures in inbox.
2. It tells you when it is done, blocked, or alerting — via inbox and API.
3. It picks up task- and file-related events quickly (scheduler wakes on events).
4. It remembers prior context via the brain and project/active-work snapshots.
5. It knows project structure from `project_context`, not only free-form notes.
6. Multiple personas can coordinate — **improving** with Move 8.
7. Workspace observations cite evidence and reconcile conflicting summaries when the same files are implicated.

8. Humans and agents can communicate bidirectionally — send messages via CLI/API, agent replies appear in inbox, webhooks push in real time, coordinator delegations carry structured handoff context.

**Items 1–5, 7, and 8 are in place; item 6 advances with Coordinator-MCP and routing polish.**