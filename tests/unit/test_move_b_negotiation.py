"""Tests for Move B — task negotiation lifecycle and protocol parsing."""

from __future__ import annotations

import pytest

from tasks import (
    Task,
    TaskTransitionError,
    accept_negotiated_task,
    assign_task,
    get_active_task,
    negotiate_task,
    redirect_task,
)
from workflow import _parse_negotiation_response


# ── negotiate_task ────────────────────────────────────────────────────


class TestNegotiateTask:
    def _pending(self) -> Task:
        return Task(id="t001", title="Test task", status="pending")

    def test_negotiate_transitions_pending_to_negotiating(self):
        task = self._pending()
        negotiate_task(task, "workspace_observer")
        assert task.status == "negotiating"

    def test_negotiate_sets_assigned_to(self):
        task = self._pending()
        negotiate_task(task, "codebase_learner")
        assert task.assigned_to == "codebase_learner"

    def test_negotiate_sets_started_at(self):
        task = self._pending()
        assert task.started_at is None
        negotiate_task(task, "workspace_observer")
        assert task.started_at is not None

    def test_negotiate_rejects_non_pending(self):
        task = self._pending()
        task.status = "in_progress"
        with pytest.raises(TaskTransitionError):
            negotiate_task(task, "workspace_observer")

    def test_negotiate_rejects_blocked(self):
        task = self._pending()
        task.status = "blocked"
        with pytest.raises(TaskTransitionError):
            negotiate_task(task, "workspace_observer")


# ── accept_negotiated_task ────────────────────────────────────────────


class TestAcceptNegotiatedTask:
    def _negotiating(self) -> Task:
        task = Task(id="t002", title="Test task", status="negotiating")
        task.assigned_to = "workspace_observer"
        return task

    def test_accept_transitions_to_in_progress(self):
        task = self._negotiating()
        accept_negotiated_task(task)
        assert task.status == "in_progress"

    def test_accept_sets_negotiation_response(self):
        task = self._negotiating()
        accept_negotiated_task(task)
        assert task.negotiation_response == "accept"

    def test_accept_rejects_non_negotiating(self):
        task = Task(id="t003", title="x", status="pending")
        with pytest.raises(TaskTransitionError):
            accept_negotiated_task(task)

    def test_accept_rejects_in_progress(self):
        task = Task(id="t004", title="x", status="in_progress")
        with pytest.raises(TaskTransitionError):
            accept_negotiated_task(task)


# ── redirect_task ─────────────────────────────────────────────────────


class TestRedirectTask:
    def _negotiating(self) -> Task:
        task = Task(id="t005", title="Refactor DB", status="negotiating")
        task.assigned_to = "workspace_observer"
        return task

    def test_redirect_transitions_to_pending(self):
        task = self._negotiating()
        redirect_task(task, "codebase_learner")
        assert task.status == "pending"

    def test_redirect_sets_target_persona(self):
        task = self._negotiating()
        redirect_task(task, "codebase_learner", reason="better fit")
        assert task.target_persona == "codebase_learner"

    def test_redirect_clears_assigned_to(self):
        task = self._negotiating()
        redirect_task(task, "codebase_learner")
        assert task.assigned_to == ""

    def test_redirect_clears_started_at(self):
        task = self._negotiating()
        task.started_at = "2026-04-16T10:00:00+00:00"
        redirect_task(task, "codebase_learner")
        assert task.started_at is None

    def test_redirect_records_note_with_reason(self):
        task = self._negotiating()
        redirect_task(task, "codebase_learner", reason="needs code analysis")
        assert any("REDIRECTED" in n for n in task.progress_notes)
        assert any("codebase_learner" in n for n in task.progress_notes)

    def test_redirect_sets_negotiation_response(self):
        task = self._negotiating()
        redirect_task(task, "codebase_learner")
        assert task.negotiation_response == "redirect"

    def test_redirect_rejects_non_negotiating(self):
        task = Task(id="t006", title="x", status="pending")
        with pytest.raises(TaskTransitionError):
            redirect_task(task, "codebase_learner")

    def test_redirect_no_reason_no_note(self):
        task = self._negotiating()
        redirect_task(task, "codebase_learner")
        # No note when reason is empty
        assert len(task.progress_notes) == 0

    def test_redirect_with_reason_records_note(self):
        task = self._negotiating()
        redirect_task(task, "codebase_learner", reason="more appropriate")
        assert len(task.progress_notes) == 1


# ── get_active_task includes negotiating ─────────────────────────────


class TestGetActiveTaskNegotiating:
    def test_returns_negotiating_task(self):
        task = Task(id="t007", title="X", status="negotiating", assigned_to="obs")
        result = get_active_task([task], "obs")
        assert result is task

    def test_returns_negotiating_without_filter(self):
        task = Task(id="t008", title="X", status="negotiating", assigned_to="obs")
        result = get_active_task([task])
        assert result is task

    def test_ignores_negotiating_for_different_persona(self):
        task = Task(id="t009", title="X", status="negotiating", assigned_to="obs")
        result = get_active_task([task], "codebase_learner")
        assert result is None


# ── _parse_negotiation_response ───────────────────────────────────────


class TestParseNegotiationResponse:
    def test_accept(self):
        result = _parse_negotiation_response("ACCEPT TASK t001", "t001")
        assert result["action"] == "accept"

    def test_accept_case_insensitive(self):
        result = _parse_negotiation_response("accept task t001", "t001")
        assert result["action"] == "accept"

    def test_redirect_with_reason(self):
        result = _parse_negotiation_response(
            "REDIRECT TASK t002 TO @codebase_learner: needs code analysis",
            "t002",
        )
        assert result["action"] == "redirect"
        assert result["to_persona"] == "codebase_learner"
        assert "needs code analysis" in result["reason"]

    def test_redirect_without_colon(self):
        result = _parse_negotiation_response(
            "REDIRECT TASK t003 TO @workspace_observer",
            "t003",
        )
        assert result["action"] == "redirect"
        assert result["to_persona"] == "workspace_observer"

    def test_clarify_becomes_blocked(self):
        result = _parse_negotiation_response(
            "CLARIFY TASK t004: what is the acceptance criterion?",
            "t004",
        )
        assert result["action"] == "blocked"
        assert "acceptance criterion" in result["reason"]

    def test_clarify_case_insensitive(self):
        result = _parse_negotiation_response(
            "clarify task t005: unclear scope",
            "t005",
        )
        assert result["action"] == "blocked"

    def test_unknown_defaults_to_accept(self):
        result = _parse_negotiation_response("Sure, I'll do this task.", "t006")
        assert result["action"] == "accept"

    def test_wrong_task_id_not_matched(self):
        # ACCEPT for a different task ID should fall through to default accept
        result = _parse_negotiation_response("ACCEPT TASK t999", "t001")
        assert result["action"] == "accept"  # default

    def test_redirect_wrong_task_id_not_matched(self):
        result = _parse_negotiation_response(
            "REDIRECT TASK t999 TO @codebase_learner: x", "t001"
        )
        # Falls through to default
        assert result["action"] == "accept"

    def test_redirect_reason_empty_when_no_colon(self):
        result = _parse_negotiation_response(
            "REDIRECT TASK t010 TO @obs",
            "t010",
        )
        assert result["action"] == "redirect"
        assert result["reason"] == ""


# ── Task field backward compat ────────────────────────────────────────


class TestTaskNegotiationFieldDefaults:
    def test_negotiation_response_defaults_empty(self):
        task = Task(id="t020", title="x")
        assert task.negotiation_response == ""

    def test_handoff_context_defaults_empty_dict(self):
        task = Task(id="t021", title="x")
        assert task.handoff_context == {}

    def test_load_roundtrip_new_fields(self):
        from dataclasses import asdict
        task = Task(id="t022", title="roundtrip", negotiation_response="accept")
        task.handoff_context = {"summary": "done", "findings": ["good"]}
        d = asdict(task)
        assert d["negotiation_response"] == "accept"
        assert d["handoff_context"]["summary"] == "done"
