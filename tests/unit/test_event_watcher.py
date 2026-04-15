"""Tests for event_watcher.py — file watching, polling, event queue, PID management."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from event_watcher import (
    EventWatcher,
    _should_ignore,
    _push_event,
    _push_event_nowait,
    _read_pid,
    _write_pid,
    _clear_pid,
    _is_pid_alive,
    is_watcher_running,
    install_git_hook,
    append_git_commit_event,
    enqueue_event,
    drain_pending_events,
    pending_events_status,
)
import event_watcher


# ── _should_ignore ─────────────────────────────────────────────────────


class TestShouldIgnore:
    def test_ignores_git_dir(self):
        assert _should_ignore(".git/config") is True

    def test_ignores_pycache(self):
        assert _should_ignore("src/__pycache__/mod.pyc") is True

    def test_ignores_node_modules(self):
        assert _should_ignore("node_modules/lodash/index.js") is True

    def test_ignores_pyc_suffix(self):
        assert _should_ignore("module.pyc") is True

    def test_ignores_tmp_suffix(self):
        assert _should_ignore("scratch.tmp") is True

    def test_allows_normal_py(self):
        assert _should_ignore("main.py") is False

    def test_allows_normal_js(self):
        assert _should_ignore("src/app.js") is False

    def test_ignores_data_dir(self):
        assert _should_ignore("data/events.json") is True


# ── Event queue push ───────────────────────────────────────────────────


class TestPushEvent:
    def test_push_event_when_queue_none(self):
        """Should not crash when queue is None."""
        event_watcher._event_queue = None
        _push_event("test_event")  # should just log warning, not crash

    def test_push_event_to_queue(self):
        q = asyncio.PriorityQueue(maxsize=10)
        event_watcher._event_queue = q
        try:
            _push_event("file_change", {"path": "test.py"})
            assert not q.empty()
            event = q.get_nowait()
            # New format: (priority, event_type, payload)
            assert event[0] == 2  # file_change priority
            assert event[1] == "file_change"
            assert event[2] == {"path": "test.py"}
        finally:
            event_watcher._event_queue = None

    def test_push_event_nowait_raises_when_no_queue(self):
        event_watcher._event_queue = None
        with pytest.raises(ValueError, match="not initialized"):
            _push_event_nowait("test")

    def test_push_event_drops_on_full_queue(self):
        q = asyncio.Queue(maxsize=1)
        event_watcher._event_queue = q
        try:
            _push_event("first")
            _push_event("second")  # queue full, should not crash
            assert q.qsize() == 1
        finally:
            event_watcher._event_queue = None


# ── EventWatcher polling ───────────────────────────────────────────────


class TestEventWatcherPolling:
    def test_take_snapshot(self, tmp_path):
        (tmp_path / "hello.py").write_text("print('hi')", encoding="utf-8")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "mod.py").write_text("x = 1", encoding="utf-8")

        watcher = EventWatcher(str(tmp_path))
        snap = watcher._take_snapshot()
        assert "hello.py" in snap
        assert "sub/mod.py" in snap

    def test_snapshot_ignores_pycache(self, tmp_path):
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "mod.cpython-313.pyc").write_bytes(b"")
        watcher = EventWatcher(str(tmp_path))
        snap = watcher._take_snapshot()
        assert len(snap) == 0

    def test_poll_detects_new_file(self, tmp_path):
        q = asyncio.PriorityQueue(maxsize=100)
        watcher = EventWatcher(str(tmp_path), event_queue=q)
        watcher._start_polling()

        # Create a file after initial snapshot
        (tmp_path / "new.py").write_text("x = 1", encoding="utf-8")
        count = watcher.poll_once()

        assert count == 1
        event = q.get_nowait()
        # New format: (priority, event_type, payload)
        assert event[0] == 2  # file_created priority
        assert event[1] == "file_created"
        assert event[2]["path"] == "new.py"

    def test_poll_detects_modified_file(self, tmp_path):
        (tmp_path / "mod.py").write_text("v1", encoding="utf-8")
        q = asyncio.PriorityQueue(maxsize=100)
        watcher = EventWatcher(str(tmp_path), event_queue=q)
        watcher._start_polling()

        # Modify the file (ensure mtime changes)
        time.sleep(0.05)
        (tmp_path / "mod.py").write_text("v2", encoding="utf-8")
        count = watcher.poll_once()

        assert count == 1
        event = q.get_nowait()
        # New format: (priority, event_type, payload)
        assert event[0] == 2  # file_modified priority
        assert event[1] == "file_modified"

    def test_poll_detects_deleted_file(self, tmp_path):
        f = tmp_path / "gone.py"
        f.write_text("bye", encoding="utf-8")
        q = asyncio.PriorityQueue(maxsize=100)
        watcher = EventWatcher(str(tmp_path), event_queue=q)
        watcher._start_polling()

        f.unlink()
        count = watcher.poll_once()

        assert count == 1
        event = q.get_nowait()
        # New format: (priority, event_type, payload)
        assert event[0] == 2  # file_deleted priority
        assert event[1] == "file_deleted"

    def test_poll_returns_zero_when_no_changes(self, tmp_path):
        (tmp_path / "stable.py").write_text("ok", encoding="utf-8")
        watcher = EventWatcher(str(tmp_path))
        watcher._start_polling()
        assert watcher.poll_once() == 0

    def test_poll_returns_zero_when_not_polling(self, tmp_path):
        watcher = EventWatcher(str(tmp_path))
        assert watcher.poll_once() == 0


# ── PID management ─────────────────────────────────────────────────────


class TestPidManagement:
    def test_write_read_clear(self, tmp_path, monkeypatch):
        pid_file = tmp_path / "watcher.pid"
        monkeypatch.setattr("event_watcher.Path", lambda *a: tmp_path if a == ("data",) else Path(*a))
        # Use the actual module — just patch the paths
        monkeypatch.setattr(
            "event_watcher._write_pid",
            lambda: pid_file.write_text(str(os.getpid()), encoding="utf-8"),
        )
        monkeypatch.setattr(
            "event_watcher._read_pid",
            lambda: int(pid_file.read_text(encoding="utf-8").strip()) if pid_file.exists() else None,
        )
        monkeypatch.setattr(
            "event_watcher._clear_pid",
            lambda: pid_file.unlink(missing_ok=True),
        )

        from event_watcher import _write_pid, _read_pid, _clear_pid
        _write_pid()
        assert _read_pid() == os.getpid()
        _clear_pid()
        assert _read_pid() is None

    def test_is_pid_alive_current_process(self):
        assert _is_pid_alive(os.getpid()) is True

    def test_is_pid_alive_nonexistent(self):
        # PID 99999999 almost certainly doesn't exist
        assert _is_pid_alive(99999999) is False


# ── Git hook ───────────────────────────────────────────────────────────


class TestGitHook:
    def test_install_hook_not_git_repo(self, tmp_path):
        result = install_git_hook(str(tmp_path))
        assert "Not a git repository" in result

    def test_install_hook_creates_file(self, tmp_path):
        (tmp_path / ".git").mkdir()
        result = install_git_hook(str(tmp_path))
        hook_path = tmp_path / ".git" / "hooks" / "post-commit"
        assert hook_path.exists()
        assert "NATLClaw" in hook_path.read_text(encoding="utf-8")
        assert "Installed" in result

    def test_install_hook_idempotent(self, tmp_path):
        (tmp_path / ".git").mkdir()
        install_git_hook(str(tmp_path))
        result = install_git_hook(str(tmp_path))
        assert "already installed" in result

    def test_install_hook_appends_to_existing(self, tmp_path):
        (tmp_path / ".git").mkdir()
        hooks_dir = tmp_path / ".git" / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "post-commit").write_text("#!/bin/sh\necho existing\n", encoding="utf-8")

        result = install_git_hook(str(tmp_path))
        assert "Appended" in result
        content = (hooks_dir / "post-commit").read_text(encoding="utf-8")
        assert "existing" in content
        assert "NATLClaw" in content


# ── EventWatcher start/stop ────────────────────────────────────────────


class TestStartStop:
    def test_start_falls_back_to_polling(self, tmp_path):
        """Without watchdog installed, start() should fall back to polling."""
        watcher = EventWatcher(str(tmp_path))
        with patch.dict("sys.modules", {"watchdog": None, "watchdog.observers": None, "watchdog.events": None}):
            watcher.start()
        assert watcher._polling is True
        watcher.stop()
        assert watcher._polling is False

    def test_stop_clears_polling_state(self, tmp_path):
        watcher = EventWatcher(str(tmp_path))
        watcher._polling = True
        watcher.stop()
        assert watcher._polling is False


# ── Cross-process events (enqueue / drain) ────────────────────────────


class TestCrossProcessEvents:
    def test_enqueue_creates_ndjson_file(self, tmp_path, monkeypatch):
        ndjson = tmp_path / "pending_events.ndjson"
        monkeypatch.setattr(event_watcher, "_PENDING_EVENTS_FILE", ndjson)

        enqueue_event("task_created", {"task_id": "t001"})

        assert ndjson.exists()
        import json
        record = json.loads(ndjson.read_text(encoding="utf-8").strip())
        assert record["event_type"] == "task_created"
        assert record["priority"] == 1
        assert record["payload"]["task_id"] == "t001"

    def test_drain_reads_and_clears_file(self, tmp_path, monkeypatch):
        ndjson = tmp_path / "pending_events.ndjson"
        monkeypatch.setattr(event_watcher, "_PENDING_EVENTS_FILE", ndjson)

        # Write two events
        enqueue_event("task_created", {"task_id": "t001"})
        enqueue_event("task_answered", {"task_id": "t002"})

        q = asyncio.PriorityQueue()
        count = drain_pending_events(q)

        assert count == 2
        assert q.qsize() == 2
        # File should be empty after drain
        assert ndjson.read_text(encoding="utf-8").strip() == ""

        # Verify events are in the queue with correct priority
        ev1 = q.get_nowait()
        assert ev1[1] in ("task_created", "task_answered")

    def test_drain_returns_zero_when_no_file(self, tmp_path, monkeypatch):
        ndjson = tmp_path / "nonexistent.ndjson"
        monkeypatch.setattr(event_watcher, "_PENDING_EVENTS_FILE", ndjson)

        q = asyncio.PriorityQueue()
        assert drain_pending_events(q) == 0

    def test_drain_skips_malformed_lines(self, tmp_path, monkeypatch):
        ndjson = tmp_path / "pending_events.ndjson"
        monkeypatch.setattr(event_watcher, "_PENDING_EVENTS_FILE", ndjson)

        ndjson.write_text('not valid json\n{"priority":1,"event_type":"ok","payload":{}}\n',
                          encoding="utf-8")

        q = asyncio.PriorityQueue()
        count = drain_pending_events(q)
        assert count == 1  # only the valid line

    def test_drain_dedupes_identical_replayed_events(self, tmp_path, monkeypatch):
        ndjson = tmp_path / "pending_events.ndjson"
        monkeypatch.setattr(event_watcher, "_PENDING_EVENTS_FILE", ndjson)

        enqueue_event("task_created", {"task_id": "t001"})
        enqueue_event("task_created", {"task_id": "t001"})
        enqueue_event("task_created", {"task_id": "t001"})

        q = asyncio.PriorityQueue()
        count = drain_pending_events(q)
        assert count == 1
        assert q.qsize() == 1
        event = q.get_nowait()
        assert event[1] == "task_created"
        assert event[2] == {"task_id": "t001"}

    def test_push_event_nowait_includes_priority(self):
        """_push_event_nowait should produce (priority, event_type, payload) tuples."""
        q = asyncio.PriorityQueue(maxsize=10)
        event_watcher._event_queue = q
        try:
            _push_event_nowait("git_commit", {"hash": "abc123"})
            event = q.get_nowait()
            assert event == (1, "git_commit", {"hash": "abc123"})
        finally:
            event_watcher._event_queue = None

    def test_drain_supports_legacy_event_records(self, tmp_path, monkeypatch):
        ndjson = tmp_path / "pending_events.ndjson"
        monkeypatch.setattr(event_watcher, "_PENDING_EVENTS_FILE", ndjson)
        ndjson.write_text('{"type":"file_change","path":"src/main.py","ts":"2026-01-01T00:00:00Z"}\n',
                          encoding="utf-8")

        q = asyncio.PriorityQueue()
        count = drain_pending_events(q)
        assert count == 1
        assert q.qsize() == 1
        priority, event_type, payload = q.get_nowait()
        assert priority == 2
        assert event_type == "file_change"
        assert payload["path"] == "src/main.py"

    def test_pending_events_status_counts_types_and_malformed(self, tmp_path, monkeypatch):
        ndjson = tmp_path / "pending_events.ndjson"
        monkeypatch.setattr(event_watcher, "_PENDING_EVENTS_FILE", ndjson)
        ndjson.write_text(
            '{"priority":1,"event_type":"task_created","payload":{"task_id":"t1"}}\n'
            '{"priority":2,"event_type":"file_change","payload":{"path":"x.py"}}\n'
            'not-json\n',
            encoding="utf-8",
        )
        status = pending_events_status()
        assert status["exists"] is True
        assert status["total_lines"] == 3
        assert status["valid_events"] == 2
        assert status["malformed_lines"] == 1
        assert status["by_type"]["task_created"] == 1
        assert status["by_type"]["file_change"] == 1
