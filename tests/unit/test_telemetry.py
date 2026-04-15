from __future__ import annotations

import types

from config import AppConfig
import telemetry


def test_init_sentry_no_dsn_returns_false(monkeypatch):
    monkeypatch.setattr(telemetry, "_SENTRY_INITIALIZED", False)
    cfg = AppConfig(sentry_dsn="")
    assert telemetry.init_sentry(cfg) is False


def test_init_sentry_with_dsn_initializes_once(monkeypatch):
    monkeypatch.setattr(telemetry, "_SENTRY_INITIALIZED", False)

    call_count = {"init": 0}

    fake_sdk = types.ModuleType("sentry_sdk")

    def _init(**kwargs):
        call_count["init"] += 1
        assert kwargs["dsn"] == "https://example@sentry.invalid/1"

    fake_sdk.init = _init

    fake_fastapi_mod = types.ModuleType("sentry_sdk.integrations.fastapi")
    fake_fastapi_mod.FastApiIntegration = type("FastApiIntegration", (), {})

    fake_logging_mod = types.ModuleType("sentry_sdk.integrations.logging")
    fake_logging_mod.LoggingIntegration = type("LoggingIntegration", (), {"__init__": lambda self, **_: None})

    monkeypatch.setitem(__import__("sys").modules, "sentry_sdk", fake_sdk)
    monkeypatch.setitem(__import__("sys").modules, "sentry_sdk.integrations.fastapi", fake_fastapi_mod)
    monkeypatch.setitem(__import__("sys").modules, "sentry_sdk.integrations.logging", fake_logging_mod)

    cfg = AppConfig(sentry_dsn="https://example@sentry.invalid/1")
    assert telemetry.init_sentry(cfg) is True
    assert telemetry.init_sentry(cfg) is True
    assert call_count["init"] == 1


def test_manual_profiler_helpers(monkeypatch):
    class _Profiler:
        def __init__(self):
            self.started = 0
            self.stopped = 0

        def start_profiler(self):
            self.started += 1

        def stop_profiler(self):
            self.stopped += 1

    profiler = _Profiler()
    fake_sdk = types.ModuleType("sentry_sdk")
    fake_sdk.profiler = profiler

    monkeypatch.setitem(__import__("sys").modules, "sentry_sdk", fake_sdk)

    assert telemetry.start_sentry_profiler() is True
    assert telemetry.stop_sentry_profiler() is True
    assert profiler.started == 1
    assert profiler.stopped == 1


def test_send_test_exception(monkeypatch):
    calls = {"captured": 0, "flushed": 0}

    fake_sdk = types.ModuleType("sentry_sdk")

    def _capture_exception(_exc):
        calls["captured"] += 1
        return "abc123"

    def _flush(timeout=0):
        assert timeout == 3.0
        calls["flushed"] += 1

    fake_sdk.capture_exception = _capture_exception
    fake_sdk.flush = _flush

    monkeypatch.setitem(__import__("sys").modules, "sentry_sdk", fake_sdk)
    monkeypatch.setattr(telemetry, "init_sentry", lambda _c: True)
    cfg = AppConfig(sentry_dsn="https://example@sentry.invalid/1")
    assert telemetry.send_test_exception(cfg) == "abc123"
    assert calls["captured"] == 1
    assert calls["flushed"] == 1
