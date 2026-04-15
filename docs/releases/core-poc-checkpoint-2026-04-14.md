# Core POC Release Checkpoint (2026-04-14)

This checkpoint records the successful Core POC gate run and release decision.

## Gate outcomes

- Core suite: `21 passed, 1 warning in 3.31s`
- Targeted provider-switch checks: `2 passed, 1 warning in 0.36s`
- Smoke run:
  - `Smoke artifact written: artifacts/poc-smoke-evidence.json`
  - `Smoke status: PASS`
  - `execution_count= 3`
  - `checks= {'three_cycles_completed': True, 'state_persisted': True, 'lessons_present_in_context': True, 'context_injected_across_cycles': True}`

## Scope statement

Release applies to Core NATLClaw POC scope as defined in `docs/spec.md` and `docs/requirements.md` (local-first, single-agent POC).

## Decision

GO: Core POC is done for defined scope.