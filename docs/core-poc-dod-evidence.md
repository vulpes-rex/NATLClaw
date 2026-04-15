# Core POC DoD Evidence Pack

This document is the single evidence index for the Core NATLClaw POC Definition of Done in `docs/spec.md`.

## DoD Criteria Mapping

| DoD criterion | Evidence |
| --- | --- |
| `python main.py` runs 3+ heartbeat cycles without errors | `tests/unit/test_scheduler.py::test_run_scheduler_runs_three_cycles_without_errors` and smoke runner `poc_smoke.py` |
| State persists across restart and execution count continues | `tests/unit/test_state.py::test_execution_count_continues_across_restart_cycles` |
| Lessons from previous heartbeats appear in subsequent context | `tests/unit/test_learning_calibration.py::TestContextBlockCalibration::test_previous_lessons_appear_in_new_context_block` |
| Provider switch works by config (`.env`/env vars) only | `tests/unit/test_copilot_auth.py::TestConfigIntegration::test_provider_switch_uses_env_only_for_openai` and `tests/unit/test_copilot_auth.py::TestConfigIntegration::test_provider_switch_uses_env_only_for_ollama` |
| Ctrl+C clean shutdown with state saved | `tests/unit/test_scheduler.py::test_run_scheduler_handles_keyboard_interrupt` |

## Core Regression Gate Inclusion

The core regression manifest includes all Core POC criteria tests:
- `core_suite_tests.txt`
- `run_core_suite.py`

## Deterministic Smoke Validation

Run:

`python poc_smoke.py --output artifacts/poc-smoke-evidence.json --state-file artifacts/poc-smoke-state.json`

Expected:
- Exit code `0`
- `artifacts/poc-smoke-evidence.json` exists
- JSON `checks` values are all `true`

## Verification Commands

Run targeted criteria checks:

`python -m pytest -q tests/unit/test_scheduler.py::test_run_scheduler_runs_three_cycles_without_errors tests/unit/test_scheduler.py::test_run_scheduler_handles_keyboard_interrupt tests/unit/test_state.py::test_execution_count_continues_across_restart_cycles tests/unit/test_learning_calibration.py::TestContextBlockCalibration::test_previous_lessons_appear_in_new_context_block tests/unit/test_copilot_auth.py::TestConfigIntegration::test_provider_switch_uses_env_only_for_openai tests/unit/test_copilot_auth.py::TestConfigIntegration::test_provider_switch_uses_env_only_for_ollama`

Run full core suite gate:

`python run_core_suite.py`

## Sprint Exit Checklist

- [x] Core DoD criteria have explicit automated coverage.
- [x] Criteria tests are part of core regression manifest.
- [x] Deterministic smoke runner exists with artifact output.
- [x] Sprint board cards mapped to Core POC DoD criteria.

## Release Checkpoint (2026-04-14)

Status: Core POC GO for defined scope.

### Recorded results

- Core suite result: `21 passed, 1 warning in 3.31s`
- Provider-switch targeted checks: `2 passed, 1 warning in 0.36s`
- Smoke run:
  - `Smoke artifact written: artifacts/poc-smoke-evidence.json`
  - `Smoke status: PASS`
  - `execution_count= 3`
  - `checks= {'three_cycles_completed': True, 'state_persisted': True, 'lessons_present_in_context': True, 'context_injected_across_cycles': True}`

### Release decision

All Core POC Definition of Done criteria in `docs/spec.md` are satisfied by automated evidence and smoke validation. Core POC is complete for local, POC-defined scope.
