# NATLClaw 2-Week Sprint Board (v3)

## Backlog

- _(empty)_


## In Progress (WIP Limit: 3)

- _(empty)_

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
- [x] **S14: Scheduler backpressure + bounded work per heartbeat**
- [x] **S15: Operator control plane hardening**
- [x] **S17: OpenClaw surface contract foundation**
- [x] **S18: Single-channel ingress MVP bridge**
- [x] **S19: Session/routing observability**
- [x] **S20: Surface hardening + staged rollout**
- [x] **S13: Persistence integrity + crash consistency**
- [x] **S16: Regression gate for core flows (CI-grade)**

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
