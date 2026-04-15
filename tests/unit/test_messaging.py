"""Tests for the outbox messaging system (messaging.py)."""
from __future__ import annotations

import asyncio
import json
import os

import pytest

from messaging import (
    Message,
    append_message,
    build_inbox_summary,
    extend_messages,
    create_message,
    dismiss_all_read,
    emit_alert,
    emit_escalation_alert,
    emit_fyi,
    emit_task_blocked,
    emit_task_completed,
    emit_task_failed,
    emit_task_started,
    emit_task_timed_out,
    find_message,
    format_inbox,
    format_message_detail,
    get_by_type,
    get_requiring_response,
    get_unread,
    load_outbox,
    mark_dismissed,
    mark_read,
    prune_old_messages,
    save_outbox,
)


# ── Helpers ───────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


class _FakeTask:
    """Minimal task-like object for testing emit_* functions."""
    def __init__(self, **kwargs):
        self.id = kwargs.get("id", "t123")
        self.title = kwargs.get("title", "Fix the bug")
        self.priority = kwargs.get("priority", "high")
        self.assigned_to = kwargs.get("assigned_to", "dev")
        self.heartbeats_spent = kwargs.get("heartbeats_spent", 3)
        self.max_heartbeats = kwargs.get("max_heartbeats", 10)
        self.deliverables = kwargs.get("deliverables", ["src/auth.py"])


# ── Message creation ──────────────────────────────────────────────────

class TestCreateMessage:
    def test_creates_with_defaults(self):
        m = create_message("status", "Hello")
        assert m.type == "status"
        assert m.title == "Hello"
        assert m.status == "unread"
        assert m.urgency == "normal"
        assert m.id.startswith("m")
        assert m.created_at != ""

    def test_creates_with_all_fields(self):
        m = create_message(
            "question", "Need input",
            body="What DB?",
            urgency="high",
            task_id="t123",
            persona="dev",
            heartbeat=42,
            requires_response=True,
            payload={"key": "value"},
        )
        assert m.type == "question"
        assert m.urgency == "high"
        assert m.task_id == "t123"
        assert m.persona == "dev"
        assert m.heartbeat == 42
        assert m.requires_response is True
        assert m.payload == {"key": "value"}

    def test_unique_ids(self):
        ids = {create_message("fyi", "test").id for _ in range(20)}
        assert len(ids) == 20


# ── Emit helpers ──────────────────────────────────────────────────────

class TestEmitHelpers:
    def test_emit_task_completed(self):
        task = _FakeTask(deliverables=["src/main.py", "tests/test.py"])
        m = emit_task_completed(task, persona="dev", heartbeat=5)
        assert m.type == "handoff"
        assert m.task_id == "t123"
        assert "completed" in m.title.lower()
        assert "src/main.py" in m.body
        assert m.payload["deliverables"] == ["src/main.py", "tests/test.py"]
        assert m.payload["severity"] == "normal"

    def test_emit_task_blocked(self):
        task = _FakeTask()
        m = emit_task_blocked(task, "What database?", persona="dev", heartbeat=3)
        assert m.type == "question"
        assert m.urgency == "high"
        assert m.requires_response is True
        assert "database" in m.body.lower()
        assert m.payload["question"] == "What database?"
        assert m.payload["severity"] == "high"

    def test_emit_task_failed(self):
        task = _FakeTask()
        m = emit_task_failed(task, "API timeout", persona="dev", heartbeat=7)
        assert m.type == "alert"
        assert m.urgency == "high"
        assert "failed" in m.title.lower()
        assert m.payload["reason"] == "API timeout"
        assert m.payload["severity"] == "high"

    def test_emit_task_started(self):
        task = _FakeTask()
        m = emit_task_started(task, persona="dev", heartbeat=1)
        assert m.type == "status"
        assert m.urgency == "low"
        assert "started" in m.title.lower()
        assert m.payload["severity"] == "low"

    def test_emit_task_timed_out(self):
        task = _FakeTask(max_heartbeats=5)
        m = emit_task_timed_out(task, persona="dev", heartbeat=10)
        assert m.type == "alert"
        assert m.urgency == "high"
        assert "timed out" in m.title.lower()
        assert m.payload["max_heartbeats"] == 5
        assert m.payload["severity"] == "high"

    def test_emit_alert(self):
        m = emit_alert("Error spike", "5 errors in last hour", urgency="high")
        assert m.type == "alert"
        assert m.urgency == "high"
        assert m.title == "Error spike"
        assert m.payload["severity"] == "high"

    def test_emit_escalation_alert(self):
        m = emit_escalation_alert(
            "repeated_bug_work",
            "Repeated bug-fix pattern detected",
            "Bug work has repeated for multiple cycles.",
            severity="high",
            persona="workspace_observer",
            heartbeat=22,
            payload={"bug_signal_count": 3},
        )
        assert m.type == "alert"
        assert m.urgency == "high"
        assert m.payload["escalation_type"] == "repeated_bug_work"
        assert m.payload["severity"] == "high"
        assert m.payload["bug_signal_count"] == 3

    def test_emit_fyi(self):
        m = emit_fyi("Brain maintenance", "Archived 10 stale notes")
        assert m.type == "fyi"
        assert m.urgency == "low"
        assert m.payload["severity"] == "low"


# ── State transitions ─────────────────────────────────────────────────

class TestStateTransitions:
    def test_mark_read(self):
        m = create_message("status", "Hello")
        assert m.status == "unread"
        mark_read(m)
        assert m.status == "read"
        assert m.read_at is not None

    def test_mark_read_idempotent(self):
        m = create_message("status", "Hello")
        mark_read(m)
        first_read = m.read_at
        mark_read(m)
        assert m.read_at == first_read  # no-op on already read

    def test_mark_dismissed(self):
        m = create_message("status", "Hello")
        mark_read(m)
        mark_dismissed(m)
        assert m.status == "dismissed"
        assert m.dismissed_at is not None

    def test_dismiss_all_read(self):
        msgs = [
            create_message("status", "A"),
            create_message("status", "B"),
            create_message("status", "C"),
        ]
        mark_read(msgs[0])
        mark_read(msgs[1])
        # C stays unread
        count = dismiss_all_read(msgs)
        assert count == 2
        assert msgs[0].status == "dismissed"
        assert msgs[1].status == "dismissed"
        assert msgs[2].status == "unread"


# ── Query helpers ─────────────────────────────────────────────────────

class TestQueryHelpers:
    def _make_messages(self):
        m1 = create_message("status", "A", urgency="low")
        m2 = create_message("question", "B", urgency="high", requires_response=True)
        m3 = create_message("alert", "C", urgency="urgent")
        m4 = create_message("fyi", "D")
        mark_read(m4)
        return [m1, m2, m3, m4]

    def test_get_unread_sorted_by_urgency(self):
        msgs = self._make_messages()
        unread = get_unread(msgs)
        assert len(unread) == 3
        assert unread[0].urgency == "urgent"
        assert unread[1].urgency == "high"
        assert unread[2].urgency == "low"

    def test_get_by_type(self):
        msgs = self._make_messages()
        alerts = get_by_type(msgs, "alert")
        assert len(alerts) == 1
        assert alerts[0].type == "alert"

    def test_get_requiring_response(self):
        msgs = self._make_messages()
        needs = get_requiring_response(msgs)
        assert len(needs) == 1
        assert needs[0].requires_response is True

    def test_find_message(self):
        msgs = self._make_messages()
        found = find_message(msgs, msgs[0].id)
        assert found is msgs[0]

    def test_find_message_not_found(self):
        assert find_message([], "nonexistent") is None


class TestDedupHelpers:
    def test_append_message_dedupes_equivalent_unread(self):
        messages = [create_message("alert", "Failure", body="Task t1 failed", urgency="high", task_id="t1")]
        dup = create_message("alert", "Failure", body="Task t1 failed", urgency="high", task_id="t1")
        appended = append_message(messages, dup)
        assert appended is False
        assert len(messages) == 1

    def test_append_message_allows_after_dismissed(self):
        existing = create_message("alert", "Failure", body="Task t1 failed", urgency="high", task_id="t1")
        mark_dismissed(existing)
        messages = [existing]
        dup = create_message("alert", "Failure", body="Task t1 failed", urgency="high", task_id="t1")
        appended = append_message(messages, dup)
        assert appended is True
        assert len(messages) == 2

    def test_extend_messages_returns_appended_count(self):
        messages = []
        a = create_message("question", "Need input", body="Q1", urgency="high", task_id="t1")
        b = create_message("question", "Need input", body="Q1", urgency="high", task_id="t1")
        c = create_message("alert", "Other", body="different", urgency="high", task_id="t1")
        count = extend_messages(messages, [a, b, c])
        assert count == 2
        assert len(messages) == 2


# ── Persistence ───────────────────────────────────────────────────────

class TestPersistence:
    def test_save_and_load_round_trip(self, tmp_path):
        state_file = str(tmp_path / "agent_state.json")
        m1 = create_message("status", "Task A done", task_id="t001")
        m2 = create_message("question", "Need info", requires_response=True)
        mark_read(m2)

        _run(save_outbox([m1, m2], state_file))
        loaded = _run(load_outbox(state_file))

        assert len(loaded) == 2
        assert loaded[0].title == "Task A done"
        assert loaded[0].task_id == "t001"
        assert loaded[1].status == "read"
        assert loaded[1].requires_response is True

    def test_load_missing_file_returns_empty(self, tmp_path):
        state_file = str(tmp_path / "nonexistent.json")
        msgs = _run(load_outbox(state_file))
        assert msgs == []

    def test_load_corrupt_file_returns_empty(self, tmp_path):
        state_file = str(tmp_path / "agent_state.json")
        outbox_path = tmp_path / "outbox.json"
        outbox_path.write_text("NOT JSON", encoding="utf-8")
        msgs = _run(load_outbox(state_file))
        assert msgs == []

    def test_load_non_list_returns_empty(self, tmp_path):
        state_file = str(tmp_path / "agent_state.json")
        outbox_path = tmp_path / "outbox.json"
        outbox_path.write_text('{"not": "a list"}', encoding="utf-8")
        msgs = _run(load_outbox(state_file))
        assert msgs == []

    def test_load_ignores_unknown_fields(self, tmp_path):
        state_file = str(tmp_path / "agent_state.json")
        outbox_path = tmp_path / "outbox.json"
        data = [{"id": "m123", "title": "Test", "type": "fyi", "future_field": True}]
        outbox_path.write_text(json.dumps(data), encoding="utf-8")
        loaded = _run(load_outbox(state_file))
        assert len(loaded) == 1
        assert loaded[0].id == "m123"
        assert not hasattr(loaded[0], "future_field")

    def test_atomic_write_creates_parent_dirs(self, tmp_path):
        nested = tmp_path / "deep" / "nested"
        state_file = str(nested / "agent_state.json")
        _run(save_outbox([create_message("fyi", "Test")], state_file))
        outbox_file = nested / "outbox.json"
        assert outbox_file.exists()


# ── Prune ─────────────────────────────────────────────────────────────

class TestPrune:
    def test_prune_removes_dismissed(self):
        msgs = [create_message("fyi", f"Msg {i}") for i in range(5)]
        for m in msgs[:3]:
            mark_read(m)
            mark_dismissed(m)
        pruned = prune_old_messages(msgs)
        assert pruned == 3
        assert len(msgs) == 2

    def test_prune_keeps_unread_and_read(self):
        msgs = [
            create_message("status", "Unread"),
            create_message("status", "Read"),
        ]
        mark_read(msgs[1])
        pruned = prune_old_messages(msgs)
        assert pruned == 0
        assert len(msgs) == 2

    def test_prune_over_max_count(self):
        msgs = [create_message("fyi", f"Msg {i}") for i in range(250)]
        for m in msgs[:200]:
            mark_read(m)
            mark_dismissed(m)
        pruned = prune_old_messages(msgs, max_count=200)
        assert len(msgs) <= 200


# ── Display helpers ───────────────────────────────────────────────────

class TestDisplay:
    def test_format_inbox_empty(self):
        assert format_inbox([]) == "(no messages)"

    def test_format_inbox_shows_unread(self):
        msgs = [
            create_message("status", "Done"),
            create_message("question", "Need help", requires_response=True),
        ]
        output = format_inbox(msgs)
        assert "Done" in output
        assert "Need help" in output
        assert "needs response" in output

    def test_format_inbox_hides_dismissed(self):
        msgs = [create_message("fyi", "Old")]
        mark_read(msgs[0])
        mark_dismissed(msgs[0])
        output = format_inbox(msgs)
        assert output == "(no messages)"

    def test_format_inbox_show_read(self):
        msgs = [create_message("fyi", "Old")]
        mark_read(msgs[0])
        mark_dismissed(msgs[0])
        output = format_inbox(msgs, show_read=True)
        assert "Old" in output

    def test_format_message_detail(self):
        m = create_message(
            "question", "Need DB info",
            body="Which database should I use?",
            task_id="t123",
            persona="dev",
            requires_response=True,
            payload={"question": "Which DB?"},
        )
        output = format_message_detail(m)
        assert "question" in output
        assert "Need DB info" in output
        assert "Which database" in output
        assert "t123" in output
        assert "Requires response: yes" in output

    def test_build_inbox_summary_empty(self):
        assert build_inbox_summary([]) == ""

    def test_build_inbox_summary_with_messages(self):
        msgs = [
            create_message("alert", "Error", urgency="high"),
            create_message("question", "Q", requires_response=True),
            create_message("fyi", "Info"),
        ]
        summary = build_inbox_summary(msgs)
        assert "3 unread" in summary
        assert "1 need response" in summary
        assert "1 alert" in summary

    def test_build_inbox_summary_no_unread(self):
        msgs = [create_message("fyi", "Read")]
        mark_read(msgs[0])
        assert build_inbox_summary(msgs) == ""


# ── CLI integration ──────────────────────────────────────────────────

class TestCLIIntegration:
    def test_parser_inbox_list(self):
        from cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["inbox", "list"])
        assert args.command == "inbox"
        assert args.inbox_command == "list"

    def test_parser_inbox_list_all(self):
        from cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["inbox", "list", "-a"])
        assert args.all is True

    def test_parser_inbox_show(self):
        from cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["inbox", "show", "m123"])
        assert args.inbox_command == "show"
        assert args.message_id == "m123"

    def test_parser_inbox_dismiss_single(self):
        from cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["inbox", "dismiss", "m456"])
        assert args.inbox_command == "dismiss"
        assert args.message_id == "m456"

    def test_parser_inbox_dismiss_all(self):
        from cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["inbox", "dismiss", "-a"])
        assert args.inbox_command == "dismiss"
        assert args.all is True

    def test_parser_inbox_clear(self):
        from cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["inbox", "clear"])
        assert args.inbox_command == "clear"


# ── Scheduler integration ────────────────────────────────────────────

class TestSchedulerIntegration:
    def test_scheduler_imports(self):
        """All messaging imports resolve in scheduler."""
        from scheduler import run_scheduler
        assert callable(run_scheduler)

    def test_emit_functions_return_messages(self):
        """All emit_* functions return Message objects."""
        task = _FakeTask()
        msgs = [
            emit_task_completed(task),
            emit_task_blocked(task, "Q"),
            emit_task_failed(task, "R"),
            emit_task_started(task),
            emit_task_timed_out(task),
            emit_alert("Alert"),
            emit_fyi("FYI"),
        ]
        for m in msgs:
            assert isinstance(m, Message)
            assert m.id.startswith("m")
            assert m.status == "unread"
