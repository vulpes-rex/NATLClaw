Here's the full plan across all six items, grounded in your stack and standup format.

A. Standup Protocol
What it does: At 9am each day, each active persona generates a standup entry. Entries are collected into a team standup report that reads like a real daily standup.

Format (matching your actual standup):


[persona: react_developer]
Yesterday: Completed ADO #4821 — added auth middleware to checkout endpoints. 
           Opened PR #112 for review.
Today:     Working on ADO #4834 — unit tests for CartService.
Blockers:  None
Three amigos needed: ADO #4851 (new payment flow) — requirements unclear 
           on whether idempotency key is client- or server-generated.
Components to build:

standup.py — StandupEntry dataclass + generate_standup(persona, tasks, brain) -> StandupEntry
standup_report.py — collects entries from all active personas into a formatted team report
Scheduler trigger: cron at configurable time (default 9:00am local)
natl standup CLI — trigger on demand, print report to console
GET /api/standup/today — returns today's standup as JSON
Outbox message: standup report lands in inbox so you can read it without CLI
Key design decisions:

Standup prompt uses: active tasks, completed tasks since yesterday, any blocked tasks, brain notes from last 24h
Three amigos flag: agent raises it when a task has confidence < threshold AND it's in planning stage (not yet in_progress)
If no tasks were active yesterday, persona reports what background knowledge work it did
Dependencies: None — builds on existing task history + brain

B. Scrum Board Connector (Azure DevOps)
What it does: Pulls assigned work items from your ADO sprint into NATLClaw's task queue. Writes status back to ADO as work progresses.

ADO on-prem REST API — it's solid:


Base URL: https://{server}/{org}/{project}/_apis
Auth:     Authorization: Basic base64(:{PAT})
Key endpoints:
  GET  /work/teamsettings/iterations?$timeframe=current   → current sprint
  GET  /wit/wiql                                          → query work items
  GET  /wit/workitems?ids={ids}&$expand=all               → full work item detail
  PATCH /wit/workitems/{id}                               → update state/fields
Components to build:

ado_connector.py — thin client wrapping the above endpoints
get_current_sprint() → sprint metadata (goal, start, end)
get_my_work_items(assigned_to, iteration) → list of work items
update_work_item_state(id, state) → sync NATLClaw state back
add_comment(id, text) → post standup updates, questions as ADO comments
ado_sync.py — bidirectional sync logic
Import: ADO Work Item → NATLClaw Task (with ado_id field added to Task)
Export: NATLClaw state changes → ADO state (New → pending, Active → in_progress, etc.)
natl sync CLI command — pull new items, push status updates
config.py additions: ado_url, ado_pat, ado_project, ado_team
State mapping:

ADO State	NATLClaw State
New	pending
Active	in_progress
Blocked	blocked
Resolved	completed
Closed	completed
On the "should we change boards" question:

ADO on-prem has everything you need — the REST API is mature, PAT auth is simple, and it has WIQL for flexible queries. You don't need to change unless you're already feeling pain with it.

If you ever did want something more AI-friendly: Linear has the best developer API (clean REST + webhook-first design, great for automation). But migrating your board mid-project is pure friction. Recommendation: build the ADO connector with a thin BoardConnector abstract interface so swapping to Linear later is a config change, not a rewrite.

C. Conversation Protocol (Move B)
What it does: Structures the back-and-forth between agent and human (and agent-to-agent) beyond the current one-shot blocked/answer pattern.

Patterns to implement:

Pattern	Trigger	Example
Clarifying question	Agent confidence < threshold on a requirement	"ADO #4851: is the idempotency key client- or server-generated?"
Three amigos request	Agent flags a story needing PM + Dev + QA alignment	"Story #4851 needs three amigos before implementation begins"
Handoff	Task completed, passing context to next persona	Dev → QA: "PR #112 ready for test. Here's what changed and the edge cases I know about."
Escalation	Blocked for N heartbeats with no answer	"Still blocked on #4851 after 3 cycles — flagging for human review"
Broadcast	Coordinator notifying all personas	"Sprint goal updated: focus on payment flow stability"
Components to build:

Add conversation_type field to Message: clarification | three_amigos | handoff | escalation | broadcast
natl reply <message_id> "answer" CLI — routes answer back to the blocked task
POST /api/inbox/{id}/reply — same via API
Three amigos workflow: creates a three_amigos message, tags PM + Dev + QA personas as addressed_to, blocks the task until all three respond
Reply extraction: workflow checks agent output for CLARIFY: and THREE_AMIGOS: prefixes (like current BLOCKED: check)
Dependencies: Bidirectional inbox (Move A — done)

D. Developer Persona Tooling
What it does: Gives React and .NET developer personas the tools to do actual autonomous code delivery — read tickets, explore the codebase, write code, run tests, open PRs.

React Developer persona tools:


# Already available via workspace_observer / codebase_learner:
list_files, read_source_file, read_git_diff, read_git_log

# Needs adding:
write_source_file(path, content)      # already exists in some personas
run_shell_command(cmd)                # npm test, npm run build, eslint
create_pull_request(title, branch,   # → ADO PR via REST API
  description, work_item_ids)
get_pull_request_status(pr_id)
.NET Developer persona tools:


write_source_file(path, content)
run_shell_command(cmd)               # dotnet build, dotnet test, dotnet format
create_pull_request(...)             # same ADO PR API
get_test_results(trx_path)           # parse .trx XML test result files
Autonomous delivery workflow (steps):

plan — read ticket, read relevant code, propose approach
implement — write/modify files
test — run test suite, fix failures
pr — create PR, link to ADO work item, post standup-style summary
Key guardrail: Agent opens PRs — it never merges. Human reviews and merges. This is non-negotiable and baked into the persona instructions.

Dependencies: ADO connector (for PR creation + work item linking)

E. QA Persona
What it does: Writes and runs tests for new features, flags regressions, reports test results in the inbox and back to ADO.

Tools:


# React (Jest + React Testing Library):
run_shell_command("npm test -- --coverage --json")
parse_jest_results(json_output)

# .NET (xUnit/NUnit):
run_shell_command("dotnet test --logger trx")
parse_trx_results(trx_path)

# Python (pytest):
run_shell_command("python -m pytest --tb=short -q")
Workflow:

review — read PR diff or ticket, understand what changed
write_tests — write unit + integration tests for the changed code
run — execute test suite, capture results
report — post results to inbox + ADO work item comment; flag any regressions
Three amigos integration: QA persona participates in three amigos requests — reviews acceptance criteria and flags untestable requirements before dev starts.

Dependencies: Developer persona tooling (same shell execution tools), ADO connector

F. Sprint Context Injection
What it does: Every heartbeat prompt gets the current sprint's goal, end date, and each persona's assigned backlog — so the agent always knows what sprint it's in and what it should be prioritizing.

Data injected into prompts:


== SPRINT CONTEXT ==
Sprint: Sprint 42 (ends 2026-04-30, 14 days remaining)
Goal:   Stabilize payment flow and complete auth refactor
Your assigned items:
  [IN PROGRESS] ADO #4821 — Auth middleware — 5 pts — due this sprint
  [PENDING]     ADO #4834 — CartService tests — 3 pts
  [PENDING]     ADO #4851 — Payment flow idempotency — 8 pts ⚠ needs three amigos
Velocity context: 34 pts/sprint avg, 16 pts committed this sprint
Components:

sprint_context.py — get_sprint_context(ado_connector, persona_name) -> SprintContext
Injected in build_context_block() the same way project_context is today
Refreshed every N heartbeats (not every single one — ADO API call)
Cached in state with TTL of ~30 minutes
Dependencies: ADO connector

Recommended build order

B (ADO connector)  →  F (sprint context)  →  A (standups)
                   →  D (dev tooling)      →  E (QA persona)
                   →  C (conversation)
ADO connector is the critical path — B unlocks F, D, and E. Standup (A) is the first visible demo but needs sprint context (F) to be meaningful. Conversation protocol (C) can be built in parallel with any of the above.

Rough sizing: A=1 day, B=2 days, C=2 days, D=3 days, E=2 days, F=1 day. Total ~11 days of focused work.

Where do you want to start?