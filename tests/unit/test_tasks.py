"""Tests for the task queue system (tasks.py)."""
from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import patch

import pytest

from tasks import (
    Task,
    TaskTransitionError,
    answer_task,
    assign_task,
    auto_timeout_tasks,
    block_task,
    build_task_context,
    cancel_task,
    complete_task,
    create_task,
    fail_task,
    find_task,
    format_task_detail,
    format_task_list,
    get_active_task,
    get_blocked_tasks,
    get_pending_tasks,
    load_tasks,
    retry_task,
    save_tasks,
    start_task,
    advance_task,
)


# ── Helpers ───────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


# ── Task creation ─────────────────────────────────────────────────────

class TestCreateTask:
    def test_creates_with_defaults(self):
        t = create_task("Fix the bug")
        assert t.title == "Fix the bug"
        assert t.description == "Fix the bug"
        assert t.priority == "medium"
        assert t.status == "pending"
        assert t.max_heartbeats == 10
        assert t.id.startswith("t")

    def test_creates_with_custom_values(self):
        t = create_task("Deploy", description="Ship to prod", priority="urgent", max_heartbeats=5)
        assert t.title == "Deploy"
        assert t.description == "Ship to prod"
        assert t.priority == "urgent"
        assert t.max_heartbeats == 5

    def test_unique_ids(self):
        ids = {create_task("t").id for _ in range(20)}
        assert len(ids) == 20


# ── Lifecycle operations ──────────────────────────────────────────────

class TestLifecycle:
    def test_assign_task(self):
        t = create_task("Do work")
        assign_task(t, "python_developer")
        assert t.status == "assigned"
        assert t.assigned_to == "python_developer"
        assert t.started_at is not None

    def test_start_task(self):
        t = create_task("Do work")
        assign_task(t, "dev")
        start_task(t)
        assert t.status == "in_progress"

    def test_start_task_sets_started_at_if_missing(self):
        t = create_task("Work")
        t.status = "assigned"
        t.started_at = None
        start_task(t)
        assert t.started_at is not None

    def test_advance_task(self):
        t = create_task("Work")
        assign_task(t, "dev")
        start_task(t)
        advance_task(t, "Made progress on step 1")
        assert t.heartbeats_spent == 1
        assert "Made progress on step 1" in t.progress_notes

    def test_advance_task_truncates_long_notes(self):
        t = create_task("Work")
        assign_task(t, "dev")
        start_task(t)
        advance_task(t, "x" * 1000)
        assert len(t.progress_notes[0]) == 500

    def test_block_task(self):
        t = create_task("Work")
        assign_task(t, "dev")
        start_task(t)
        block_task(t, "Which database?", heartbeat_number=3)
        assert t.status == "blocked"
        assert len(t.questions) == 1
        assert t.questions[0]["question"] == "Which database?"
        assert t.questions[0]["heartbeat"] == 3

    def test_answer_task(self):
        t = create_task("Work")
        assign_task(t, "dev")
        start_task(t)
        block_task(t, "Which DB?")
        answer_task(t, "PostgreSQL")
        assert t.status == "assigned"
        assert len(t.answers) == 1
        assert t.answers[0]["answer"] == "PostgreSQL"

    def test_complete_task(self):
        t = create_task("Work")
        assign_task(t, "dev")
        start_task(t)
        complete_task(t, deliverables=["src/main.py", "tests/test_main.py"])
        assert t.status == "completed"
        assert t.completed_at is not None
        assert "src/main.py" in t.deliverables

    def test_complete_task_no_deliverables(self):
        t = create_task("Work")
        assign_task(t, "dev")
        start_task(t)
        complete_task(t)
        assert t.status == "completed"
        assert t.deliverables == []

    def test_fail_task(self):
        t = create_task("Work")
        assign_task(t, "dev")
        start_task(t)
        fail_task(t, "API key missing")
        assert t.status == "failed"
        assert t.completed_at is not None
        assert any("FAILED: API key missing" in n for n in t.progress_notes)

    def test_fail_task_no_reason(self):
        t = create_task("Work")
        fail_task(t)
        assert t.status == "failed"

    def test_cancel_task(self):
        t = create_task("Work")
        assign_task(t, "dev")
        start_task(t)
        cancel_task(t, "No longer needed")
        assert t.status == "failed"
        assert t.completed_at is not None
        assert any("CANCELLED" in n for n in t.progress_notes)
        assert "No longer needed" in t.progress_notes[-1]

    def test_cancel_task_no_reason(self):
        t = create_task("Work")
        cancel_task(t)
        assert t.status == "failed"
        assert any("CANCELLED by developer" in n for n in t.progress_notes)

    def test_cancel_task_raises_on_completed(self):
        t = create_task("Work")
        assign_task(t, "dev")
        start_task(t)
        complete_task(t)
        with pytest.raises(TaskTransitionError):
            cancel_task(t, "too late")

    def test_cancel_task_raises_on_failed(self):
        t = create_task("Work")
        fail_task(t, "boom")
        with pytest.raises(TaskTransitionError):
            cancel_task(t, "try cancel")
        assert any("FAILED: boom" in n for n in t.progress_notes)

    def test_cancel_blocked_task(self):
        t = create_task("Work")
        assign_task(t, "dev")
        start_task(t)
        block_task(t, "Need info")
        cancel_task(t, "Giving up")
        assert t.status == "failed"
        assert any("CANCELLED" in n for n in t.progress_notes)

    def test_retry_failed_task(self):
        t = create_task("Work")
        assign_task(t, "dev")
        start_task(t)
        advance_task(t, "step 1")
        fail_task(t, "timeout")
        retry_task(t)
        assert t.status == "pending"
        assert t.assigned_to == ""
        assert t.started_at is None
        assert t.completed_at is None
        assert t.heartbeats_spent == 0
        assert any("RETRIED" in n for n in t.progress_notes)

    def test_retry_blocked_task(self):
        t = create_task("Work")
        assign_task(t, "dev")
        start_task(t)
        block_task(t, "Need DB creds")
        retry_task(t)
        assert t.status == "pending"
        assert t.heartbeats_spent == 0

    def test_retry_preserves_progress_notes(self):
        t = create_task("Work")
        assign_task(t, "dev")
        start_task(t)
        advance_task(t, "Made progress")
        fail_task(t, "oops")
        retry_task(t)
        # Progress notes should include original + FAILED + RETRIED
        assert any("Made progress" in n for n in t.progress_notes)
        assert any("RETRIED" in n for n in t.progress_notes)


    def test_invalid_assign_requires_pending(self):
        t = create_task("Work")
        assign_task(t, "dev")
        with pytest.raises(TaskTransitionError):
            assign_task(t, "dev2")

    def test_invalid_start_requires_assigned_or_in_progress(self):
        t = create_task("Work")
        with pytest.raises(TaskTransitionError):
            start_task(t)

    def test_invalid_block_requires_in_progress(self):
        t = create_task("Work")
        with pytest.raises(TaskTransitionError):
            block_task(t, "Need info")

    def test_invalid_answer_requires_blocked(self):
        t = create_task("Work")
        with pytest.raises(TaskTransitionError):
            answer_task(t, "Any answer")

    def test_invalid_complete_requires_in_progress(self):
        t = create_task("Work")
        with pytest.raises(TaskTransitionError):
            complete_task(t)

    def test_invalid_retry_requires_failed_or_blocked(self):
        t = create_task("Work")
        with pytest.raises(TaskTransitionError):
            retry_task(t)


# ── Auto-timeout ──────────────────────────────────────────────────────

class TestAutoTimeout:
    def test_times_out_exceeded_tasks(self):
        t = create_task("Work", max_heartbeats=3)
        assign_task(t, "dev")
        start_task(t)
        t.heartbeats_spent = 3
        timed_out = auto_timeout_tasks([t])
        assert t.id in timed_out
        assert t.status == "failed"

    def test_does_not_timeout_under_limit(self):
        t = create_task("Work", max_heartbeats=5)
        assign_task(t, "dev")
        start_task(t)
        t.heartbeats_spent = 4
        timed_out = auto_timeout_tasks([t])
        assert timed_out == []
        assert t.status == "in_progress"

    def test_ignores_completed_tasks(self):
        t = create_task("Work", max_heartbeats=1)
        t.heartbeats_spent = 5
        assign_task(t, "dev")
        start_task(t)
        complete_task(t)
        timed_out = auto_timeout_tasks([t])
        assert timed_out == []


# ── Query helpers ─────────────────────────────────────────────────────

class TestQueryHelpers:
    def _make_tasks(self):
        t1 = create_task("Low prio", priority="low")
        t2 = create_task("Urgent", priority="urgent")
        t3 = create_task("Medium", priority="medium")
        t4 = create_task("In progress")
        assign_task(t4, "dev")
        start_task(t4)
        t4.assigned_to = "dev"
        t5 = create_task("Blocked")
        assign_task(t5, "dev")
        start_task(t5)
        block_task(t5, "Need info")
        return [t1, t2, t3, t4, t5]

    def test_get_pending_tasks_sorted_by_priority(self):
        tasks = self._make_tasks()
        pending = get_pending_tasks(tasks)
        assert len(pending) == 3
        assert pending[0].priority == "urgent"
        assert pending[1].priority == "medium"
        assert pending[2].priority == "low"

    def test_get_active_task(self):
        tasks = self._make_tasks()
        active = get_active_task(tasks, "dev")
        assert active is not None
        assert active.title == "In progress"

    def test_get_active_task_no_persona_filter(self):
        tasks = self._make_tasks()
        active = get_active_task(tasks)
        assert active is not None

    def test_get_active_task_none_when_empty(self):
        assert get_active_task([], "dev") is None

    def test_get_blocked_tasks(self):
        tasks = self._make_tasks()
        blocked = get_blocked_tasks(tasks)
        assert len(blocked) == 1
        assert blocked[0].title == "Blocked"

    def test_find_task(self):
        tasks = self._make_tasks()
        found = find_task(tasks, tasks[0].id)
        assert found is tasks[0]

    def test_find_task_not_found(self):
        assert find_task([], "nonexistent") is None


# ── Persistence ───────────────────────────────────────────────────────

class TestPersistence:
    def test_save_and_load_round_trip(self, tmp_path):
        state_file = str(tmp_path / "agent_state.json")
        t1 = create_task("Task 1", priority="high")
        t2 = create_task("Task 2")
        assign_task(t2, "dev")
        start_task(t2)
        advance_task(t2, "Step 1 done")

        _run(save_tasks([t1, t2], state_file))
        loaded = _run(load_tasks(state_file))

        assert len(loaded) == 2
        assert loaded[0].title == "Task 1"
        assert loaded[0].priority == "high"
        assert loaded[1].status == "in_progress"
        assert loaded[1].progress_notes == ["Step 1 done"]

    def test_load_missing_file_returns_empty(self, tmp_path):
        state_file = str(tmp_path / "nonexistent.json")
        tasks = _run(load_tasks(state_file))
        assert tasks == []

    def test_load_corrupt_file_returns_empty(self, tmp_path):
        state_file = str(tmp_path / "agent_state.json")
        tasks_path = tmp_path / "tasks.json"
        tasks_path.write_text("NOT JSON", encoding="utf-8")
        tasks = _run(load_tasks(state_file))
        assert tasks == []

    def test_load_non_list_returns_empty(self, tmp_path):
        state_file = str(tmp_path / "agent_state.json")
        tasks_path = tmp_path / "tasks.json"
        tasks_path.write_text('{"not": "a list"}', encoding="utf-8")
        tasks = _run(load_tasks(state_file))
        assert tasks == []

    def test_load_ignores_unknown_fields(self, tmp_path):
        """Forward compatibility — extra fields in JSON are silently dropped."""
        state_file = str(tmp_path / "agent_state.json")
        tasks_path = tmp_path / "tasks.json"
        data = [{"id": "t123", "title": "Test", "future_field": True}]
        tasks_path.write_text(json.dumps(data), encoding="utf-8")
        loaded = _run(load_tasks(state_file))
        assert len(loaded) == 1
        assert loaded[0].id == "t123"
        assert not hasattr(loaded[0], "future_field")

    def test_atomic_write_creates_parent_dirs(self, tmp_path):
        nested = tmp_path / "deep" / "nested"
        state_file = str(nested / "agent_state.json")
        _run(save_tasks([create_task("Test")], state_file))
        tasks_file = nested / "tasks.json"
        assert tasks_file.exists()


# ── Display helpers ───────────────────────────────────────────────────

class TestDisplay:
    def test_format_task_list_empty(self):
        assert format_task_list([]) == "(no tasks)"

    def test_format_task_list_shows_all(self):
        t1 = create_task("A")
        t2 = create_task("B")
        assign_task(t2, "dev")
        start_task(t2)
        output = format_task_list([t1, t2])
        assert "A" in output
        assert "B" in output

    def test_format_task_list_filters_by_status(self):
        t1 = create_task("Pending")
        t2 = create_task("Done")
        assign_task(t2, "dev")
        start_task(t2)
        complete_task(t2)
        output = format_task_list([t1, t2], status_filter="completed")
        assert "Done" in output
        assert "Pending" not in output

    def test_format_task_detail(self):
        t = create_task("Detailed task", description="Full description")
        assign_task(t, "dev")
        start_task(t)
        advance_task(t, "Step 1")
        output = format_task_detail(t)
        assert "Detailed task" in output
        assert "Full description" in output
        assert "Step 1" in output
        assert "dev" in output

    def test_build_task_context(self):
        t = create_task("Context task", description="Context desc", priority="high")
        t.heartbeats_spent = 2
        t.max_heartbeats = 5
        ctx = build_task_context(t)
        assert "ACTIVE TASK" in ctx
        assert "Context task" in ctx
        assert "high" in ctx
        assert "3/5" in ctx  # heartbeats_spent + 1


# ── Scheduler integration ────────────────────────────────────────────

class TestSchedulerIntegration:
    """Verify the scheduler correctly routes tasks vs background heartbeats."""

    def test_scheduler_imports(self):
        """All task-related imports resolve in scheduler."""
        from scheduler import run_scheduler
        # If we get here, imports worked
        assert callable(run_scheduler)

    def test_task_priority_ordering(self):
        """Urgent tasks are picked before low-priority ones."""
        tasks = [
            create_task("Low", priority="low"),
            create_task("Urgent", priority="urgent"),
            create_task("High", priority="high"),
        ]
        pending = get_pending_tasks(tasks)
        assert pending[0].priority == "urgent"
        assert pending[1].priority == "high"
        assert pending[2].priority == "low"

    def test_task_fairness_old_low_priority_promotes_over_new_high(self):
        """A very old low-priority task should be promoted to avoid starvation."""
        old_low = create_task("Old low", priority="low")
        old_low.created_at = "2026-01-01T00:00:00+00:00"
        new_high = create_task("New high", priority="high")
        new_high.created_at = "2026-01-04T11:00:00+00:00"

        with patch("tasks.datetime") as mock_dt:
            from datetime import datetime, timezone
            mock_dt.now.return_value = datetime(2026, 1, 4, 12, 0, 0, tzinfo=timezone.utc)
            mock_dt.fromisoformat = datetime.fromisoformat
            pending = get_pending_tasks([new_high, old_low])

        assert pending[0].title == "Old low"
        assert pending[1].title == "New high"

    def test_task_fairness_same_effective_rank_prefers_older(self):
        """When promoted ranks tie, deterministic order is oldest first."""
        older = create_task("Older high", priority="high")
        older.created_at = "2026-01-01T00:00:00+00:00"
        newer = create_task("New urgent", priority="urgent")
        newer.created_at = "2026-01-04T11:59:00+00:00"

        with patch("tasks.datetime") as mock_dt:
            from datetime import datetime, timezone
            mock_dt.now.return_value = datetime(2026, 1, 4, 12, 0, 0, tzinfo=timezone.utc)
            mock_dt.fromisoformat = datetime.fromisoformat
            pending = get_pending_tasks([newer, older])

        assert pending[0].title == "Older high"
        assert pending[1].title == "New urgent"

    def test_active_task_prevents_new_pickup(self):
        """When a task is already in progress, no new one should be picked."""
        t1 = create_task("Already working")
        assign_task(t1, "dev")
        start_task(t1)
        t2 = create_task("Waiting", priority="urgent")

        active = get_active_task([t1, t2], "dev")
        assert active is t1  # Already-active takes priority

    def test_blocked_task_not_returned_as_active(self):
        """Blocked tasks are NOT picked up as active."""
        t = create_task("Blocked")
        assign_task(t, "dev")
        start_task(t)
        block_task(t, "Waiting for user input")
        active = get_active_task([t])
        assert active is None


# ── CLI integration ───────────────────────────────────────────────────

class TestCLIIntegration:
    def test_parser_task_add(self):
        from cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["task", "add", "Fix tests", "-p", "high", "--max-heartbeats", "5"])
        assert args.command == "task"
        assert args.task_command == "add"
        assert args.title == "Fix tests"
        assert args.priority == "high"
        assert args.max_heartbeats == 5

    def test_parser_task_list(self):
        from cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["task", "list", "-s", "blocked"])
        assert args.task_command == "list"
        assert args.status == "blocked"

    def test_parser_task_status(self):
        from cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["task", "status", "t123"])
        assert args.task_command == "status"
        assert args.task_id == "t123"

    def test_parser_task_answer(self):
        from cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["task", "answer", "t123", "Use OAuth2"])
        assert args.task_command == "answer"
        assert args.task_id == "t123"
        assert args.answer == "Use OAuth2"

    def test_parser_task_cancel(self):
        from cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["task", "cancel", "t123", "--reason", "Not needed"])
        assert args.task_command == "cancel"
        assert args.task_id == "t123"
        assert args.reason == "Not needed"

    def test_parser_task_cancel_no_reason(self):
        from cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["task", "cancel", "t456"])
        assert args.task_command == "cancel"
        assert args.task_id == "t456"
        assert args.reason == ""

    def test_parser_task_retry(self):
        from cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["task", "retry", "t789"])
        assert args.task_command == "retry"
        assert args.task_id == "t789"
