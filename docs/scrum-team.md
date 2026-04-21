# NATLClaw — AI Scrum Team

## Vision

NATLClaw agents participate as **full members of a scrum team** — picking up
tickets from the Azure DevOps sprint board, writing code, opening PRs, asking
clarifying questions, and reporting at the 9am standup — all without needing a
human to prompt them like a chatbot.

The team:

| Role | Persona | Workflow |
|---|---|---|
| Scrum Master / PM | `project_manager` | Monitors sprint health, flags risks, facilitates three-amigos |
| React Developer | `react_developer` | Builds components, writes Jest/RTL tests, opens PRs |
| .NET Developer | `dotnet_developer` | Writes C# / ASP.NET, writes xUnit tests, opens PRs |
| QA Engineer | `qa_engineer` | Writes and runs tests for new features, flags regressions |
| Codebase Learner | `codebase_learner` | Continuously builds deep knowledge of the project |

---

## How it works

### 1. Sprint kickoff — `natl sync`

At the start (or any time during) a sprint, the ADO connector pulls assigned
work items into NATLClaw's task queue:

```bash
natl sync
# → Pulled 6 work items from Sprint 42 (ADO project: MyProject)
#   t_abc1  [pending]  #4821 Auth middleware refactor          8 pts  → react_developer
#   t_abc2  [pending]  #4834 CartService unit tests            3 pts  → react_developer
#   t_abc3  [pending]  #4851 Payment idempotency              8 pts  → react_developer
#   t_abc4  [pending]  #4812 OrderController xUnit tests      5 pts  → dotnet_developer
#   t_abc5  [pending]  #4798 NullRef fix — OrderService       2 pts  → dotnet_developer
#   t_abc6  [pending]  #4860 E2E tests for checkout flow      5 pts  → qa_engineer
```

Work items are mapped to personas based on ADO work item type, area path, and
team assignment.  The `target_persona` field on each task routes it to the
right specialist.

### 2. Autonomous work

The scheduler picks up pending tasks, assigns them to the appropriate persona,
and runs `run_task_heartbeat()` cycles until each task is done, blocked, or
failed.

Each heartbeat cycle:

```
plan    → read ticket + explore codebase → decide what to do this cycle
execute → write code / tests / docs using file system tools
test    → run npm test / dotnet test — fix failures
pr      → when done, open PR in ADO linked to the work item
report  → post progress update as ADO work item comment
```

The agent **never merges its own PR**.  It opens the PR and notifies the team.
A human reviews and merges.

### 3. Daily standup — 9:00am

At 9am every weekday, each active persona generates a standup entry:

**Format:**
- What I worked on yesterday
- What I plan to work on today
- Blockers (if any)
- Three amigos needed (if any story has unclear requirements)

The standup report is:
- Posted to the configured **Teams channel** as an Adaptive Card
- Sent as a **formatted HTML email** to the team
- Available via `natl standup` or `GET /api/standup/today`

**Example Teams card:**

```
🤖 Daily Standup — Tuesday, April 15

👤 react_developer
Yesterday:   Completed ADO #4821 — auth middleware.  PR #112 open for review.
Today:       ADO #4834 — CartService unit tests.
Blockers:    None
⚠️ Three amigos: ADO #4851 — payment idempotency (client vs server unclear)

👤 dotnet_developer
Yesterday:   Fixed ADO #4798 — NullRef in OrderService.  PR #113 merged.
Today:       ADO #4812 — xUnit tests for OrderController.
Blockers:    Waiting for DB migration script from DevOps.

👤 qa_engineer
Yesterday:   Wrote E2E tests for cart flow (3 new scenarios).
Today:       ADO #4860 — checkout flow E2E tests.
Blockers:    None
```

### 4. Clarifying questions + three amigos

When an agent encounters ambiguous requirements it can't resolve from the
codebase or brain:

```
Agent → ADO comment:  "BLOCKED: Is the idempotency key client- or server-generated?"
Agent → Teams:        🔒 Blocked on Story #4851 — payment idempotency
Agent → Inbox:        Message type=question, addressed_to=developer
```

A **three amigos** request is raised when a story needs PM + Dev + QA alignment
before implementation begins.  The PM persona facilitates:

```
Agent → Teams:  ⚠️ Three amigos needed: ADO #4851
                Reason: Payment idempotency key ownership is ambiguous.
                Who should join: PM, React Dev, QA.
```

Human answers via:
- `natl reply <message_id> "answer"`
- Teams @mention reply (if Graph mode is configured)
- Email reply (Outlook connector reads unread messages)

### 5. Status sync back to ADO

As work progresses, NATLClaw writes status back to ADO automatically:

| NATLClaw status | ADO state | Trigger |
|---|---|---|
| `in_progress` | Active | Task started |
| `blocked` | Blocked | Agent hits uncertainty |
| `completed` | Resolved | PR opened or task done |
| `failed` | Removed | Max heartbeats exceeded / unrecoverable error |

Comments are added to the ADO work item at key lifecycle events
(started, blocked question, PR opened, completed).

---

## Build order

Items with ✅ are done.  Items marked 🔨 are in progress.

| # | Item | Status | File(s) |
|---|---|---|---|
| A | **Standup protocol** | Planned | `standup.py` (not yet built) |
| B | **ADO connector** | ✅ Done | `connectors/ado.py` |
| C | **Teams connector** | ✅ Done | `connectors/teams.py` |
| D | **Outlook connector** | ✅ Done | `connectors/outlook.py` |
| E | **Conversation protocol** (Move B) | Partial | `workflow.py`, `messaging.py` |
| F | **Developer persona tooling** | Planned | `personas/react_developer/`, `personas/dotnet_developer/` |
| G | **QA persona** | Planned | `personas/qa_engineer/` |
| H | **Sprint context injection** | Planned | `sprint_context.py` |
| I | **`natl sync` CLI** | Planned | `cli.py` |
| J | **Connector health API** | Planned | `api_server.py` |

---

## Configuration quick-start

Minimum `.env` to get the ADO + Teams standup working:

```ini
# Azure DevOps (on-prem)
ADO_URL=https://tfs.company.com/DefaultCollection
ADO_PAT=<your-pat>
ADO_PROJECT=MyProject
ADO_TEAM=MyProject Team

# Teams incoming webhook (no app registration needed)
TEAMS_WEBHOOK_URL=https://company.webhook.office.com/webhookb2/…

# (Optional) Outlook email standup
MS_TENANT_ID=<tenant-guid>
MS_CLIENT_ID=<app-guid>
MS_CLIENT_SECRET=<secret>
OUTLOOK_SENDER=natl-agent@company.com
OUTLOOK_STANDUP_RECIPIENTS=team@company.com
```

Full variable reference: [docs/connectors.md](connectors.md#environment-variable-reference)

---

## Persona definitions (planned)

### `react_developer`

```json
{
  "description": "Senior React / TypeScript developer — builds components, writes tests, opens PRs",
  "workflow": "steps",
  "tools": { "module": "personas.react_developer.tools" },
  "steps": ["plan", "implement", "test", "pr"]
}
```

Key tools: `read_source_file`, `write_source_file`, `run_shell_command` (`npm test`, `npx eslint`), `create_pull_request` (via ADO connector).

### `dotnet_developer`

```json
{
  "description": "Senior .NET / C# developer — writes ASP.NET endpoints, xUnit tests, opens PRs",
  "workflow": "steps",
  "tools": { "module": "personas.dotnet_developer.tools" }
}
```

Key tools: `read_source_file`, `write_source_file`, `run_shell_command` (`dotnet build`, `dotnet test`), `create_pull_request`.

### `qa_engineer`

```json
{
  "description": "QA engineer — writes unit/integration/E2E tests, runs suites, flags regressions",
  "workflow": "steps",
  "tools": { "module": "personas.qa_engineer.tools" }
}
```

Key tools: `read_source_file`, `write_source_file`, `run_shell_command` (`npm test`, `dotnet test`, `npx playwright test`), `parse_test_results`.

### `project_manager`

```json
{
  "description": "Scrum master / PM — monitors sprint health, facilitates three amigos, flags risks",
  "workflow": "freeform",
  "tools": { "module": "personas.project_manager.tools" }
}
```

Key tools: `get_sprint_items` (ADO), `send_teams_message`, `create_task`, `send_standup_report`.

---

## Stack notes

- **Frontend:** React + TypeScript — Jest + React Testing Library for unit/component tests; Playwright for E2E.
- **Backend:** ASP.NET Core / C# — xUnit for unit tests; `dotnet test` for execution; `.trx` result parsing.
- **AI agents:** Python — pytest; this repo.
- **Source control:** Azure DevOps Git (on-prem).
- **CI/CD:** ADO Pipelines (YAML).

Personas know the stack from their persona instructions and from the `project_context`
injection (git branch, recently modified files, active work snapshot) that appears
in every heartbeat prompt.

---

## Related docs

- [docs/connectors.md](connectors.md) — ADO, Teams, Outlook connector API and setup
- [docs/coworker-roadmap.md](coworker-roadmap.md) — Overall roadmap and build order
- [docs/coworker-vision.md](coworker-vision.md) — Long-term coworker vision
