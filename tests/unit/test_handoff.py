"""Tests for handoff.py — HandoffContext dataclass and delegation builder."""

from __future__ import annotations

import pytest

from handoff import HandoffContext, build_handoff_from_delegation, _as_list


# ── HandoffContext basics ─────────────────────────────────────────────


class TestHandoffContextDefaults:
    def test_all_fields_have_defaults(self):
        hc = HandoffContext()
        assert hc.summary == ""
        assert hc.findings == []
        assert hc.brain_note_ids == []
        assert hc.files_touched == []
        assert hc.open_questions == []
        assert hc.recommendations == []
        assert hc.prior_task_id == ""
        assert hc.prior_persona == ""

    def test_fully_populated(self):
        hc = HandoffContext(
            summary="Did analysis",
            findings=["func A is slow"],
            brain_note_ids=["n001"],
            files_touched=["src/main.py"],
            open_questions=["What is the SLA?"],
            recommendations=["Cache the result"],
            prior_task_id="t001",
            prior_persona="workspace_observer",
        )
        assert hc.summary == "Did analysis"
        assert "func A is slow" in hc.findings


# ── to_dict / from_dict ───────────────────────────────────────────────


class TestHandoffContextSerialization:
    def test_roundtrip(self):
        hc = HandoffContext(
            summary="Summary",
            findings=["f1", "f2"],
            brain_note_ids=["n1"],
            files_touched=["a.py"],
            open_questions=["q?"],
            recommendations=["r!"],
            prior_task_id="t1",
            prior_persona="obs",
        )
        d = hc.to_dict()
        hc2 = HandoffContext.from_dict(d)
        assert hc2.summary == hc.summary
        assert hc2.findings == hc.findings
        assert hc2.brain_note_ids == hc.brain_note_ids
        assert hc2.files_touched == hc.files_touched
        assert hc2.prior_persona == hc.prior_persona

    def test_from_dict_ignores_unknown_keys(self):
        d = {"summary": "hi", "unknown_future_field": 123}
        hc = HandoffContext.from_dict(d)
        assert hc.summary == "hi"
        assert not hasattr(hc, "unknown_future_field")

    def test_from_dict_empty(self):
        hc = HandoffContext.from_dict({})
        assert hc.summary == ""
        assert hc.findings == []

    def test_to_dict_returns_copies_of_lists(self):
        hc = HandoffContext(findings=["x"])
        d = hc.to_dict()
        d["findings"].append("y")
        assert hc.findings == ["x"]


# ── to_prompt_block ───────────────────────────────────────────────────


class TestHandoffContextPromptBlock:
    def test_empty_handoff_returns_empty_string(self):
        hc = HandoffContext()
        assert hc.to_prompt_block() == ""

    def test_block_has_header_and_footer(self):
        hc = HandoffContext(summary="Did something")
        block = hc.to_prompt_block()
        assert "== HANDOFF CONTEXT ==" in block
        assert "== END HANDOFF ==" in block

    def test_summary_in_block(self):
        hc = HandoffContext(summary="Analysed main.py")
        block = hc.to_prompt_block()
        assert "Analysed main.py" in block

    def test_findings_in_block(self):
        hc = HandoffContext(findings=["bottleneck in loop", "missing index"])
        block = hc.to_prompt_block()
        assert "bottleneck in loop" in block
        assert "missing index" in block

    def test_files_in_block(self):
        hc = HandoffContext(files_touched=["src/a.py", "src/b.py"])
        block = hc.to_prompt_block()
        assert "src/a.py" in block

    def test_brain_note_ids_in_block(self):
        hc = HandoffContext(brain_note_ids=["n001", "n002"])
        block = hc.to_prompt_block()
        assert "n001" in block

    def test_open_questions_in_block(self):
        hc = HandoffContext(open_questions=["What is the deadline?"])
        block = hc.to_prompt_block()
        assert "What is the deadline?" in block

    def test_recommendations_in_block(self):
        hc = HandoffContext(recommendations=["Use a cache"])
        block = hc.to_prompt_block()
        assert "Use a cache" in block

    def test_prior_persona_in_block(self):
        hc = HandoffContext(summary="x", prior_persona="workspace_observer")
        block = hc.to_prompt_block()
        assert "@workspace_observer" in block

    def test_prior_task_id_in_block(self):
        hc = HandoffContext(summary="x", prior_task_id="t001")
        block = hc.to_prompt_block()
        assert "t001" in block


# ── build_handoff_from_delegation ─────────────────────────────────────


class TestBuildHandoffFromDelegation:
    def test_returns_none_when_no_content(self):
        result = build_handoff_from_delegation({
            "persona": "obs",
            "task": "Do something",
            "files": [],
            "context": "",
        })
        assert result is None

    def test_returns_handoff_with_context(self):
        d = {
            "persona": "obs",
            "task": "Analyse code",
            "context": "I found three bottlenecks",
            "findings": ["bottleneck A", "bottleneck B"],
            "prior_persona": "coordinator",
        }
        hc = build_handoff_from_delegation(d)
        assert hc is not None
        assert hc.summary == "I found three bottlenecks"
        assert "bottleneck A" in hc.findings
        assert hc.prior_persona == "coordinator"

    def test_maps_files_to_files_touched(self):
        d = {
            "context": "something",
            "files": ["src/a.py"],
        }
        hc = build_handoff_from_delegation(d)
        assert hc is not None
        assert "src/a.py" in hc.files_touched

    def test_prefers_summary_over_context(self):
        d = {
            "summary": "explicit summary",
            "context": "fallback context",
            "findings": ["f1"],
        }
        hc = build_handoff_from_delegation(d)
        assert hc is not None
        assert hc.summary == "explicit summary"

    def test_open_questions_and_recommendations(self):
        d = {
            "context": "analysis done",
            "open_questions": ["Is it thread-safe?"],
            "recommendations": ["Add a mutex"],
        }
        hc = build_handoff_from_delegation(d)
        assert hc is not None
        assert "Is it thread-safe?" in hc.open_questions
        assert "Add a mutex" in hc.recommendations

    def test_brain_note_ids_passed_through(self):
        d = {
            "context": "done",
            "brain_note_ids": ["n001", "n002"],
        }
        hc = build_handoff_from_delegation(d)
        assert hc is not None
        assert "n001" in hc.brain_note_ids


# ── _as_list helper ───────────────────────────────────────────────────


class TestAsListHelper:
    def test_none_returns_empty(self):
        assert _as_list(None) == []

    def test_string_returns_single_item(self):
        assert _as_list("hello") == ["hello"]

    def test_empty_string_returns_empty(self):
        assert _as_list("") == []

    def test_list_passthrough(self):
        assert _as_list(["a", "b"]) == ["a", "b"]

    def test_filters_falsy_items(self):
        assert _as_list(["a", None, "", "b"]) == ["a", "b"]


# ── build_task_context with handoff ──────────────────────────────────


class TestBuildTaskContextWithHandoff:
    def test_no_handoff_no_block(self):
        from tasks import Task, build_task_context
        task = Task(id="t1", title="x", description="desc")
        ctx = build_task_context(task)
        assert "HANDOFF" not in ctx

    def test_empty_handoff_dict_no_block(self):
        from tasks import Task, build_task_context
        task = Task(id="t2", title="x", description="desc", handoff_context={})
        ctx = build_task_context(task)
        assert "HANDOFF" not in ctx

    def test_populated_handoff_injects_block(self):
        from tasks import Task, build_task_context
        hc = HandoffContext(summary="Prior work done", findings=["issue A"])
        task = Task(
            id="t3",
            title="Continue analysis",
            description="Build on prior work",
            handoff_context=hc.to_dict(),
        )
        ctx = build_task_context(task)
        assert "HANDOFF CONTEXT" in ctx
        assert "Prior work done" in ctx
        assert "issue A" in ctx
