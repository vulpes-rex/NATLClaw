# OpenClaw Surface Boundary Policy

## Purpose

Define enforceable boundaries between surface/adapters and NATLClaw core runtime so channel integrations remain decoupled from heartbeat/workflow internals.

Companion docs:

- [OpenClaw Surface Architecture](./openclaw-surface-architecture.md)
- [OpenClaw Surface Contract Examples](./openclaw-surface-contract-examples.md)
- [OpenClaw Session and Routing Design](./openclaw-session-routing-design.md)

## 1) Ownership Model

| Domain                      | Owns write authority                  |
| --------------------------- | ------------------------------------- |
| Heartbeat loop              | `scheduler.py`                        |
| Workflow execution          | `workflow.py`                         |
| Task lifecycle              | `tasks.py`                            |
| Operator messaging          | `messaging.py`                        |
| Brain knowledge persistence | `second_brain.py`                     |
| Surface normalization       | surface ingress + routing modules     |
| Channel protocol adapters   | adapter modules only                  |

## 2) Required Boundary Rules

1. Adapters write only normalized envelopes into surface ingress APIs/contracts.
2. Surface routing emits generic intent classes (`create_task`, `append_inbox_message`, `ignore`, `escalate_operator`).
3. Core runtime remains persona/provider/channel agnostic in implementation.
4. Surface outages must fail-open and never block core heartbeat progression.
5. Scheduler remains the only authority for heartbeat cycle execution.

## 3) Explicitly Forbidden Patterns

- Adapter writes directly to core persistence artifacts:
  - `agent_state.json`
  - `tasks.json`
  - `outbox.json`
  - brain JSON/SQLite stores
- Core module branches on adapter name, channel type, or provider protocol semantics.
- Persona domain logic hardcoded into core loops (for example dedicated persona `if` branches in scheduler/workflow).

## 4) Dependency Direction

Allowed:

- `adapter -> surface contract -> scheduler queue -> existing modules`

Disallowed:

- `scheduler -> adapter module internals`
- `adapter -> direct core storage mutation`

## 5) Test-Enforced Guardrails

Before enabling any new channel:

1. Contract conformance fixtures pass for adapter payload normalization.
2. Idempotency replay tests prove no duplicate task/inbox side effects.
3. Boundary checks confirm no forbidden imports/writes.
4. Scheduler core suite remains green.

## 6) Change Control Policy

Any surface-related PR must include:

- Boundary impact statement (what changed, what did not).
- Contract version compatibility notes.
- Evidence of unchanged core-loop behavior with flags disabled.

## 7) Operational Policy

- Surface feature flags default to disabled.
- Rollout is staged (canary -> partial -> full).
- Rollback must not require runtime code edits; flags and config toggles only.
