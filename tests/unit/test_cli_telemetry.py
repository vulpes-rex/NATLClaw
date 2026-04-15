from __future__ import annotations

import argparse
from types import SimpleNamespace

import cli


def test_cmd_telemetry_profile_runs_sample(monkeypatch, capsys):
    calls = {"start": 0, "stop": 0}
    sleeps: list[float] = []

    monkeypatch.setattr(
        "cli.start_sentry_profiler",
        lambda: calls.__setitem__("start", calls["start"] + 1) or True,
    )
    monkeypatch.setattr(
        "cli.stop_sentry_profiler",
        lambda: calls.__setitem__("stop", calls["stop"] + 1) or True,
    )
    monkeypatch.setattr("cli.time.sleep", lambda seconds: sleeps.append(seconds))

    cli.cmd_telemetry_profile(
        argparse.Namespace(run_sample=True, iterations=3, slow_ms=100.0, fast_ms=50.0),
        config=SimpleNamespace(sentry_dsn="https://example@sentry.invalid/1"),
    )
    out = capsys.readouterr().out

    assert "Profiling sample workload" in out
    assert "Completed 3 iteration(s)" in out
    assert calls["start"] == 1
    assert calls["stop"] == 1
    assert sleeps == [0.1, 0.05, 0.1, 0.05, 0.1, 0.05]


def test_cmd_telemetry_profile_requires_workload_flag(monkeypatch, capsys):
    calls = {"start": 0}

    monkeypatch.setattr(
        "cli.start_sentry_profiler",
        lambda: calls.__setitem__("start", calls["start"] + 1) or True,
    )

    cli.cmd_telemetry_profile(
        argparse.Namespace(run_sample=False, iterations=3, slow_ms=100.0, fast_ms=50.0),
        config=SimpleNamespace(sentry_dsn="https://example@sentry.invalid/1"),
    )
    out = capsys.readouterr().out

    assert "No workload selected. Re-run with --run-sample." in out
    assert calls["start"] == 0


def test_cmd_telemetry_test_error_success(monkeypatch, capsys):
    monkeypatch.setattr("cli.send_test_exception", lambda _c: "evt123")
    cli.cmd_telemetry_test_error(
        argparse.Namespace(),
        config=SimpleNamespace(sentry_dsn="https://example@sentry.invalid/1"),
    )
    out = capsys.readouterr().out
    assert "event_id=evt123" in out


def test_cmd_telemetry_test_error_failure(monkeypatch, capsys):
    monkeypatch.setattr("cli.send_test_exception", lambda _c: None)
    cli.cmd_telemetry_test_error(
        argparse.Namespace(),
        config=SimpleNamespace(sentry_dsn="https://example@sentry.invalid/1"),
    )
    out = capsys.readouterr().out
    assert "Failed to send Sentry test exception." in out


def test_cmd_telemetry_test_error_missing_dsn(capsys):
    cli.cmd_telemetry_test_error(argparse.Namespace(), config=SimpleNamespace(sentry_dsn=""))
    out = capsys.readouterr().out
    assert "SENTRY_DSN is not set" in out
    assert "dsn= does not set" in out
