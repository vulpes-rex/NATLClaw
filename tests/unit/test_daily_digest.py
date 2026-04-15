"""Tests for daily_digest.py — digest builder, task summary, day detection."""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from daily_digest import (
    _task_summary,
    _brain_summary_for_digest,
    _event_queue_summary,
    _load_tasks,
    build_digest,
    save_digest,
    is_first_run_today,
)


# ── Helpers ────────────────────────────────��───────────────────────────

def _make_brain(notes=None, pages=None, connections=None):
    return SimpleNamespace(
        notes=notes or {},
        pages=pages or {},
        connections=connections or [],
    )


# ── _task_summary ──────────────────────────────────────────────────────


class TestTaskSummary:
    def test_empty(self):
        assert _task_summary([]) == "No tasks on the board."

    def test_counts_by_status(self):
        tasks = [
            {"status": "pending", "title": "a"},
            {"status": "pending", "title": "b"},
            {"status": "completed", "title": "c"},
        ]
        result = _task_summary(tasks)
        assert "2 pending" in result
        assert "1 completed" in result

    def test_overdue_detection(self):
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        tasks = [{"status": "pending", "title": "Late task", "due": yesterday}]
        result = _task_summary(tasks)
        assert "OVERDUE" in result
        assert "Late task" in result

    def test_completed_not_overdue(self):
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        tasks = [{"status": "completed", "title": "Done", "due": yesterday}]
        result = _task_summary(tasks)
        assert "OVERDUE" not in result


# ── _brain_summary_for_digest ──────────────────────────���───────────────


class TestBrainSummary:
    def test_empty_brain(self):
        brain = _make_brain()
        result = _brain_summary_for_digest(brain)
        assert "0 notes" in result
        assert "0 wiki pages" in result

    def test_with_notes(self):
        brain = _make_brain(notes={
            "n1": {"content": "First note about APIs", "created_at": "2026-04-01T00:00:00Z", "tags": ["api"]},
            "n2": {"content": "Second note about testing", "created_at": "2026-04-02T00:00:00Z", "tags": ["test"]},
        })
        result = _brain_summary_for_digest(brain)
        assert "2 notes" in result
        assert "Recent notes:" in result

    def test_truncates_content(self):
        brain = _make_brain(notes={
            "n1": {"content": "x" * 200, "created_at": "2026-04-01T00:00:00Z", "tags": []},
        })
        result = _brain_summary_for_digest(brain)
        # Content should be truncated to 80 chars
        lines = result.splitlines()
        note_line = [l for l in lines if l.startswith("  -")][0]
        assert len(note_line) < 200


# ── _event_queue_summary ──────────────────────────────────────────────


class TestEventQueueSummary:
    def test_no_queue_file(self, monkeypatch):
        monkeypatch.setattr(
            "daily_digest.pending_events_status",
            lambda: {
                "exists": False,
                "total_lines": 0,
                "valid_events": 0,
                "malformed_lines": 0,
                "by_type": {},
            },
        )
        result = _event_queue_summary()
        assert result == "Event queue: empty."

    def test_empty_queue(self, monkeypatch):
        monkeypatch.setattr(
            "daily_digest.pending_events_status",
            lambda: {
                "exists": True,
                "total_lines": 0,
                "valid_events": 0,
                "malformed_lines": 0,
                "by_type": {},
            },
        )
        assert _event_queue_summary() == "Event queue: empty."

    def test_queue_with_types_and_malformed_lines(self, monkeypatch):
        monkeypatch.setattr(
            "daily_digest.pending_events_status",
            lambda: {
                "exists": True,
                "total_lines": 3,
                "valid_events": 2,
                "malformed_lines": 1,
                "by_type": {"file_change": 1, "task_created": 1},
            },
        )
        result = _event_queue_summary()
        assert "Event queue: 3 events" in result
        assert "1 file_change" in result
        assert "1 task_created" in result
        assert "malformed=1" in result


# ── _load_tasks ────────────────────────────────────────────────────────


class TestLoadTasks:
    def test_no_file(self, monkeypatch):
        monkeypatch.setattr("daily_digest.TASKS_PATH", Path("nonexistent.json"))
        assert _load_tasks() == []

    def test_valid_json(self, tmp_path, monkeypatch):
        tasks_file = tmp_path / "tasks.json"
        tasks_file.write_text(json.dumps([
            {"id": "t1", "title": "Test", "status": "pending"},
        ]), encoding="utf-8")
        monkeypatch.setattr("daily_digest.TASKS_PATH", tasks_file)
        result = _load_tasks()
        assert len(result) == 1
        assert result[0]["title"] == "Test"

    def test_invalid_json(self, tmp_path, monkeypatch):
        tasks_file = tmp_path / "tasks.json"
        tasks_file.write_text("not json", encoding="utf-8")
        monkeypatch.setattr("daily_digest.TASKS_PATH", tasks_file)
        assert _load_tasks() == []

    def test_non_list_json(self, tmp_path, monkeypatch):
        tasks_file = tmp_path / "tasks.json"
        tasks_file.write_text('{"key": "value"}', encoding="utf-8")
        monkeypatch.setattr("daily_digest.TASKS_PATH", tasks_file)
        assert _load_tasks() == []


# ── build_digest ───────────────────────────────────────────────────────


class TestBuildDigest:
    def test_includes_header(self):
        brain = _make_brain()
        with patch("daily_digest._git_log_since", return_value="(no commits)"):
            digest = build_digest(brain, persona_name="test_persona")
        assert "Daily Brief" in digest
        assert "test_persona" in digest

    def test_includes_brain_section(self):
        brain = _make_brain()
        with patch("daily_digest._git_log_since", return_value="(no commits)"):
            digest = build_digest(brain)
        assert "## Brain" in digest
        assert "0 notes" in digest

    def test_includes_tasks_section(self):
        brain = _make_brain()
        with patch("daily_digest._git_log_since", return_value="(no commits)"), \
             patch("daily_digest._load_tasks", return_value=[]):
            digest = build_digest(brain)
        assert "## Tasks" in digest
        assert "No tasks on the board" in digest

    def test_includes_git_changes(self):
        brain = _make_brain()
        with patch("daily_digest._git_log_since", return_value="abc1234 Fix bug\n 1 file changed"):
            digest = build_digest(brain)
        assert "## Recent Changes" in digest
        assert "Fix bug" in digest

    def test_includes_active_work_section(self):
        brain = _make_brain()
        with patch("daily_digest._git_log_since", return_value="(no commits)"):
            digest = build_digest(
                brain,
                active_work={
                    "branch": "feature/observer",
                    "files": ["cli.py", "scheduler.py"],
                    "commit_intent": "Improve status output",
                },
            )
        assert "## Current Active Work" in digest
        assert "feature/observer" in digest
        assert "cli.py" in digest


# ── save_digest ────────────────────────────────────────────────────────


class TestSaveDigest:
    def test_saves_to_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("daily_digest.DIGEST_DIR", tmp_path / "digests")
        path = save_digest("Test digest content")
        assert path.exists()
        assert path.read_text(encoding="utf-8") == "Test digest content"
        assert path.suffix == ".md"


# ── is_first_run_today ────────────────────────────────────────────────


class TestIsFirstRunToday:
    def test_none_heartbeat(self):
        assert is_first_run_today(None) is True

    def test_today_heartbeat(self):
        now = datetime.now(timezone.utc).isoformat()
        assert is_first_run_today(now) is False

    def test_yesterday_heartbeat(self):
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        assert is_first_run_today(yesterday) is True

    def test_invalid_heartbeat(self):
        assert is_first_run_today("not-a-date") is True
