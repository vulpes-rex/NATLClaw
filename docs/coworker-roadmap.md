# NATLClaw — Coworker Roadmap

## Current State → Coworker Gap

**What we have:** A knowledge journal that researches AI topics on a timer, with chat/code modes the user explicitly invokes.

**What a coworker does differently:**

| Coworker trait | Current state | Gap |
|---|---|---|
| **Knows what you're working on** | Brain is 86 notes of generic AI research | No awareness of actual projects/repos |
| **Notices things proactively** | Heartbeat captures abstract knowledge | Doesn't watch git, tests, or files |
| **Brings up relevant info unprompted** | Recall only when asked | No notifications or surfaced insights |
| **Handles delegated tasks** | Code mode is one-shot, interactive | No background task queue or "do this later" |
| **Remembers context across days** | Chat memory works but basic | No structured project model, no daily rhythm |
| **Coordinates across domains** | 7 personas exist in isolation | Coordinator mode is stubbed, not working |

---

## Move 1: Flip the Default Heartbeat to Observe Your Work

**Priority:** Now — highest ROI, everything else builds on real context.

The single biggest shift. Instead of generating AI textbook notes, the heartbeat should:

- Read recent git log / changed files
- Scan for test failures, lint issues, TODOs
- Capture insights about *your actual code*, not abstract AI concepts
- The `codebase_learner` persona already does 80% of this — make it the co-default alongside `default`

**Implementation:**
- Update `default` persona heartbeat task to focus on workspace observation
- Pull in git log reading and file scanning from `codebase_learner` tools
- Teach capture prompts to generate notes about real code patterns, not theory
- Add project-root detection so the agent knows which repo it's in

---

## Move 2: File/Git Event Queue → Agent Inbox

**Priority:** Next — makes the observation loop reactive, not just polling.

The `codebase_learner` has `drain_events()` but no event source is wired up. Add:

- A lightweight file watcher (`watchdog`) that queues file-change events to a JSON file
- Post-commit git hook that queues commit metadata (hash, message, files changed)
- The heartbeat drains the queue and reasons about what changed and why it matters
- Events older than 24h are auto-pruned

**Implementation:**
- New module `event_watcher.py` — starts watchdog observer, writes events to `data/events.json`
- Git hook script (`hooks/post-commit`) that appends to the same queue
- `natl watch start` / `natl watch stop` CLI commands
- Heartbeat Step 0: drain events → summarize → feed into capture prompt

---

## Move 3: Daily Digest / Morning Briefing

**Priority:** Then — immediate "coworker" feel, it greets you with useful info.

First heartbeat of a session (or scheduled time) produces:

- What changed since last session (git log summary)
- Open tasks / overdue items (project_manager persona)
- Brain notes relevant to current work
- Suggested priorities for the day

**Implementation:**
- New CLI command `natl brief` — runs a one-shot digest without starting the full scheduler
- Detect "first run of the day" in scheduler (compare last_heartbeat date vs today)
- Aggregate: git log since last_heartbeat, task board status, recent brain notes
- Format as a concise morning briefing printed to console
- Optionally write to `data/daily-digest-YYYY-MM-DD.md`

---

## Move 4: Background Task Queue

**Priority:** After — delegation unlocks the "handle this for me" pattern.

Let the user say `natl task "refactor the auth module"` and have it:

- Queue the task with priority and optional deadline
- Pick it up during a heartbeat cycle (or dedicated worker)
- Report status via `natl task status`
- Store results to brain for review

**Implementation:**
- New `data/task_queue.json` — list of `{id, description, priority, status, created, result}`
- `natl task add "description"` — enqueue
- `natl task list` — show queue with status
- `natl task status <id>` — detailed status for one task
- Heartbeat checks queue → picks highest-priority pending task → runs code-mode agent → writes result
- Task completion triggers a brain note with summary of what was done

---

## Move 5: Complete the Coordinator Mode

**Priority:** Later — maturity feature that compounds once basics work.

Wire up multi-persona orchestration so a single heartbeat can:

- Run `codebase_learner` to ingest what changed
- Run `python_developer` to assess code quality
- Run `project_manager` to update task status
- Synthesize a unified summary across all persona outputs

**Implementation:**
- Finish `coordinator` workflow mode in `workflow.py`
- Config: `coordinator_roster` — list of personas to run each cycle
- Each persona runs independently with its own tools/prompt
- Final synthesis step merges outputs → single brain note + optional console report
- Persona results stored as sub-notes linked to a coordinator summary note

---

## Move 6: Richer Context Model

**Priority:** Later — structural improvement that deepens everything above.

Replace flat brain notes with structured project awareness:

- **Project registry** — which repos, languages, frameworks, test commands, build scripts
- **User patterns** — working hours, common mistakes, preferences, naming conventions
- **Active work tracking** — current branch, current feature, blockers, recent decisions

**Implementation:**
- New `data/projects.json` — `{repo_path, language, framework, test_cmd, build_cmd, last_seen}`
- Auto-populated on first heartbeat by scanning workspace
- `natl project add <path>` / `natl project list` CLI commands
- Heartbeat context enrichment includes project metadata
- User patterns extracted from chat history and brain notes over time
- Active work inferred from git branch name + recent commit messages

---

## Suggested Build Order

| Phase | Move | Estimated Scope | Depends On |
|---|---|---|---|
| **Phase 1** | #1 — Observe your work | Prompt rewrites + tool reuse | Nothing |
| **Phase 2** | #2 — Event queue | New module + git hook + CLI | Phase 1 |
| **Phase 3** | #3 — Daily digest | New CLI command + aggregation | Phases 1–2 |
| **Phase 4** | #4 — Task queue | New data file + CLI + heartbeat integration | Phase 1 |
| **Phase 5** | #5 — Coordinator | Workflow completion + multi-persona runs | Phases 1–4 |
| **Phase 6** | #6 — Context model | New data structures + auto-population | Phases 1–3 |

---

## Success Criteria

The system feels like a coworker when:

1. You open a terminal and it tells you what happened overnight
2. It notices you broke a test before you do
3. It remembers that you were working on the auth module yesterday
4. You can say "clean up that TODO list in scheduler.py" and come back to a PR
5. It connects dots across projects — "this pattern in repo A is similar to what you did in repo B"
6. It stops generating notes about abstract AI and starts generating notes about your code
