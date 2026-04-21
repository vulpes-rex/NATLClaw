# Cerebrum

> OpenWolf's learning memory. Updated automatically as the AI learns from interactions.
> Do not edit manually unless correcting an error.
> Last updated: 2026-04-16

## User Preferences

<!-- How the user likes things done. Code style, tools, patterns, communication. -->

## Key Learnings

- `%-d` strftime format (no-pad day) is Linux-only. On Windows use `%d` or build the string with `f"{dt.day}"` directly. This affected connectors/teams.py and connectors/outlook.py.
- Outlook tests should patch `conn._token` directly (not `urllib.request.urlopen` for token fetch) because `GraphTokenCache` is a module-level singleton — tests run in the same process share the cache and can starve each other's mock side_effects.
- **Project:** natlclaw
- **Description:** NATLClaw is a framework for building autonomous agents with memory, learning, and goal-tracking capabilities. It implements a "second brain" architecture inspired by Tiago Forte's PARA method, allowin

## Do-Not-Repeat

<!-- Mistakes made and corrected. Each entry prevents the same mistake recurring. -->
<!-- Format: [YYYY-MM-DD] Description of what went wrong and what to do instead. -->

- [2026-04-16] When adding keyword-only args (`inbox=`, `outbox=`) to `run_heartbeat`, mock functions in tests that patch it via `side_effect=` must accept `**_kw` or the exact new kwargs — otherwise the coordinator logs "unexpected keyword argument" and the test fails silently at the assertion level.
- [2026-04-16] When adding a new `bool` field to `AppConfig`, any test fixture using `MagicMock(spec=AppConfig)` returns a truthy mock for that attribute — NOT the default False. Always explicitly set the new field to its default value in all such fixtures (especially in test_workflow_modes.py's `config()` fixture). If you forget, tests that are gated on the new bool will silently activate the new path.
- [2026-04-16] Adding `from some_module import foo` *inside a function body* causes Python to treat `foo` as a local name for the *entire* function scope. If `foo` is also referenced *earlier* in the function (before the import runs), Python raises `UnboundLocalError`. In `scheduler.py`, use aliased imports (`from messaging import create_message as _create_message`) or reference the already-existing module-level name instead of re-importing.
- [2026-04-16] When a new `int`/`float` field is added to `AppConfig`, `MagicMock(spec=AppConfig)` returns a `MagicMock` for that attribute, which breaks arithmetic comparisons. Guard with `int(getattr(config, "field", default))` wrapped in try/except in the scheduler, rather than relying on the caller to set the right type.
- [2026-04-16] Windows console (cp1252) can't encode em-dash `—` (U+2014) or right arrow `→` (U+2192). Use ASCII alternatives (`-` and `->`) in any string that gets printed to stdout on Windows.

## Key Learnings

- Move B task negotiation: new lifecycle states `negotiating` (pending→accepted/redirected) sit between `pending` and `in_progress`. Guard the negotiation gate with `config.task_negotiation_enabled` (default False) so existing flows are unchanged.
- `HandoffContext` in handoff.py is a typed payload coordinator passes to sub-tasks via `task.handoff_context` dict. `build_task_context()` injects it as a `== HANDOFF CONTEXT ==` block automatically when present.
- `_parse_negotiation_response()` in workflow.py is a pure function (testable without agent mock) that converts agent text to `{action, to_persona, reason}`.

## Decision Log

<!-- Significant technical decisions with rationale. Why X was chosen over Y. -->

- [2026-04-16] Coordinator maturity (task deps, routing, file locks) is fully in-process. External coordinator-mcp deferred until real cross-process contention is observed.
- [2026-04-16] Notification dispatch uses stdlib `urllib` + `asyncio.to_thread` (no new deps). `plyer` for OS toast is optional and fails gracefully.
- [2026-04-16] `run_heartbeat` uses keyword-only `inbox`/`outbox` so existing positional callers are unaffected.
- [2026-04-16] Task negotiation (`task_negotiation_enabled=False` default) opted for a config flag rather than per-task field to keep the existing assign→start path unchanged for all current users. The `negotiating` state is inserted between `pending` and `in_progress` only when the flag is enabled.
