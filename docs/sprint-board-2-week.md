# NATLClaw 2-Week Sprint Board (v3)

## Backlog

- [ ] **S13: Persistence integrity + crash consistency**
  - **Goal:** Ensure state/task/outbox/brain remain mutually consistent across crashes/restarts.
  - **Accept:** Atomic write + recovery invariants are enforced and tested under kill/restart scenarios.
  - **Measure:** 0 corrupted/partial state in fault-injection restart tests.
  - **Progress:**
    - [x] Added fault-injection tests for atomic persistence (`state`, `tasks`, `outbox`) under replace failures (`tests/integration/test_persistence_crash_consistency.py`).
    - [x] Added cross-file restart/reload consistency assertions after mixed simulated crash paths (`tests/integration/test_persistence_crash_consistency.py`).
    - [ ] Extend crash-consistency coverage to brain persistence and scheduler-driven restart paths.

- [ ] **S14: Scheduler backpressure + bounded work per heartbeat**
  - **Goal:** Prevent overload when events/tasks spike.
  - **Accept:** Per-heartbeat work caps, queue depth visibility, and graceful spillover behavior.
  - **Measure:** p95 heartbeat duration remains under target under burst load; no unbounded lag growth.
  - **Progress:**
    - [x] Added bounded per-heartbeat event draining with spillover semantics in scheduler (`scheduler.py`).
    - [x] Added queue/backpressure runtime visibility surfaced through operator status (`operator_status.py`, `cli.py`).
    - [x] Added burst-load integration validation for bounded spillover behavior (`tests/integration/test_scheduler_soak.py`).

- [ ] **S15: Operator control plane hardening**
  - **Goal:** Give operators deterministic control during incidents.
  - **Accept:** Add pause/resume, drain, and maintenance mode semantics (CLI + API), with clear status reflection.
  - **Measure:** Incident playbook actions complete in under 1 minute with expected transitions.
  - **Progress:**
    - [x] Added persistent scheduler control state with pause/resume/drain/maintenance flags (`scheduler_control.py`).
    - [x] Added API control endpoints for pause/resume/drain/maintenance and reflected state in scheduler status (`api_server.py`).
    - [x] Added CLI control commands (`natl scheduler status|pause|resume|drain|maintenance-enable|maintenance-disable`) with operator status reflection (`cli.py`, `operator_status.py`).
    - [x] Added unit/integration tests for control semantics and API behavior (`tests/unit/test_scheduler.py`, `tests/integration/test_api_server.py`).

- [ ] **S16: Regression gate for core flows (CI-grade)**
  - **Goal:** Prevent regressions in scheduler/task/status/idempotency core.
  - **Accept:** A slim core suite test target exists and is required before merge.
  - **Measure:** Every PR runs core suite; regressions are caught pre-merge.
  - **Progress:**
    - [x] Added a slim core suite manifest and runner target (`core_suite_tests.txt`, `run_core_suite.py`).
    - [x] Added CI workflow to execute core suite on PRs and `master` pushes (`.github/workflows/core-suite.yml`).
    - [ ] Wire branch protection to require `Core Regression Suite` before merge.

- [ ] **S17: OpenClaw surface contract foundation**
  - **Goal:** Define normalized ingress event/session/routing contracts without changing core runtime behavior.
  - **Accept:** Architecture doc + contract examples + boundary policy are merged and cross-linked.
  - **Measure:** Adapter teams can build against a stable contract with no core-loop edits.
  - **Progress:**
    - [x] Confirmed normalized architecture contract and machine-readable schema foundations (`openclaw-surface-architecture.md`, `surface-event-v1.schema.json`).
    - [x] Added dedicated contract examples guide for adapter/session/routing implementation (`openclaw-surface-contract-examples.md`).
    - [x] Added explicit boundary/ownership policy doc with enforcement checklist (`openclaw-surface-boundary-policy.md`).
    - [x] Cross-linked architecture, adoption, MVP, and session/routing docs to contract examples and boundary policy.

- [ ] **S18: Single-channel ingress MVP bridge**
  - **Goal:** Map one inbound channel path into existing task/inbox primitives.
  - **Accept:** One canary channel can create actionable task/inbox outcomes through normalized ingress.
  - **Measure:** End-to-end ingestion success and idempotency checks pass in integration tests.
  - **Progress:**
    - [x] Added `POST /api/surface/events` ingress endpoint with `surface-event-v1` validation and canary adapter allowlist checks (`api_server.py`, `surface_ingress.py`).
    - [x] Bridged normalized route decisions to existing task/inbox primitives with scheduler wake signaling for task outcomes (`surface_ingress.py`, `event_watcher.py`).
    - [x] Added idempotency key replay handling (`accepted_noop`) and conflict detection for mismatched payload reuse (`surface_ingress.py`).
    - [x] Added integration coverage for create-task path, inbox path, duplicate no-op idempotency, and invalid payload rejection (`tests/integration/test_api_server.py`).

- [ ] **S19: Session/routing observability**
  - **Goal:** Make session identity and routing decisions visible to operators.
  - **Accept:** Session and route status are queryable and traceable in logs/API.
  - **Measure:** Operator can trace inbound event -> route decision -> task/inbox result.

- [ ] **S20: Surface hardening + staged rollout**
  - **Goal:** Safely roll out channel surfaces with rollback controls.
  - **Accept:** Feature-flag rollout, soak/backpressure tests, and incident playbook are documented.
  - **Measure:** Canary rollout can be enabled/disabled in minutes without scheduler degradation.

## In Progress (WIP Limit: 3)

- [ ] **S13: Persistence integrity + crash consistency**
- [ ] **S14: Scheduler backpressure + bounded work per heartbeat**
- [ ] **S15: Operator control plane hardening**
- [ ] **S18: Single-channel ingress MVP bridge**
- [ ] **S16: Regression gate for core flows (CI-grade)**

## Done

- [x] **S1: Scheduler reliability baseline**
- [x] **S2: Event wake-up correctness**
- [x] **S3: Task lifecycle determinism**
- [x] **S4: Blocked-task round trip**
- [x] **S5: Inbox signal quality**
- [x] **S6: Operator health snapshot**
- [x] **S7: Failure classification + metrics**
- [x] **S8: Runbook docs**
- [x] **S9: Long-run reliability soak**
- [x] **S10: Event/task idempotency hardening**
- [x] **S11: Project context accuracy**
- [x] **S12: Task throughput + SLA controls**

## Suggested Execution Order (2 Weeks)

- **Week 1:** `S13 -> S14`
- **Week 2:** `S15 -> S16`

## Suggested Execution Order (Surface Track)

- **Surface Week 1:** `S17 -> S18`
- **Surface Week 2:** `S19 -> S20`

## Daily Standup Template

- **Yesterday:** What moved to Done.
- **Today:** Top 1 card from In Progress.
- **Blockers:** Only items preventing completion today.
- **Scope check:** Any new idea goes to Backlog, not In Progress, unless replacing a current card.

## Definition of Done (Per Card)

- [ ] Behavior is tested (unit/integration as appropriate).
- [ ] Failure path is handled (not just happy path).
- [ ] Logs/metrics make outcome visible.
- [ ] No regression to scheduler/task core flows.
- [ ] Clear operator-facing outcome (CLI/API/inbox).
- [ ] Surface/AgentOps work includes boundary guardrail checks and attached DoD evidence artifacts.

### Boundary Checklist Reference (Surface/AgentOps Cards)

- Ensure no persona/provider/channel-specific task logic is hardcoded in core loops.
- Ensure adapter logic remains in adapter/surface modules and writes through contracts only.
- Ensure routing emits generic intents; persona definitions own domain behavior.
- Ensure fail-open behavior is validated when optional surfaces are unavailable.
- Ensure evidence package links are attached before moving card to Done.

## Surface Track References

- [OpenClaw Surface Adoption Plan](./openclaw-surface-adoption-plan.md)
- [OpenClaw Surface Architecture](./openclaw-surface-architecture.md)
- [OpenClaw Surface Contract Examples](./openclaw-surface-contract-examples.md)
- [OpenClaw Surface Boundary Policy](./openclaw-surface-boundary-policy.md)
- [OpenClaw Surface Rollout](./openclaw-surface-rollout.md)
