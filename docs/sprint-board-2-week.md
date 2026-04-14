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

- [ ] **S15: Operator control plane hardening**
  - **Goal:** Give operators deterministic control during incidents.
  - **Accept:** Add pause/resume, drain, and maintenance mode semantics (CLI + API), with clear status reflection.
  - **Measure:** Incident playbook actions complete in under 1 minute with expected transitions.

- [ ] **S16: Regression gate for core flows (CI-grade)**
  - **Goal:** Prevent regressions in scheduler/task/status/idempotency core.
  - **Accept:** A slim core suite test target exists and is required before merge.
  - **Measure:** Every PR runs core suite; regressions are caught pre-merge.

- [ ] **S17: OpenClaw surface contract foundation**
  - **Goal:** Define normalized ingress event/session/routing contracts without changing core runtime behavior.
  - **Accept:** Architecture doc + contract examples + boundary policy are merged and cross-linked.
  - **Measure:** Adapter teams can build against a stable contract with no core-loop edits.

- [ ] **S18: Single-channel ingress MVP bridge**
  - **Goal:** Map one inbound channel path into existing task/inbox primitives.
  - **Accept:** One canary channel can create actionable task/inbox outcomes through normalized ingress.
  - **Measure:** End-to-end ingestion success and idempotency checks pass in integration tests.

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
- [ ] *(empty)*
- [ ] *(empty)*

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
- [OpenClaw Surface Rollout](./openclaw-surface-rollout.md)
