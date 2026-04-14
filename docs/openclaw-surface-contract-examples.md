# OpenClaw Surface Contract Examples

## Purpose

Provide concrete, adapter-friendly examples for `surface-event-v1`, session identity, and routing outcomes so teams can implement against stable contracts without editing core runtime loops.

Primary spec references:

- [OpenClaw Surface Architecture](./openclaw-surface-architecture.md)
- [surface-event-v1 JSON Schema](./surface-event-v1.schema.json)
- [OpenClaw Session and Routing Design](./openclaw-session-routing-design.md)

## 1) Ingress Event Examples (`surface-event-v1`)

### 1.1 Direct Message -> Action Request

```json
{
  "spec_version": "1.0",
  "event_id": "evt_dm_0001",
  "event_type": "message.received",
  "ts": "2026-04-14T20:25:01Z",
  "source": {
    "adapter": "discord",
    "channel_type": "discord",
    "channel_instance": "primary-guild"
  },
  "session": {
    "session_id": "sess_discord_u_42",
    "thread_id": null,
    "user_id": "u_42",
    "group_id": null
  },
  "routing": {
    "persona_hint": "project_manager",
    "priority": "high",
    "requires_reply": true
  },
  "payload": {
    "text": "Please summarize blockers and create follow-up tasks.",
    "attachments": []
  },
  "meta": {
    "trace_id": "trc_0001",
    "idempotency_key": "discord:u_42:msg_8901"
  }
}
```

Expected route (MVP): `create_task`

### 1.2 Group Informational Message

```json
{
  "spec_version": "1.0",
  "event_id": "evt_group_0001",
  "event_type": "message.received",
  "ts": "2026-04-14T20:30:00Z",
  "source": {
    "adapter": "telegram",
    "channel_type": "telegram",
    "channel_instance": "ops-room"
  },
  "session": {
    "session_id": "sess_telegram_grp_ops_room",
    "thread_id": "ops-room",
    "user_id": "u_900",
    "group_id": "g_ops"
  },
  "routing": {
    "persona_hint": null,
    "priority": "normal",
    "requires_reply": false
  },
  "payload": {
    "text": "FYI: CI is green and deployment completed.",
    "attachments": []
  },
  "meta": {
    "trace_id": "trc_0002",
    "idempotency_key": "telegram:g_ops:msg_100"
  }
}
```

Expected route (MVP): `append_inbox_message`

### 1.3 Duplicate Replay (Idempotent No-op)

- First delivery: accepted, routed, effects applied.
- Replay with same `meta.idempotency_key` and same payload hash: treated as already processed.

Expected behavior:

1. Return success/no-op semantics.
2. Do not create duplicate task or duplicate inbox message.

## 2) Session Identity Examples

Recommended stable formats:

- DM: `sess_<channel_type>_<user_id>`
- Group: `sess_<channel_type>_<group_id>`
- Threaded channel: `sess_<channel_type>_<thread_id>`

Examples:

- `sess_discord_u_42`
- `sess_telegram_grp_ops_room`
- `sess_webhook_t_2026_04_14_1`

Session fields (minimum):

```json
{
  "session_id": "sess_discord_u_42",
  "channel_type": "discord",
  "origin_type": "dm",
  "active_persona": "project_manager",
  "state": "active",
  "reply_mode": "auto",
  "last_event_ts": "2026-04-14T20:25:01Z"
}
```

## 3) Routing Decision Examples (`surface-routing-v1`)

### 3.1 Create Task Decision

```json
{
  "route_id": "rte_0001",
  "session_id": "sess_discord_u_42",
  "decision": "create_task",
  "persona": "project_manager",
  "priority": "high",
  "reason": "Actionable request with explicit output expectation",
  "target": {
    "task_title": "Summarize blockers and create follow-up actions",
    "emit_inbox_message": true
  }
}
```

### 3.2 Append Inbox Message Decision

```json
{
  "route_id": "rte_0002",
  "session_id": "sess_telegram_grp_ops_room",
  "decision": "append_inbox_message",
  "persona": "default",
  "priority": "normal",
  "reason": "Informational message without actionable work",
  "target": {
    "message_type": "status",
    "requires_response": false
  }
}
```

### 3.3 Escalate Operator Decision

```json
{
  "route_id": "rte_0003",
  "session_id": "sess_webhook_t_2026_04_14_1",
  "decision": "escalate_operator",
  "persona": "default",
  "priority": "urgent",
  "reason": "Repeated adapter parse failures for same source",
  "target": {
    "alert_code": "surface.route.failure_burst",
    "emit_inbox_message": true
  }
}
```

## 4) Bridge Output Examples to Existing NATLClaw Primitives

| Routing decision         | Existing module target | Expected side effect                                  |
| ------------------------ | ---------------------- | ----------------------------------------------------- |
| `create_task`            | `tasks.py`             | New pending task + scheduler wake signal              |
| `append_inbox_message`   | `messaging.py`         | New inbox/outbox message entry                        |
| `ignore`                 | none                   | Metrics/log only                                      |
| `escalate_operator`      | `messaging.py`         | Alert message and optional status annotation          |

## 5) Contract Compatibility Rules

1. New adapters must emit valid `surface-event-v1` envelopes.
2. Session/routing extensions may add optional fields but must not remove required fields.
3. Contract revisions require a new `spec_version`; `1.0` remains backward-compatible.

## 6) Adapter Implementation Checklist

- Validate payload against `surface-event-v1` schema.
- Populate stable `session.session_id`.
- Provide deterministic `meta.idempotency_key`.
- Never write directly to `tasks.json`, `outbox.json`, `agent_state.json`, or brain stores.
- Emit through surface bridge only.
