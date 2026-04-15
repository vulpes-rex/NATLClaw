from __future__ import annotations

import argparse

import cli


def test_watch_status_shows_pending_queue_details(monkeypatch, capsys):
    monkeypatch.setattr("event_watcher.is_watcher_running", lambda: True)
    monkeypatch.setattr("event_watcher._read_pid", lambda: 4242)
    monkeypatch.setattr(
        "event_watcher.pending_events_status",
        lambda: {
            "exists": True,
            "total_lines": 2,
            "valid_events": 2,
            "malformed_lines": 0,
            "by_type": {"file_change": 1, "task_created": 1},
        },
    )

    cli.cmd_watch_status(argparse.Namespace(), config=None)  # type: ignore[arg-type]
    out = capsys.readouterr().out
    assert "Watcher is RUNNING (PID 4242)." in out
    assert "Event queue: 2 pending event(s)." in out
    assert "by type: 1 file_change, 1 task_created" in out


def test_watch_status_shows_empty_queue(monkeypatch, capsys):
    monkeypatch.setattr("event_watcher.is_watcher_running", lambda: False)
    monkeypatch.setattr("event_watcher._read_pid", lambda: None)
    monkeypatch.setattr(
        "event_watcher.pending_events_status",
        lambda: {
            "exists": False,
            "total_lines": 0,
            "valid_events": 0,
            "malformed_lines": 0,
            "by_type": {},
        },
    )

    cli.cmd_watch_status(argparse.Namespace(), config=None)  # type: ignore[arg-type]
    out = capsys.readouterr().out
    assert "Watcher is NOT running." in out
    assert "Event queue: empty." in out
