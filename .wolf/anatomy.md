# anatomy.md

> Auto-maintained by OpenWolf. Last scanned: 2026-04-17T00:14:21.104Z
> Files: 590 tracked | Anatomy hits: 0 | Misses: 0

## ./

- `_build_graph.py` — Build graphify knowledge graph for NATLClaw. (~711 tok)
- `.gitignore` — Git ignore rules (~5204 tok)
- `.mcp.json` (~128 tok)
- `ado_sync.py` — Bidirectional ADO ↔ NATLClaw sync — Feature B (ado_sync.py). (~3540 tok)
- `agent_setup.py` — permission_handler, create_agent (~3207 tok)
- `api_server.py` — OpenAI-compatible API server for NATLClaw. (~28006 tok)
- `brain_index.py` — Semantic search index for brain notes. (~2055 tok)
- `capture_policy.py` — Capture behavior for heartbeat/task notes — data-driven, not persona-name checks. (~1107 tok)
- `CLAUDE.md` — OpenWolf (~57 tok)
- `cli.py` — NATLClaw CLI — command-line interface with subcommands. (~30665 tok)
- `config.py` — class: validate_config, load_config (~3635 tok)
- `conftest.py` — Root conftest — ensure a fresh event loop is available for legacy tests (~277 tok)
- `CONTRIBUTING.md` — Contributing to NATLClaw (~373 tok)
- `copilot_auth.py` — GitHub Copilot token exchange for CLI usage. (~1316 tok)
- `core_agent_tools.py` — Core agent tools merged with every persona’s extension tools (unless opted out). (~482 tok)
- `core_suite_tests.txt` (~429 tok)
- `daily_digest.py` — Daily digest / morning briefing generator. (~2184 tok)
- `decision_engine.py` — Decision engine for NATLClaw. (~14370 tok)
- `dump_brain.py` — Quick script to dump brain contents. (~200 tok)
- `error_classification.py` — classify_error_text, top_error_types (~399 tok)
- `event_config.py` — Event priority configuration. (~132 tok)
- `event_watcher.py` — Lightweight workspace event watcher. (~6493 tok)
- `execution_log.py` — SQLite-backed execution log. (~1817 tok)
- `file.html` — Example Domain (~141 tok)
- `goals.py` — Goal / task management for multi-heartbeat objectives. (~1502 tok)
- `graphifyy-0.4.1-py3-none-any.whl` (~59084 tok)
- `handoff.py` — Structured handoff context for coordinator task delegation (Move B). (~1447 tok)
- `inbox.json` (~1 tok)
- `ingest.py` — External knowledge ingestion pipeline. (~1770 tok)
- `learning.py` — Lesson extraction with CodeIntel-style FP/TP calibration. (~3494 tok)
- `LICENSE` — Project license (~286 tok)
- `main.py` — main (~221 tok)
- `MANIFEST.in` — Include all text files in prompts and personas directories (~84 tok)
- `mcp.json` (~5333 tok)
- `mcp.schema.json` — Declares names (~1885 tok)
- `messaging.py` — Outbox messaging system for the coworker interaction model. (~8539 tok)
- `metrics.py` — Structured metrics collection for NATLClaw. (~1685 tok)
- `notification_dispatch.py` — Proactive notification dispatch — push outbox messages to multiple channels. (~2631 tok)
- `nul` (~25 tok)
- `operator_status.py` — build_operator_status (~2422 tok)
- `outbox.json` (~1 tok)
- `persona_loader.py` — Load personas and MCP server configs from mcp.json. (~8498 tok)
- `persona.schema.json` — Declares name (~1399 tok)
- `poc_smoke.py` — run_smoke, main (~2201 tok)
- `preference_feedback.py` — Inbox actions → brain relevance feedback (coworker preference learning). (~794 tok)
- `project_context.py` — Project context detection for NATLClaw. (~3952 tok)
- `prompts.py` — Prompt template loader for workflow steps. (~827 tok)
- `pyproject.toml` — Python project configuration (~454 tok)
- `pytest.ini` (~209 tok)
- `README.md` — Project documentation (~1427 tok)
- `requirements.txt` — Python dependencies (~12 tok)
- `run_core_suite.py` — main (~487 tok)
- `scheduler_control.py` — URL configuration (~987 tok)
- `scheduler.py` — URL configuration (~11945 tok)
- `second_brain.py` — URL configuration (~39369 tok)
- `setup.py` — Python package setup (~437 tok)
- `sprint_context.py` — Sprint context injection — Feature F. (~3000 tok)
- `standup.py` — Standup protocol — per-persona daily standup entries and team report. (~3878 tok)
- `state.py` — URL configuration (~1120 tok)
- `surface_ingress.py` — Surface ingress bridge for S18 single-channel MVP. (~6511 tok)
- `tasks.json` (~1 tok)
- `tasks.py` — Task queue for the coworker interaction model. (~6131 tok)
- `telemetry.py` — init_sentry, send_test_exception, start_sentry_profiler, stop_sentry_profiler (~1158 tok)
- `test_integration.py` — Integration tests — real component interactions, real files, minimal mocking. (~16794 tok)
- `test_learning_loop.py` — Learning-loop validation tests. (~9249 tok)
- `test_output.txt` — Declares collected (~647 tok)
- `test_results.txt` — Declares collecting (~383 tok)
- `test_results2.txt` — Declares collecting (~20442 tok)
- `test_results3.txt` — Declares collecting (~455 tok)
- `test_state.json` (~3208 tok)
- `test_x.json` (~50 tok)
- `test.json` (~55 tok)
- `tmp0nc1_3mv.tmp` (~464 tok)
- `workflow.py` — run_heartbeat, run_task_heartbeat (~20025 tok)

## .claude/

- `mcp.json` (~128 tok)
- `settings.json` (~638 tok)
- `settings.local.json` (~188 tok)

## .claude/rules/

- `openwolf.md` (~313 tok)

## .cursor/

- `settings.json` (~20 tok)

## .github/workflows/

- `core-suite.yml` — CI: Core Regression Suite (~169 tok)

## .pytest_cache/

- `.gitignore` — Git ignore rules (~11 tok)
- `CACHEDIR.TAG` (~51 tok)
- `README.md` — Project documentation (~78 tok)

## .pytest_cache/v/cache/

- `lastfailed` (~302 tok)
- `nodeids` (~40518 tok)

## .venv/

- `.gitignore` — Git ignore rules (~1 tok)
- `pyvenv.cfg` (~126 tok)

## .venv/Include/site/python3.13/greenlet/

- `greenlet.h` — ifndef Py_GREENLETOBJECT_H (~1406 tok)

## .venv/Lib/site-packages/

- `__editable___natlclaw_0_1_0_finder.py` — _EditableFinder: find_spec, find_spec, find_module, install (~1609 tok)
- `__editable__.natlclaw-0.1.0.pth` (~24 tok)
- `_cffi_backend.cp313-win_amd64.pyd` (~47768 tok)
- `81d243bd2c585b0f4821__mypyc.cp313-win_amd64.pyd` (~57782 tok)
- `clr.py` (~28 tok)
- `distutils-precedence.pth` (~41 tok)
- `isympy.py` — main (~3206 tok)
- `py.py` — shim for pylib going away (~94 tok)
- `pythoncom.py` — Magic utility that "redirects" to pythoncomXX.dll (~41 tok)
- `pywin32.pth` — .pth file for the PyWin32 extensions (~50 tok)
- `pywin32.version.txt` (~2 tok)
- `scipy-1.17.1-cp313-cp313-win_amd64.whl` (~0 tok)
- `six.py` — Utilities for writing code that runs on Python 2 and 3 (~9916 tok)
- `threadpoolctl.py` — threadpoolctl (~14492 tok)
- `typing_extensions.py` — _Sentinel: final, done, done, disjoint_base + 1 more (~45837 tok)

## .venv/Lib/site-packages/_distutils_hack/

- `__init__.py` — don't import any costly modules (~1930 tok)
- `override.py` (~13 tok)

## .venv/Lib/site-packages/_pytest/

- `__init__.py` (~112 tok)
- `_argcomplete.py` — Allow bash-completion for argparse with argcomplete if installed. (~1079 tok)
- `_version.py` — file generated by vcs-versioning (~149 tok)
- `cacheprovider.py` — Implementation of the cache provider. (~6614 tok)
- `capture.py` — Per-test stdout/stderr capturing mechanism. (~10523 tok)
- `compat.py` — Python version compatibility code and random general utilities. (~2930 tok)
- `debugging.py` — Interactive debugging with PDB, the Python Debugger. (~3985 tok)
- `deprecated.py` — Deprecation messages and bits of code used elsewhere in the codebase that (~1032 tok)
- `doctest.py` — Discover and run doctests in modules and test files. (~7280 tok)
- `faulthandler.py` — pytest_addoption, pytest_configure, pytest_unconfigure, get_stderr_fileno + 5 more (~1215 tok)
- `fixtures.py` — mypy: allow-untyped-defs (~22481 tok)
- `freeze_support.py` — Provides a function to report all internal modules for using freezing (~372 tok)
- `helpconfig.py` — Version info, help messages, tracing configuration. (~2863 tok)
- `hookspec.py` — Hook specifications for pytest plugins which are invoked by pytest itself (~12292 tok)
- `junitxml.py` — Report test results in JUnit-XML format, for use with Jenkins and build (~7292 tok)
- `legacypath.py` — Add backward compatibility support for the legacy py path type. (~4740 tok)
- `logging.py` — Access and control log capturing. (~10067 tok)
- `main.py` — Core implementation of the testing process: init, session, runtest loop. (~12125 tok)
- `monkeypatch.py` — Monkeypatching and mocking functionality. (~4429 tok)
- `nodes.py` — mypy: allow-untyped-defs (~7583 tok)
- `outcomes.py` — Exception classes and constants handling test outcomes as well as (~2888 tok)
- `pastebin.py` — Submit failure or test session information to a pastebin service. (~1188 tok)
- `pathlib.py` — URL patterns: 1 routes (~10823 tok)
- `py.typed` (~0 tok)
- `pytester_assertions.py` — Helper plugin for pytester; should not be loaded on its own. (~644 tok)
- `pytester.py` — (Disabled by default) support for testing pytest and pytest plugins. (~17826 tok)
- `python_api.py` — mypy: allow-untyped-defs (~9056 tok)
- `python.py` — Python test discovery, setup and run of test functions. (~19644 tok)
- `raises.py` — of: raises, raises, raises, raises + 4 more (~17167 tok)
- `recwarn.py` — Record warnings during test function execution. (~3825 tok)
- `reports.py` — mypy: allow-untyped-defs (~6638 tok)
- `runner.py` — Basic collect and runtest protocol implementations. (~5653 tok)
- `scope.py` — Scope: next_lower, next_higher, from_user (~783 tok)
- `setuponly.py` — pytest_addoption, pytest_fixture_setup, pytest_fixture_post_finalizer, pytest_cmdline_main (~905 tok)
- `setupplan.py` — pytest_addoption, pytest_fixture_setup, pytest_cmdline_main (~339 tok)
- `skipping.py` — Support for skip/xfail functions and markers. (~3089 tok)
- `stash.py` — View: get (~883 tok)
- `stepwise.py` — class: pytest_addoption, pytest_configure, pytest_sessionfinish, last_cache_date + 7 more (~2197 tok)
- `subtests.py` — Builtin plugin that adds subtests support. (~3784 tok)
- `terminal.py` — Terminal reporting of the full testing process. (~18410 tok)
- `terminalprogress.py` — A plugin to register the TerminalProgressPlugin plugin. (~330 tok)
- `threadexception.py` — ThreadExceptionMeta: collect_thread_exception, cleanup, thread_exception_hook, pytest_configure + 3 more (~1416 tok)
- `timing.py` — Indirection for time functions. (~888 tok)
- `tmpdir.py` — Support for providing temporary directories to test functions. (~3579 tok)
- `tracemalloc.py` — tracemalloc_message (~223 tok)
- `unittest.py` — Discover and run std-library "unittest" style tests. (~6996 tok)
- `unraisableexception.py` — UnraisableMeta: gc_collect_harder, collect_unraisable, cleanup, unraisable_hook + 4 more (~1480 tok)
- `warning_types.py` — PytestWarning: simple, format, warn_explicit_for (~1257 tok)
- `warnings.py` — mypy: allow-untyped-defs (~1484 tok)

## .venv/Lib/site-packages/_pytest/_code/

- `__init__.py` — Python inspection/code generation API. (~149 tok)
- `code.py` — mypy: allow-untyped-defs (~16036 tok)
- `source.py` — mypy: allow-untyped-defs (~2221 tok)

## .venv/Lib/site-packages/_pytest/_io/

- `__init__.py` (~55 tok)
- `pprint.py` — mypy: allow-untyped-defs (~5607 tok)
- `saferepr.py` — SafeRepr: repr, repr_instance, safeformat, saferepr + 1 more (~1167 tok)
- `terminalwriter.py` — Helper functions for writing to terminals and files. (~2570 tok)
- `wcwidth.py` — wcwidth, wcswidth (~369 tok)

## .venv/Lib/site-packages/_pytest/_py/

- `__init__.py` (~0 tok)
- `error.py` — create errno-specific classes for IO or os calls. (~993 tok)
- `path.py` — local path implementation. (~14066 tok)

## .venv/Lib/site-packages/_pytest/assertion/

- `__init__.py` — Support for presenting detailed information in failing assertions. (~2035 tok)
- `rewrite.py` — .py" for example) we can't bail out based (~13774 tok)
- `truncate.py` — Utilities for truncating assertion output. (~1554 tok)
- `util.py` — Utilities for assertion debugging. (~5875 tok)

## .venv/Lib/site-packages/_pytest/config/

- `__init__.py` — Command line options, config-file and conftest.py processing. (~22658 tok)
- `argparsing.py` — mypy: allow-untyped-defs (~5840 tok)
- `compat.py` — URL configuration (~842 tok)
- `exceptions.py` — Declares UsageError (~90 tok)
- `findpaths.py` — URL configuration (~3680 tok)

## .venv/Lib/site-packages/_pytest/mark/

- `__init__.py` — Generic mechanism for marking and selecting python functions. (~2820 tok)
- `expression.py` — TokenType: lex, accept, accept, accept + 10 more (~3213 tok)
- `structures.py` — mypy: allow-untyped-defs (~6593 tok)

## .venv/Lib/site-packages/_yaml/

- `__init__.py` — This is a stub package designed to roughly emulate the _yaml (~401 tok)

## .venv/Lib/site-packages/a2a/

- `__init__.py` — The A2A Python SDK. (~8 tok)
- `_base.py` — A2ABaseModel: to_camel_custom (~362 tok)
- `py.typed` (~0 tok)
- `types.py` — filename:  https://raw.githubusercontent.com/a2aproject/A2A/refs/heads/main/specification/json/a2a.json (~15714 tok)

## .venv/Lib/site-packages/a2a/auth/

- `__init__.py` (~0 tok)
- `user.py` — Authenticated user information. (~226 tok)

## .venv/Lib/site-packages/a2a/client/

- `__init__.py` — Client-side components for interacting with an A2A agent. (~552 tok)
- `base_client.py` — BaseClient: send_message, get_task, cancel_task, set_task_callback + 4 more (~3022 tok)
- `card_resolver.py` — A2ACardResolver: get_agent_card (~1129 tok)
- `client_factory.py` — ClientFactory: connect, register, create, minimal_agent_card (~3111 tok)
- `client_task_manager.py` — ClientTaskManager: get_task, get_task_or_raise, save_task_event, process + 1 more (~1850 tok)
- `client.py` — class: send_message, get_task, cancel_task, set_task_callback + 6 more (~1937 tok)
- `errors.py` — Custom exceptions for the A2A client. (~1066 tok)
- `helpers.py` — Helper functions for the A2A client. (~182 tok)
- `legacy_grpc.py` — Backwards compatibility layer for the legacy A2A gRPC client. (~386 tok)
- `legacy.py` — Backwards compatibility layer for legacy A2A clients. (~3869 tok)
- `middleware.py` — Pydantic: ClientCallContext (12 fields) (~476 tok)
- `optionals.py` — Attempt to import the optional module (~156 tok)

## .venv/Lib/site-packages/a2a/client/auth/

- `__init__.py` — Client-side authentication components for the A2A Python SDK. (~95 tok)
- `credentials.py` — CredentialService: get_credentials, get_credentials, set_credentials (~486 tok)
- `interceptor.py` — AuthInterceptor: intercept (~1129 tok)

## .venv/Lib/site-packages/a2a/client/transports/

- `__init__.py` — A2A Client Transports. (~122 tok)
- `base.py` — ClientTransport: send_message, send_message_streaming, get_task, cancel_task + 5 more (~1066 tok)
- `grpc.py` — logger: create, send_message, send_message_streaming, resubscribe + 6 more (~2520 tok)
- `jsonrpc.py` — logger: send_message, send_message_streaming, get_task, cancel_task + 3 more (~4626 tok)
- `rest.py` — logger: send_message, send_message_streaming, get_task, cancel_task + 3 more (~4381 tok)

## .venv/Lib/site-packages/a2a/extensions/

- `__init__.py` (~0 tok)
- `common.py` — get_requested_extensions, find_extension_by_uri, update_extension_header (~334 tok)

## .venv/Lib/site-packages/a2a/grpc/

- `__init__.py` (~0 tok)
- `a2a_pb2_grpc.py` — Client and server classes corresponding to protobuf-defined services. (~5966 tok)
- `a2a_pb2.py` — Generated protocol buffer code. (~8106 tok)
- `a2a_pb2.pyi` — Declares TaskState (~7694 tok)

## .venv/Lib/site-packages/a2a/server/

- `__init__.py` — Server-side components for implementing an A2A agent. (~18 tok)
- `context.py` — Defines the ServerCallContext class. (~200 tok)
- `id_generator.py` — IDGeneratorContext: generate, generate (~196 tok)
- `models.py` — SQLAlchemy: PydanticType (tasks) (~2346 tok)

## .venv/Lib/site-packages/a2a/server/agent_execution/

- `__init__.py` — Components for executing agent logic within the A2A server. (~149 tok)
- `agent_executor.py` — AgentExecutor: execute, cancel (~472 tok)
- `context.py` — RequestContext: get_user_input, attach_related_task, message, related_tasks + 9 more (~2058 tok)
- `request_context_builder.py` — RequestContextBuilder: build (~169 tok)
- `simple_request_context_builder.py` — SimpleRequestContextBuilder: build (~995 tok)

## .venv/Lib/site-packages/a2a/server/apps/

- `__init__.py` — HTTP application components for the A2A server. (~120 tok)

## .venv/Lib/site-packages/a2a/server/apps/jsonrpc/

- `__init__.py` — A2A JSON-RPC Applications. (~149 tok)
- `fastapi_app.py` — A2AFastAPI: openapi, add_routes_to_app, build (~1954 tok)
- `jsonrpc_app.py` — StarletteUserProxy: is_authenticated, user_name, build, build (~7002 tok)
- `starlette_app.py` — A2AStarletteApplication: routes, add_routes_to_app, build (~2080 tok)

## .venv/Lib/site-packages/a2a/server/apps/rest/

- `__init__.py` — A2A REST Applications. (~43 tok)
- `fastapi_app.py` — API: GET (1 endpoints) (~1267 tok)
- `rest_adapter.py` — RESTAdapter: event_generator, handle_get_agent_card, handle_authenticated_agent_card, routes (~2758 tok)

## .venv/Lib/site-packages/a2a/server/events/

- `__init__.py` — Event handling components for the A2A server. (~146 tok)
- `event_consumer.py` — EventConsumer: consume_one, consume_all, agent_task_callback (~1748 tok)
- `event_queue.py` — logger: enqueue_event, dequeue_event, task_done, tap + 3 more (~2873 tok)
- `in_memory_queue_manager.py` — View: get (~847 tok)
- `queue_manager.py` — View: get (~350 tok)

## .venv/Lib/site-packages/a2a/server/request_handlers/

- `__init__.py` — Request handler components for the A2A server. (~401 tok)
- `default_request_handler.py` — logger: on_get_task, on_cancel_task, on_message_send, push_notification_callback (~5991 tok)
- `grpc_handler.py` — ruff: noqa: N802 (~4403 tok)
- `jsonrpc_handler.py` — logger: on_message_send, on_message_send_stream, on_cancel_task, on_resubscribe_to_task + 3 more (~4803 tok)
- `request_handler.py` — RequestHandler: on_get_task, on_cancel_task, on_message_send, on_message_send_stream + 5 more (~1779 tok)
- `response_helpers.py` — Helper functions for building A2A JSON-RPC responses. (~1328 tok)
- `rest_handler.py` — logger: on_message_send, on_message_send_stream, on_cancel_task, on_resubscribe_to_task + 5 more (~2854 tok)

## .venv/Lib/site-packages/a2a/server/tasks/

- `__init__.py` — Components for managing tasks within the A2A server. (~809 tok)
- `base_push_notification_sender.py` — BasePushNotificationSender: send_notification (~677 tok)
- `database_push_notification_config_store.py` — ruff: noqa: PLC0415 (~3175 tok)
- `database_task_store.py` — DatabaseTaskStore: initialize, save, get, delete (~1692 tok)
- `inmemory_push_notification_config_store.py` — InMemoryPushNotificationConfigStore: set_info, get_info, delete_info (~697 tok)
- `inmemory_task_store.py` — InMemoryTaskStore: save, get, delete (~572 tok)
- `push_notification_config_store.py` — PushNotificationConfigStore: set_info, get_info, delete_info (~224 tok)
- `push_notification_sender.py` — PushNotificationSender: send_notification (~91 tok)
- `result_aggregator.py` — ResultAggregator: current_result, consume_and_emit, consume_all, consume_and_break_on_interrupt (~2232 tok)
- `task_manager.py` — TaskManager: get_task, save_task_event, ensure_task, process + 1 more (~2753 tok)
- `task_store.py` — TaskStore: save, get, delete (~226 tok)
- `task_updater.py` — TaskUpdater: update_status, add_artifact, complete, failed + 7 more (~2361 tok)

## .venv/Lib/site-packages/a2a/utils/

- `__init__.py` — Utility functions for the A2A Python SDK. (~366 tok)
- `artifact.py` — Utility functions for creating A2A Artifact objects. (~669 tok)
- `constants.py` — Constants for well-known URIs used throughout the A2A Python SDK. (~80 tok)
- `error_handlers.py` — rest_error_handler, wrapper, rest_stream_error_handler, wrapper (~1180 tok)
- `errors.py` — Custom exceptions for A2A server-side errors. (~733 tok)
- `helpers.py` — General utility functions for the A2A Python SDK. (~3934 tok)
- `message.py` — Utility functions for creating and handling A2A Message objects. (~536 tok)
- `parts.py` — Utility functions for creating and handling A2A Parts objects. (~397 tok)
- `proto_utils.py` — Utils for converting between proto and Python types. (~11092 tok)
- `signing.py` — SignatureVerificationError: create_agent_card_signer, agent_card_signer, create_signature_verifier, signature_verifier (~1454 tok)
- `task.py` — Utility functions for creating A2A Task objects. (~864 tok)
- `telemetry.py` — OpenTelemetry Tracing Utilities for A2A Python SDK. (~3667 tok)

## .venv/Lib/site-packages/a2a_sdk-0.3.23.dist-info/

- `INSTALLER` (~2 tok)
- `METADATA` (~2235 tok)
- `RECORD` (~3433 tok)
- `WHEEL` (~24 tok)

## .venv/Lib/site-packages/a2a_sdk-0.3.23.dist-info/licenses/

- `LICENSE` — Project license (~3029 tok)

## .venv/Lib/site-packages/adodbapi/

- `__init__.py` — adodbapi - A python DB API 2.0 (PEP 249) interface to Microsoft ADO (~781 tok)
- `ado_consts.py` — ADO enumerated constants documented on MSDN: (~2821 tok)
- `adodbapi.py` — adodbapi - A python DB API 2.0 (PEP 249) interface to Microsoft ADO (~13984 tok)
- `apibase.py` — adodbapi.apibase - A python DB API 2.0 (PEP 249) interface to Microsoft ADO (~7752 tok)
- `is64bit.py` — is64bit.Python() --> boolean value of detected Python word size. is64bit.os() --> os build version (~293 tok)
- `license.txt` — Declares definition (~6732 tok)
- `process_connect_string.py` — a clumsy attempt at a macro language to let the programmer execute code on the server (ex: determine 64bit) (~1549 tok)
- `readme.txt` — Declares attributes (~1196 tok)
- `schema_table.py` — call using an open ADO connection --> list of table names (~126 tok)
- `setup.py` — Python package setup (~627 tok)

## .venv/Lib/site-packages/adodbapi/examples/

- `db_print.py` — db_print.py -- a simple demo for ADO database reads. (~654 tok)
- `db_table_names.py` — db_table_names.py -- a simple demo for ADO database table listing. (~151 tok)
- `xls_read.py` (~324 tok)
- `xls_write.py` (~418 tok)

## .venv/Lib/site-packages/adodbapi/test/

- `adodbapitest.py` — Unit tests version 2.6.1.0 for adodbapi (~16056 tok)
- `adodbapitestconfig.py` — Configure this to _YOUR_ environment in order to run the testcases. (~1862 tok)
- `dbapi20.py` — Python DB API 2.0 driver compliance unit test suite. (~9529 tok)
- `is64bit.py` — is64bit.Python() --> boolean value of detected Python word size. is64bit.os() --> os build version (~290 tok)
- `setuptestframework.py` — Configure this in order to run the testcases. (~862 tok)
- `test_adodbapi_dbapi20.py` — URL configuration (~1700 tok)
- `tryconnection.py` — try_connection, try_operation_with_expected_exception (~294 tok)

## .venv/Lib/site-packages/ag_ui/

- `__init__.py` (~0 tok)
- `py.typed` (~0 tok)

## .venv/Lib/site-packages/ag_ui/core/

- `__init__.py` (~823 tok)
- `events.py` — Pydantic: BaseEvent (97 fields) (~3623 tok)
- `types.py` — Pydantic: ConfiguredBaseModel (54 fields) (~1358 tok)

## .venv/Lib/site-packages/ag_ui/encoder/

- `__init__.py` (~48 tok)
- `encoder.py` — EventEncoder: get_content_type, encode (~223 tok)

## .venv/Lib/site-packages/ag_ui_protocol-0.1.13.dist-info/

- `INSTALLER` (~2 tok)
- `METADATA` — Declares hints (~674 tok)
- `RECORD` (~363 tok)
- `WHEEL` (~21 tok)

## .venv/Lib/site-packages/agent_framework/

- `__init__.py` — Public API surface for Agent Framework core. (~3194 tok)
- `_agents.py` — _RunContext: run, create_session, get_session, run + 5 more (~20818 tok)
- `_clients.py` — SupportsChatGetResponse: get_response, get_response, get_response, get_response + 2 more (~9814 tok)
- `_compaction.py` — TokenizerProtocol: count_tokens, count_tokens, group_messages (~14058 tok)
- `_docstrings.py` — insert_docstring_block, build_layered_docstring, apply_layered_docstring (~1022 tok)
- `_evaluation.py` — Provider-agnostic evaluation framework for Microsoft Agent Framework. (~20496 tok)
- `_feature_stage.py` — ExperimentalFeature: bound_init_subclass_wrapper, init_subclass_wrapper, wrapper, decorator + 2 more (~2741 tok)
- `_mcp.py` — MCPSpecificApproval: streamable_http_client (~20774 tok)
- `_middleware.py` — _EmptyAsyncIterator: process, process, process (~16940 tok)
- `_serialization.py` — SerializationProtocol: to_dict, from_dict, is_serializable, to_dict (~7916 tok)
- `_sessions.py` — Unified context management types for the agent framework. (~9840 tok)
- `_settings.py` — Generic settings loader with environment variable resolution. (~3312 tok)
- `_skills.py` — Agent Skills provider, models, and discovery utilities. (~16633 tok)
- `_telemetry.py` — prepend_agent_framework_to_user_agent (~712 tok)
- `_tools.py` — Pydantic: WeatherArgs (40 fields) (~31455 tok)
- `_types.py` — TextSpanRegion: detect_media_type_from_base64 (~39280 tok)
- `exceptions.py` — Exception hierarchy used across Agent Framework core and connectors. (~1709 tok)
- `observability.py` — Observability and OpenTelemetry helpers for Agent Framework. (~27367 tok)
- `py.typed` (~0 tok)

## .venv/Lib/site-packages/agent_framework/_workflows/

- `__init__.py` (~14 tok)
- `_agent_executor.py` — from: agent, description, run, from_response + 7 more (~5958 tok)
- `_agent_utils.py` — resolve_agent_id (~134 tok)
- `_agent.py` — from: to_dict, to_json, from_dict, from_json + 5 more (~12200 tok)
- `_checkpoint_encoding.py` — encode_checkpoint_value, decode_checkpoint_value (~1671 tok)
- `_checkpoint.py` — URL configuration (~4905 tok)
- `_const.py` — Default maximum iterations for workflow execution. (~275 tok)
- `_conversation_history.py` — latest_user_message, ensure_author (~264 tok)
- `_edge_runner.py` — EdgeRunner: send_message, send_message, send_message, send_to_edge (~5259 tok)
- `_edge.py` — methods: threshold, id, has_condition, should_route + 2 more (~10157 tok)
- `_events.py` — from: from_exception, started, status, failed + 8 more (~5013 tok)
- `_executor.py` — Executor: handle_string, handle_data, process, handle_text + 7 more (~9137 tok)
- `_function_executor.py` — Function-based Executor and decorator utilities. (~4394 tok)
- `_message_utils.py` — Shared helpers for normalizing workflow message inputs. (~380 tok)
- `_model_utils.py` — DictConvertible: to_dict, from_dict, clone, to_json + 2 more (~454 tok)
- `_request_info_mixin.py` — RequestInfoMixin: is_request_supported, response_handler, response_handler, response_handler + 5 more (~4806 tok)
- `_runner_context.py` — from: trace_context, source_span_id, to_dict, from_dict + 29 more (~4963 tok)
- `_runner.py` — Runner: context, reset_iteration_count, run_until_convergence, restore_from_checkpoint (~5100 tok)
- `_state.py` — View: get, delete (~1299 tok)
- `_typing_utils.py` — or: is_chat_agent, resolve_type_annotation, normalize_type_to_list, is_instance_of + 4 more (~4213 tok)
- `_validation.py` — ValidationTypeEnum: validate_workflow (~5049 tok)
- `_viz.py` — Import of WorkflowExecutor is performed lazily inside methods to avoid cycles (~5062 tok)
- `_workflow_builder.py` — WorkflowBuilder: process, process, add_edge, process + 6 more (~8070 tok)
- `_workflow_context.py` — is: infer_output_types_from_ctx_annotation, validate_workflow_context_annotation, log_handler, processor + 4 more (~5310 tok)
- `_workflow_executor.py` — from: create_response, handle_subworkflow_request (~8774 tok)
- `_workflow.py` — ruff: noqa: RUF070, RUF100 (~11879 tok)

## .venv/Lib/site-packages/agent_framework/a2a/

- `__init__.py` — A2A integration namespace for optional Agent Framework connectors. (~242 tok)
- `__init__.pyi` (~35 tok)

## .venv/Lib/site-packages/agent_framework/ag_ui/

- `__init__.py` — AG-UI integration namespace for optional Agent Framework connectors. (~311 tok)
- `__init__.pyi` (~126 tok)

## .venv/Lib/site-packages/agent_framework/amazon/

- `__init__.py` — Amazon Bedrock integration namespace for optional Agent Framework connectors. (~536 tok)
- `__init__.pyi` (~170 tok)

## .venv/Lib/site-packages/agent_framework/anthropic/

- `__init__.py` — Anthropic integration namespace for optional Agent Framework connectors. (~625 tok)
- `__init__.pyi` (~195 tok)

## .venv/Lib/site-packages/agent_framework/azure/

- `__init__.py` — Azure integration namespace for optional Agent Framework connectors. (~538 tok)
- `__init__.pyi` — Type stubs for the agent_framework.azure lazy-loading namespace. (~251 tok)

## .venv/Lib/site-packages/agent_framework/chatkit/

- `__init__.py` — ChatKit integration namespace for optional Agent Framework connectors. (~285 tok)
- `__init__.pyi` (~72 tok)

## .venv/Lib/site-packages/agent_framework/declarative/

- `__init__.py` — Declarative integration namespace for optional Agent Framework connectors. (~357 tok)
- `__init__.pyi` (~186 tok)

## .venv/Lib/site-packages/agent_framework/devui/

- `__init__.py` — DevUI integration namespace for optional Agent Framework connectors. (~330 tok)
- `__init__.pyi` (~132 tok)

## .venv/Lib/site-packages/agent_framework/foundry/

- `__init__.py` — Foundry integration namespace for optional Agent Framework connectors. (~750 tok)
- `__init__.pyi` — Type stubs for the agent_framework.foundry lazy-loading namespace. (~355 tok)

## .venv/Lib/site-packages/agent_framework/github/

- `__init__.py` — GitHub integration namespace for optional Agent Framework connectors. (~377 tok)
- `__init__.pyi` (~73 tok)

## .venv/Lib/site-packages/agent_framework/google/

- `__init__.py` — Google integration namespace for optional Agent Framework connectors. (~316 tok)
- `__init__.pyi` (~57 tok)

## .venv/Lib/site-packages/agent_framework/lab/

- `__init__.py` — Lab namespace package for experimental Agent Framework integrations. (~115 tok)

## .venv/Lib/site-packages/agent_framework/lab/gaia/

- `__init__.py` — Import and re-export from the actual implementation (~45 tok)

## .venv/Lib/site-packages/agent_framework/lab/lightning/

- `__init__.py` — Import and re-export from the actual implementation (~46 tok)

## .venv/Lib/site-packages/agent_framework/lab/tau2/

- `__init__.py` — Import and re-export from the actual implementation (~45 tok)

## .venv/Lib/site-packages/agent_framework/mem0/

- `__init__.py` — Mem0 integration namespace for optional Agent Framework connectors. (~249 tok)
- `__init__.pyi` (~41 tok)

## .venv/Lib/site-packages/agent_framework/microsoft/

- `__init__.py` — Microsoft integration namespace for optional Agent Framework connectors. (~678 tok)
- `__init__.pyi` (~227 tok)

## .venv/Lib/site-packages/agent_framework/ollama/

- `__init__.py` — Ollama integration namespace for optional Agent Framework connectors. (~323 tok)
- `__init__.pyi` (~111 tok)

## .venv/Lib/site-packages/agent_framework/openai/

- `__init__.py` — OpenAI namespace for Agent Framework clients. (~602 tok)
- `__init__.pyi` — Type stubs for the agent_framework.openai lazy-loading namespace. (~250 tok)

## .venv/Lib/site-packages/agent_framework/orchestrations/

- `__init__.py` — Orchestrations integration namespace for optional Agent Framework connectors. (~658 tok)
- `__init__.pyi` (~546 tok)

## .venv/Lib/site-packages/agent_framework/redis/

- `__init__.py` — Redis integration namespace for optional Agent Framework connectors. (~264 tok)
- `__init__.pyi` (~56 tok)

## .venv/Lib/site-packages/agent_framework_a2a-1.0.0b260402.dist-info/

- `INSTALLER` (~2 tok)
- `METADATA` (~489 tok)
- `RECORD` (~210 tok)
- `WHEEL` (~22 tok)

## .venv/Lib/site-packages/agent_framework_a2a-1.0.0b260402.dist-info/licenses/

- `LICENSE` — Project license (~305 tok)

## .venv/Lib/site-packages/agent_framework_a2a/

- `__init__.py` (~107 tok)
- `_agent.py` — A2AContinuationToken: run, run, run (~8722 tok)

## .venv/Lib/site-packages/agent_framework_ag_ui-1.0.0b260402.dist-info/

- `INSTALLER` (~2 tok)
- `METADATA` — Declares and (~2267 tok)
- `RECORD` (~1822 tok)
- `WHEEL` (~24 tok)

## .venv/Lib/site-packages/agent_framework_ag_ui-1.0.0b260402.dist-info/licenses/

- `LICENSE` — Project license (~287 tok)

## .venv/Lib/site-packages/agent_framework_ag_ui/

- `__init__.py` — AG-UI protocol integration for Agent Framework. (~308 tok)
- `_agent_run.py` — Simplified AG-UI orchestration - single linear flow. (~13572 tok)
- `_agent.py` — AgentFrameworkAgent wrapper for AG-UI protocol. (~1446 tok)
- `_client.py` — AG-UI Chat Client implementation. (~5324 tok)
- `_endpoint.py` — FastAPI endpoint creation for AG-UI agents. (~2010 tok)
- `_event_converters.py` — Event converter for AG-UI protocol events to Agent Framework types. (~2397 tok)
- `_http_service.py` — HTTP service for AG-UI protocol communication. (~1707 tok)
- `_message_adapters.py` — Message format conversion between AG-UI and Agent Framework. (~13219 tok)
- `_run_common.py` — Shared AG-UI run helpers used by agent and workflow runners. (~6110 tok)
- `_types.py` — Type definitions for AG-UI integration. (~1708 tok)
- `_utils.py` — Utility functions for AG-UI integration. (~2451 tok)
- `_workflow_run.py` — Native AG-UI orchestration for MAF Workflow streams. (~8413 tok)
- `_workflow.py` — Workflow wrapper for AG-UI protocol compatibility. (~880 tok)
- `py.typed` — Marker file for PEP 561 (~7 tok)

## .venv/Lib/site-packages/agent_framework_ag_ui/_orchestration/

- `__init__.py` (~14 tok)
- `_helpers.py` — Helper functions for orchestration logic. (~1943 tok)
- `_predictive_state.py` — Predictive state handling utilities. (~2182 tok)
- `_tooling.py` — Tool handling helpers. (~1399 tok)

## .venv/Lib/site-packages/agent_framework_ag_ui_examples/

- `__init__.py` — Example agents for AG-UI demonstration. (~42 tok)
- `__main__.py` — Entry point for running the AG-UI examples server as a module. (~56 tok)
- `README.md` — Project documentation (~2811 tok)

## .venv/Lib/site-packages/agent_framework_ag_ui_examples/agents/

- `__init__.py` — Example agents for AG-UI demonstration. (~250 tok)
- `document_writer_agent.py` — Example agent demonstrating predictive state updates with document writing. (~706 tok)
- `human_in_the_loop_agent.py` — Human-in-the-loop agent demonstrating step customization (Feature 5). (~831 tok)
- `recipe_agent.py` — Recipe agent example demonstrating shared state management (Feature 3). (~1365 tok)
- `research_assistant_agent.py` — Example agent demonstrating agentic generative UI with custom events during execution. (~873 tok)
- `simple_agent.py` — Simple agentic chat example (Feature 1: Agentic Chat). (~168 tok)
- `subgraphs_agent.py` — Subgraphs travel planner built with MAF workflow primitives. (~3654 tok)
- `task_planner_agent.py` — Example agent demonstrating human-in-the-loop with function approvals. (~679 tok)
- `task_steps_agent.py` — Task steps agent demonstrating agentic generative UI (Feature 6). (~3810 tok)
- `ui_generator_agent.py` — Example agent demonstrating Tool-based Generative UI (Feature 5). (~2046 tok)
- `weather_agent.py` — Weather agent example demonstrating backend tool rendering. (~792 tok)

## .venv/Lib/site-packages/agent_framework_ag_ui_examples/server/

- `__init__.py` (~14 tok)
- `main.py` — Example FastAPI server with AG-UI endpoints. (~1550 tok)

## .venv/Lib/site-packages/agent_framework_ag_ui_examples/server/api/

- `backend_tool_rendering.py` — Backend tool rendering endpoint. (~236 tok)

## .venv/Lib/site-packages/agent_framework_anthropic-1.0.0b260402.dist-info/

- `INSTALLER` (~2 tok)
- `METADATA` (~511 tok)
- `RECORD` (~367 tok)
- `WHEEL` (~22 tok)

## .venv/Lib/site-packages/agent_framework_anthropic-1.0.0b260402.dist-info/licenses/

- `LICENSE` — Project license (~305 tok)

## .venv/Lib/site-packages/agent_framework_anthropic/

- `__init__.py` (~246 tok)
- `_bedrock_client.py` — Declares AnthropicBedrockSettings (~2067 tok)
- `_chat_client.py` — Declares ThinkingConfig (~18979 tok)
- `_foundry_client.py` — Declares AnthropicFoundrySettings (~2088 tok)
- `_vertex_client.py` — Declares AnthropicVertexSettings (~1955 tok)

## .venv/Lib/site-packages/agent_framework_azure_ai_search-1.0.0b260402.dist-info/

- `INSTALLER` (~2 tok)
- `METADATA` (~550 tok)
- `RECORD` (~244 tok)
- `WHEEL` (~22 tok)

## .venv/Lib/site-packages/agent_framework_azure_ai_search-1.0.0b260402.dist-info/licenses/

- `LICENSE` — Project license (~305 tok)

## .venv/Lib/site-packages/agent_framework_azure_ai_search/

- `__init__.py` (~122 tok)
- `_context_provider.py` — New-pattern Azure AI Search context provider using ContextProvider. (~13360 tok)

## .venv/Lib/site-packages/agent_framework_azure_cosmos-1.0.0b260402.dist-info/

- `INSTALLER` (~2 tok)
- `METADATA` (~705 tok)
- `RECORD` (~237 tok)
- `WHEEL` (~22 tok)

## .venv/Lib/site-packages/agent_framework_azure_cosmos-1.0.0b260402.dist-info/licenses/

- `LICENSE` — Project license (~305 tok)

## .venv/Lib/site-packages/agent_framework_azure_cosmos/

- `__init__.py` (~103 tok)
- `_history_provider.py` — Azure Cosmos DB history provider. (~3318 tok)

## .venv/Lib/site-packages/agent_framework_azurefunctions-1.0.0b260402.dist-info/

- `INSTALLER` (~2 tok)
- `METADATA` (~586 tok)
- `RECORD` (~540 tok)
- `WHEEL` (~22 tok)

## .venv/Lib/site-packages/agent_framework_azurefunctions-1.0.0b260402.dist-info/licenses/

- `LICENSE` — Project license (~305 tok)

## .venv/Lib/site-packages/agent_framework_azurefunctions/

- `__init__.py` (~101 tok)
- `_app.py` — AgentFunctionApp - Main application class. (~16884 tok)
- `_context.py` — Runner context for Azure Functions activity execution. (~1830 tok)
- `_entities.py` — Durable Entity for Agent Execution. (~1326 tok)
- `_errors.py` — Custom exception types for the durable agent framework. (~111 tok)
- `_orchestration.py` — Orchestration Support for Durable Agents. (~2291 tok)
- `_serialization.py` — Serialization utilities for workflow execution. (~1936 tok)
- `_workflow.py` — Workflow Execution for Durable Functions. (~11216 tok)
- `py.typed` (~0 tok)

## .venv/Lib/site-packages/agent_framework_bedrock-1.0.0b260402.dist-info/

- `INSTALLER` (~2 tok)
- `METADATA` — Declares calling (~502 tok)
- `RECORD` (~269 tok)
- `WHEEL` (~24 tok)

## .venv/Lib/site-packages/agent_framework_bedrock-1.0.0b260402.dist-info/licenses/

- `LICENSE` — Project license (~304 tok)

## .venv/Lib/site-packages/agent_framework_bedrock/

- `__init__.py` (~196 tok)
- `_chat_client.py` — type: ignore (~9133 tok)
- `_embedding_client.py` — type: ignore (~3100 tok)

## .venv/Lib/site-packages/agent_framework_chatkit-1.0.0b260402.dist-info/

- `INSTALLER` (~2 tok)
- `METADATA` — Declares to (~1737 tok)
- `RECORD` (~287 tok)
- `WHEEL` (~22 tok)

## .venv/Lib/site-packages/agent_framework_chatkit-1.0.0b260402.dist-info/licenses/

- `LICENSE` — Project license (~287 tok)

## .venv/Lib/site-packages/agent_framework_chatkit/

- `__init__.py` — Agent Framework and ChatKit Integration. (~220 tok)
- `_converter.py` — Converter utilities for converting ChatKit thread items to Agent Framework messages. (~6832 tok)
- `_streaming.py` — Streaming utilities for converting Agent Framework responses to ChatKit events. (~1148 tok)
- `py.typed` (~0 tok)

## .venv/Lib/site-packages/agent_framework_claude-1.0.0b260402.dist-info/

- `INSTALLER` (~2 tok)
- `METADATA` (~395 tok)
- `RECORD` (~217 tok)
- `WHEEL` (~22 tok)

## .venv/Lib/site-packages/agent_framework_claude-1.0.0b260402.dist-info/licenses/

- `LICENSE` — Project license (~305 tok)

## .venv/Lib/site-packages/agent_framework_claude/

- `__init__.py` (~132 tok)
- `_agent.py` — ClaudeAgentSettings: start, stop (~8694 tok)

## .venv/Lib/site-packages/agent_framework_copilotstudio-1.0.0b260402.dist-info/

- `INSTALLER` (~2 tok)
- `METADATA` (~1184 tok)
- `RECORD` (~282 tok)
- `WHEEL` (~22 tok)

## .venv/Lib/site-packages/agent_framework_copilotstudio-1.0.0b260402.dist-info/licenses/

- `LICENSE` — Project license (~305 tok)

## .venv/Lib/site-packages/agent_framework_copilotstudio/

- `__init__.py` (~116 tok)
- `_acquire_token.py` — pyright: reportUnknownMemberType = false (~1045 tok)
- `_agent.py` — CopilotStudioSettings: run, run, run (~3998 tok)

## .venv/Lib/site-packages/agent_framework_core-1.0.0.dist-info/

- `INSTALLER` (~2 tok)
- `METADATA` — Declares calling (~2719 tok)
- `RECORD` (~3196 tok)
- `WHEEL` (~22 tok)

## .venv/Lib/site-packages/agent_framework_core-1.0.0.dist-info/licenses/

- `LICENSE` — Project license (~305 tok)

## .venv/Lib/site-packages/agent_framework_declarative-1.0.0b260402.dist-info/

- `INSTALLER` (~2 tok)
- `METADATA` (~390 tok)
- `RECORD` (~870 tok)
- `WHEEL` (~22 tok)

## .venv/Lib/site-packages/agent_framework_declarative-1.0.0b260402.dist-info/licenses/

- `LICENSE` — Project license (~305 tok)

## .venv/Lib/site-packages/agent_framework_declarative/

- `__init__.py` (~252 tok)
- `_loader.py` — URL patterns: 3 routes (~10491 tok)
- `_models.py` — Binding: from_dict, from_dict, to_json_schema, from_dict (~11507 tok)

## .venv/Lib/site-packages/agent_framework_declarative/_workflows/

- `__init__.py` — Declarative workflow support for agent-framework. (~1112 tok)
- `_declarative_base.py` — Base classes for graph-based declarative workflow executors. (~9986 tok)
- `_declarative_builder.py` — Builder that transforms declarative YAML into a workflow graph. (~11880 tok)
- `_executors_agents.py` — Agent invocation executors for declarative workflows. (~11706 tok)
- `_executors_basic.py` — Basic action executors for the graph-based declarative workflow system. (~6585 tok)
- `_executors_control_flow.py` — Control flow executors for the graph-based declarative workflow system. (~5473 tok)
- `_executors_external_input.py` — External input executors for declarative workflows. (~3423 tok)
- `_executors_tools.py` — Tool invocation executors for declarative workflows. (~6952 tok)
- `_factory.py` — WorkflowFactory creates executable Workflow objects from YAML definitions. (~8010 tok)
- `_powerfx_functions.py` — Custom PowerFx-like functions for declarative workflows. (~3950 tok)
- `_state.py` — WorkflowState manages PowerFx variables during declarative workflow execution. (~6529 tok)

## .venv/Lib/site-packages/agent_framework_devui-1.0.0b260402.dist-info/

- `entry_points.txt` (~13 tok)
- `INSTALLER` (~2 tok)
- `METADATA` — Declares for (~4809 tok)
- `RECORD` (~978 tok)
- `WHEEL` (~22 tok)

## .venv/Lib/site-packages/agent_framework_devui-1.0.0b260402.dist-info/licenses/

- `LICENSE` — Project license (~305 tok)

## .venv/Lib/site-packages/agent_framework_devui/

- `__init__.py` — Agent Framework DevUI - Debug interface with OpenAI compatible API server. (~2775 tok)
- `_cli.py` — Command line interface for Agent Framework DevUI. (~1892 tok)
- `_conversations.py` — Conversation storage abstraction for OpenAI Conversations API. (~8285 tok)
- `_deployment.py` — Azure Container Apps deployment manager for DevUI entities. (~6848 tok)
- `_discovery.py` — Agent Framework entity discovery implementation. (~10840 tok)
- `_executor.py` — Agent Framework executor implementation. (~14909 tok)
- `_mapper.py` — Agent Framework message mapper implementation. (~23512 tok)
- `_server.py` — FastAPI server implementation. (~18806 tok)
- `_session.py` — Session management for agent execution tracking. (~1866 tok)
- `_tracing.py` — Simplified tracing integration for Agent Framework Server. (~1776 tok)
- `_utils.py` — Utility functions for DevUI. (~6828 tok)

## .venv/Lib/site-packages/agent_framework_devui/_openai/

- `__init__.py` — OpenAI integration for DevUI - proxy support for OpenAI Responses API. (~61 tok)
- `_executor.py` — OpenAI Executor - proxies requests to OpenAI Responses API. (~3078 tok)

## .venv/Lib/site-packages/agent_framework_devui/models/

- `__init__.py` — Agent Framework DevUI Models - OpenAI-compatible types and custom extensions. (~826 tok)
- `_discovery_models.py` — Discovery API models for entity information. (~2075 tok)
- `_openai_custom.py` — Custom OpenAI-compatible event types for Agent Framework extensions. (~4096 tok)

## .venv/Lib/site-packages/agent_framework_devui/ui/

- `index.html` — Agent Framework Dev UI (~124 tok)

## .venv/Lib/site-packages/agent_framework_devui/ui/assets/

- `index.css` — Styles: 203 vars, 2 media queries, 5 layers (~30105 tok)
- `index.js` — KE: r, a, Cp + 30 more (~243027 tok)

## .venv/Lib/site-packages/agent_framework_durabletask-1.0.0b260402.dist-info/

- `INSTALLER` (~2 tok)
- `METADATA` (~643 tok)
- `RECORD` (~713 tok)
- `WHEEL` (~22 tok)

## .venv/Lib/site-packages/agent_framework_durabletask-1.0.0b260402.dist-info/licenses/

- `LICENSE` — Project license (~305 tok)

## .venv/Lib/site-packages/agent_framework_durabletask/

- `__init__.py` — Durable Task integration for Microsoft Agent Framework. (~992 tok)
- `_callbacks.py` — Callback interfaces for Durable Agent executions. (~335 tok)
- `_client.py` — Client wrapper for Durable Task Agent Framework. (~963 tok)
- `_constants.py` — Constants for Azure Functions Agent Framework integration. (~1304 tok)
- `_durable_agent_state.py` — Durable agent state management conforming to the durable-agent-entity-state.json schema. (~15258 tok)
- `_entities.py` — Durable Task entity implementations for Microsoft Agent Framework. (~3583 tok)
- `_executors.py` — Provider strategies for Durable Agent execution. (~5298 tok)
- `_models.py` — Data models for Durable Agent Framework. (~3492 tok)
- `_orchestration_context.py` — Orchestration context wrapper for Durable Task Agent Framework. (~776 tok)
- `_response_utils.py` — Shared utilities for handling AgentResponse parsing and validation. (~828 tok)
- `_shim.py` — Durable Agent Shim for Durable Task Framework. (~1850 tok)
- `_worker.py` — Worker wrapper for Durable Task Agent Framework. (~2105 tok)
- `py.typed` (~0 tok)

## .venv/Lib/site-packages/agent_framework_foundry-1.0.0.dist-info/

- `INSTALLER` (~2 tok)
- `METADATA` (~403 tok)

## .venv/Lib/site-packages/agent_framework_foundry-1.0.0.dist-info/licenses/

- `LICENSE` — Project license (~305 tok)

## .venv/Lib/site-packages/agent_framework_foundry/

- `__init__.py` (~314 tok)
- `_agent.py` — Microsoft Foundry Agent for connecting to pre-configured agents in Foundry. (~9408 tok)
- `_chat_client.py` — FoundrySettings: resolve_file_ids, configure_azure_monitor, get_code_interpreter_tool (~7064 tok)
- `_embedding_client.py` — FoundryEmbeddingOptions: close, service_url, get_embeddings (~4686 tok)
- `_foundry_evals.py` — Microsoft Foundry Evals integration for Microsoft Agent Framework. (~9662 tok)
- `_memory_provider.py` — Foundry Memory Context Provider using ContextProvider. (~3339 tok)

## .venv/Lib/site-packages/agent_framework_foundry_local-1.0.0b260402.dist-info/

- `INSTALLER` (~2 tok)
- `METADATA` (~447 tok)
- `RECORD` (~266 tok)
- `WHEEL` (~22 tok)

## .venv/Lib/site-packages/agent_framework_foundry_local-1.0.0b260402.dist-info/licenses/

- `LICENSE` — Project license (~305 tok)

## .venv/Lib/site-packages/agent_framework_foundry_local/

- `__init__.py` (~138 tok)
- `_foundry_local_client.py` — FoundryLocalChatOptions: get_response, get_response, get_response, get_response (~4132 tok)
- `py.typed` (~0 tok)

## personas/dotnet_developer/

- `instructions.md` — .NET Developer (~503 tok)
- `tools.py` — Tools for the dotnet_developer persona — file ops, dotnet shell, ADO PR, TRX parsing. (~3349 tok)

## personas/qa_engineer/

- `instructions.md` — QA Engineer (~558 tok)
- `tools.py` — Tools for the qa_engineer persona. (~5114 tok)

## personas/react_developer/

- `instructions.md` — React Developer (~491 tok)
- `tools.py` — Tools for the react_developer skill — file ops and shell commands. (~5790 tok)
