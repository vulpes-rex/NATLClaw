# NATLClaw — External Connectors

## Overview

Connectors bridge NATLClaw agents to external services.  Three connectors
are currently implemented:

| Connector | File | Purpose |
|---|---|---|
| **AzureDevOpsConnector** | `connectors/ado.py` | Pull sprint work items; push status, comments, PRs |
| **TeamsConnector** | `connectors/teams.py` | Post standup reports and notifications to Teams |
| **OutlookConnector** | `connectors/outlook.py` | Send email and read replies via Microsoft Graph |

All connectors are **optional** — if credentials are absent the connector
disables itself and every operation is a no-op.  No new *required* Python
packages: all HTTP uses the stdlib `urllib`.

---

## Azure DevOps Connector

### What it does

- Pulls the **current sprint's work items** into NATLClaw's task queue.
- **Pushes status** back to ADO as tasks move through the NATLClaw lifecycle.
- Posts **standup updates and blocked questions** as ADO work item comments.
- **Creates pull requests** linked to work items when developer personas open PRs.

### Setup

#### 1. Create a Personal Access Token

In ADO → User Settings → Personal Access Tokens:

| Field | Value |
|---|---|
| Name | `natl-agent` |
| Expiration | 1 year (or per policy) |
| Scopes | Work Items: Read & Write; Code: Read & Write |

#### 2. Set environment variables

```ini
# .env
ADO_URL=https://tfs.company.com/DefaultCollection   # on-prem
# ADO_URL=https://dev.azure.com/myorg               # cloud
ADO_PAT=<your-pat>
ADO_PROJECT=MyProject
ADO_TEAM=MyProject Team
ADO_API_VERSION=7.1                                  # default
ADO_ASSIGNEES=dev1@company.com,agent@company.com     # optional filter
```

#### 3. State mapping

| ADO State | NATLClaw status |
|---|---|
| New | `pending` |
| Active / In Progress / Committed | `in_progress` |
| Blocked | `blocked` |
| Resolved / Closed / Done | `completed` |
| Removed | `failed` |

Reverse mapping (NATLClaw → ADO) applies when `update_work_item_state()` is called.

### API

```python
from connectors import AzureDevOpsConnector

ado = AzureDevOpsConnector(
    url="https://tfs.company.com/DefaultCollection",
    pat="<token>",
    project="MyProject",
    team="MyProject Team",
)

# Current sprint
sprint = ado.get_current_sprint()
# → SprintInfo(name="Sprint 42", finish_date="2026-04-30T…", …)

# All non-terminal work items in the sprint assigned to a user
items = ado.get_work_items(
    iteration_path=sprint.path,
    assigned_to_email="dev@company.com",
)

# Push status back
ado.update_work_item_state(
    work_item_id=4821,
    natl_status="in_progress",
    comment="Agent started work on auth middleware refactor.",
)

# Post a standup comment
ado.add_comment(4821, "**Standup update**\nYesterday: reviewed acceptance criteria.\nToday: implementing auth middleware.")

# Open a PR
pr = ado.create_pull_request(
    repository="MyRepo",
    title="ADO #4821 — Auth middleware refactor",
    source_branch="feature/auth-middleware",
    target_branch="main",
    description="Closes #4821.  See PR description for details.",
    work_item_ids=[4821],
)
# → PullRequest(id=112, url="https://tfs.company.com/…/pullrequest/112", …)
```

### `connector_from_config`

```python
from connectors.ado import connector_from_config
ado = connector_from_config(config)  # reads ADO_* env vars via AppConfig
```

---

## Teams Connector

### What it does

- Posts **standup reports** as Adaptive Cards to a Teams channel.
- Sends **notifications** (task complete, blocked, alert) as formatted cards.
- Optionally **reads recent messages** via Graph API for bidirectional use.

### Two modes

| Mode | Config needed | Capability |
|---|---|---|
| **Webhook** (simple) | `TEAMS_WEBHOOK_URL` only | Send only — no app registration |
| **Graph** (full) | `MS_TENANT_ID/CLIENT_ID/SECRET` + `TEAMS_TEAM_ID/CHANNEL_ID` | Send + read |

Start with webhook mode — it requires nothing beyond pasting a URL.

### Setup — Webhook mode

1. In Teams: open the channel → `···` → *Connectors* → *Incoming Webhook* → Configure.
2. Copy the webhook URL.

```ini
TEAMS_WEBHOOK_URL=https://company.webhook.office.com/webhookb2/…
```

### Setup — Graph mode (optional, for reading messages)

Register an Azure AD app (see [Microsoft Graph setup](#microsoft-graph-setup)), then add:

```ini
MS_TENANT_ID=<tenant-guid>
MS_CLIENT_ID=<app-guid>
MS_CLIENT_SECRET=<secret>
TEAMS_TEAM_ID=<team-guid>
TEAMS_CHANNEL_ID=<channel-guid>
```

Required permissions: `ChannelMessage.Send`, `ChannelMessage.Read.All` (application).

### API

```python
from connectors import TeamsConnector

teams = TeamsConnector(webhook_url="https://…")

# Simple text
teams.send_message("Auth refactor PR is open for review.", title="✅ Task complete")

# Notification card (urgency-coloured)
teams.send_notification(
    title="Blocked on Story #4851",
    body="Is the idempotency key client- or server-generated?",
    urgency="high",
    task_id="t_abc123",
    persona="react_developer",
)

# Standup report card
teams.send_standup_report([
    {
        "persona": "react_developer",
        "yesterday": "Completed ADO #4821 — auth middleware.  PR #112 open.",
        "today": "Working on ADO #4834 — CartService unit tests.",
        "blockers": "",
        "three_amigos": ["ADO #4851 — payment idempotency (requirements unclear)"],
    },
    {
        "persona": "dotnet_developer",
        "yesterday": "Fixed ADO #4798 — NullRef in OrderService.",
        "today": "ADO #4812 — adding xUnit tests for OrderController.",
        "blockers": "Waiting for DB migration script from DevOps.",
        "three_amigos": [],
    },
])
```

---

## Outlook Connector

### What it does

- Sends **standup summaries** as formatted HTML emails.
- Sends **agent-to-human messages** (blocked questions, task completions).
- Reads **unread replies** so agents can process human answers.

### Setup

#### 1. Microsoft Graph setup

Register an Azure AD app for all Graph-based connectors (Teams Graph + Outlook):

1. Azure Portal → **Azure Active Directory** → *App registrations* → *New registration*.
2. Name: `natl-agent`, Account type: *Single tenant*.
3. Under *API permissions* → *Add a permission* → *Microsoft Graph* → *Application permissions*:
   - `Mail.Send`
   - `Mail.Read`
   - `Mail.ReadWrite` (needed for `mark_as_read`)
   - `ChannelMessage.Send` (if also using Teams Graph mode)
   - `ChannelMessage.Read.All` (if reading Teams messages)
4. Click **Grant admin consent**.
5. Under *Certificates & secrets* → *New client secret* — copy the value immediately.
6. Note the **Tenant ID** (Directory ID) and **Application (client) ID**.

#### 2. Set environment variables

```ini
MS_TENANT_ID=<tenant-guid>
MS_CLIENT_ID=<app-guid>
MS_CLIENT_SECRET=<secret-value>
OUTLOOK_SENDER=natl-agent@company.com
OUTLOOK_REPLY_TO=natl-agent@company.com     # optional
OUTLOOK_STANDUP_RECIPIENTS=team@company.com,manager@company.com
```

The `OUTLOOK_SENDER` mailbox must exist in your tenant and the app must have
`Mail.Send` permission granted for it (or admin consent for all users).

### API

```python
from connectors import OutlookConnector

outlook = OutlookConnector(
    tenant_id="…", client_id="…", client_secret="…",
    sender="natl-agent@company.com",
)

# Send standup email
outlook.send_standup_email(
    recipients=["team@company.com"],
    entries=[{"persona": "react_developer", "yesterday": "…", "today": "…", "blockers": ""}],
)

# Send a question
outlook.send_email(
    to="dev@company.com",
    subject="🔒 Blocked: Story #4851 — idempotency key",
    body="<p>Which service generates the idempotency key — client or server?</p>",
)

# Read unread replies
emails = outlook.get_unread_emails(subject_contains="blocked")
for email in emails:
    print(email.sender_email, email.subject, email.body[:100])
    outlook.mark_as_read(email.id)
```

---

## Notification dispatch integration

All three connectors are wired into `notification_dispatch.py`.  When an outbox
message meets the urgency threshold, it is automatically dispatched to every
configured channel:

| Channel | Activated when |
|---|---|
| Generic webhooks | `NOTIFICATION_WEBHOOKS` set |
| Teams | `TEAMS_WEBHOOK_URL` or `MS_TENANT_ID` + Teams credentials set |
| Outlook | `OUTLOOK_SENDER` + `MS_TENANT_ID` credentials + `OUTLOOK_STANDUP_RECIPIENTS` set |
| OS toast | `NOTIFICATION_OS_TOAST=true` |

No code changes needed — configure the env vars and notifications flow automatically.

---

## Health checks

```bash
natl connectors status     # print health of all configured connectors

# or via API:
GET /api/connectors/status
```

```json
[
  {"name": "ado",     "enabled": true,  "healthy": true,  "error": ""},
  {"name": "teams",   "enabled": true,  "healthy": true,  "error": ""},
  {"name": "outlook", "enabled": false, "healthy": false, "error": "OUTLOOK_SENDER not configured"}
]
```

---

## Environment variable reference

| Variable | Connector | Description |
|---|---|---|
| `ADO_URL` | ADO | Base URL (cloud or on-prem) |
| `ADO_PAT` | ADO | Personal Access Token |
| `ADO_PROJECT` | ADO | Project name |
| `ADO_TEAM` | ADO | Team name |
| `ADO_API_VERSION` | ADO | REST API version (default `7.1`) |
| `ADO_ASSIGNEES` | ADO | Comma-separated emails for work item filter |
| `MS_TENANT_ID` | Teams + Outlook | Azure AD tenant GUID |
| `MS_CLIENT_ID` | Teams + Outlook | Azure AD app (client) GUID |
| `MS_CLIENT_SECRET` | Teams + Outlook | Azure AD client secret |
| `TEAMS_WEBHOOK_URL` | Teams | Incoming webhook URL (webhook mode) |
| `TEAMS_TEAM_ID` | Teams | Teams team GUID (Graph mode) |
| `TEAMS_CHANNEL_ID` | Teams | Teams channel GUID (Graph mode) |
| `OUTLOOK_SENDER` | Outlook | Service mailbox UPN |
| `OUTLOOK_REPLY_TO` | Outlook | Optional reply-to address |
| `OUTLOOK_STANDUP_RECIPIENTS` | Outlook | Comma-separated email recipients |
