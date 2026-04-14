# NATLClaw Operator Runbook

This runbook is the fast path for day-to-day operation, recovery, and task triage.

Use either `natl ...` (installed CLI) or `python cli.py ...` from repo root.

## 1) Daily Quick Check (30 seconds)

Run:

```bash
natl status
```

Look for:

- `Scheduler: RUNNING`
- `Heartbeat: active` and reasonable `seconds_ago`
- `Active task` (or `none`)
- `Blocked tasks` count
- `Inbox unread` and `needs response` count
- `Top error types` (for immediate troubleshooting direction)

If you need structured JSON:

```bash
curl http://localhost:8000/api/status
```

## 2) Start / Stop / Verify Runtime

### Start scheduler loop

```bash
natl run
```

### Run one heartbeat only

```bash
natl run --once
```

### Start API + dashboard

```bash
natl serve
```

Default endpoints:

- Dashboard: `http://localhost:8000/`
- API health: `http://localhost:8000/api/health`
- Operator snapshot: `http://localhost:8000/api/status`

### Scheduler control via API

```bash
curl -X POST http://localhost:8000/api/scheduler/start
curl -X POST http://localhost:8000/api/scheduler/stop
curl http://localhost:8000/api/scheduler/status
```

## 3) Task Triage Workflow

### See task board

```bash
natl task list
natl task list -s blocked
```

### Inspect one task

```bash
natl task status <task_id>
```

### Unblock a task (standard path)

```bash
natl task answer <task_id> "your answer"
```

After answering, verify in next cycle:

- `natl status` -> blocked count decreases
- `natl task status <task_id>` -> status moves from `blocked` to `assigned`/`in_progress`

### Cancel or retry

```bash
natl task cancel <task_id> --reason "why"
natl task retry <task_id>
```

## 4) Inbox / Notification Triage

### Check unread notifications

```bash
natl inbox list
```

### Read details (marks read)

```bash
natl inbox show <message_id>
```

### Dismiss noise once handled

```bash
natl inbox dismiss <message_id>
natl inbox dismiss -a
```

Note: message dedupe is enabled for active unread/read equivalents. Dismissed messages can reappear later if the same issue recurs.

## 5) Failure Triage by Error Type

Use:

```bash
natl status
```

Focus on `Top error types` and latest error.

### `timeout`

- Usually long-running tool/model call or hung operation.
- Actions:
  - Retry one cycle: `natl run --once`
  - Check recent heartbeat activity: `GET /api/heartbeat/activity`
  - Reduce workload size for current task.

### `auth`

- Missing/invalid credentials/API keys/session.
- Actions:
  - `natl config show` (verify required fields are present)
  - Re-auth provider/session (for copilot/openai/etc.)
  - Retry affected task.

### `network`

- Connectivity/DNS/refused connections.
- Actions:
  - Verify local network/proxy/VPN
  - Check target endpoint reachability
  - Retry once connectivity is restored.

### `io` / `state`

- File/database/path/permission/state store issues.
- Actions:
  - Confirm workspace write permissions
  - Verify data files exist and are not locked
  - Check disk space / path validity.

### `validation` / `tooling`

- Payload/schema/parsing issues or tool-call failures.
- Actions:
  - Inspect last error preview from `natl status`
  - Inspect task/inbox context for malformed input
  - Retry after correcting input.

## 6) Recovery: Scheduler Lock / Stuck Runtime

Use scheduler status:

```bash
curl http://localhost:8000/api/scheduler/status
```

It includes lock diagnostics (`exists`, `pid`, `pid_alive`, `stale`, `age_sec`).

### If scheduler appears stuck

1. Stop via API:

```bash
curl -X POST http://localhost:8000/api/scheduler/stop
```

1. Re-check status and lock.
2. Start fresh:

```bash
curl -X POST http://localhost:8000/api/scheduler/start
```

Stale/malformed lock files are auto-recovered on startup.

## 7) Escalation Checklist (when issues persist)

Capture before asking for help:

- Output of `natl status`
- Output of `/api/scheduler/status`
- Task details: `natl task status <task_id>`
- Recent activity: `GET /api/heartbeat/activity`
- Any unread alerts/questions: `natl inbox list`

This is the minimum context required for fast debugging.

## 8) OpenClaw Surface Operations (when enabled)

Surface features are optional and must be explicitly enabled via flags.

Primary flags:

- `SURFACE_INGRESS_ENABLED`
- `SURFACE_ROUTING_ENABLED`
- `SURFACE_SESSIONS_ENABLED`
- `SURFACE_CHANNELS_ENABLED`

Operator policy:

1. Enable one canary channel first.
2. Verify ingress and queue health before enabling routing.
3. Enable additional channels one by one.

If incidents occur:

- Ingress parsing incidents: disable affected adapter from `SURFACE_CHANNELS_ENABLED`.
- Routing incidents: disable `SURFACE_ROUTING_ENABLED` first.
- Scheduler/lock incidents: disable `SURFACE_INGRESS_ENABLED` and stabilize scheduler.

See full guidance:

- [OpenClaw Surface Architecture](./openclaw-surface-architecture.md)
- [OpenClaw Surface Rollout](./openclaw-surface-rollout.md)

