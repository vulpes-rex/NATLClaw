"""Test suite for Phase 3 features: goals, ingest, brain lint, source citations, coordinator."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Mock external dependencies before importing project modules
with patch.dict("sys.modules", {
    "agent_framework_github_copilot": MagicMock(),
    "agent_framework": MagicMock(),
    "agent_framework.foundry": MagicMock(),
    "agent_framework.openai": MagicMock(),
    "agent_framework.ollama": MagicMock(),
    "azure.identity": MagicMock(),
}):
    from goals import (
        add_goal,
        get_active_goals,
        get_goal,
        advance_goal,
        complete_goal,
        abandon_goal,
        build_goals_block,
        auto_expire_goals,
    )
    from ingest import (
        _chunk_text,
        _first_line_summary,
        _detect_tags,
        ingest_file,
        ingest_text,
    )
    from second_brain import (
        BrainState,
        add_note,
        lint_brain,
        build_lint_block,
    )
    from workflow import (
        _store_capture,
        run_heartbeat,
    )
    from persona_loader import Persona, load_persona
    from state import AgentState
    from config import AppConfig


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _fresh_state() -> AgentState:
    s = AgentState()
    s.execution_count = 1
    return s


def _fresh_brain() -> BrainState:
    return BrainState()


def _fresh_config() -> AppConfig:
    return AppConfig(agent_name="TestAgent")


# ======================================================================
# A. goals.py
# ======================================================================

class TestAddGoal:
    def test_add_goal_returns_id(self):
        state = _fresh_state()
        gid = add_goal(state, "Deploy v2.0")
        assert gid.startswith("g")
        assert len(state.context["active_goals"]) == 1

    def test_add_goal_fields(self):
        state = _fresh_state()
        gid = add_goal(state, "Fix bug", target_heartbeats=5, priority=3)
        goal = get_goal(state, gid)
        assert goal["description"] == "Fix bug"
        assert goal["target_heartbeats"] == 5
        assert goal["priority"] == 3
        assert goal["status"] == "pending"

    def test_add_multiple_goals(self):
        state = _fresh_state()
        add_goal(state, "A")
        add_goal(state, "B")
        add_goal(state, "C")
        assert len(get_active_goals(state)) == 3


class TestGetActiveGoals:
    def test_excludes_completed(self):
        state = _fresh_state()
        g1 = add_goal(state, "One")
        add_goal(state, "Two")
        complete_goal(state, g1)
        active = get_active_goals(state)
        assert len(active) == 1
        assert active[0]["description"] == "Two"

    def test_sorted_by_priority(self):
        state = _fresh_state()
        add_goal(state, "Low", priority=1)
        add_goal(state, "High", priority=10)
        add_goal(state, "Mid", priority=5)
        active = get_active_goals(state)
        priorities = [g["priority"] for g in active]
        assert priorities == sorted(priorities)


class TestAdvanceGoal:
    def test_advance_increments_heartbeats(self):
        state = _fresh_state()
        gid = add_goal(state, "Task")
        advance_goal(state, gid, "Step 1 done")
        goal = get_goal(state, gid)
        assert goal["heartbeats_spent"] == 1
        assert goal["status"] == "in_progress"

    def test_advance_appends_notes(self):
        state = _fresh_state()
        gid = add_goal(state, "Task")
        advance_goal(state, gid, "Step 1")
        advance_goal(state, gid, "Step 2")
        goal = get_goal(state, gid)
        assert len(goal["progress_notes"]) == 2


class TestCompleteGoal:
    def test_marks_completed(self):
        state = _fresh_state()
        gid = add_goal(state, "Task")
        complete_goal(state, gid)
        goal = get_goal(state, gid)
        assert goal["status"] == "completed"

    def test_complete_missing_goal_no_error(self):
        state = _fresh_state()
        # Should not raise
        complete_goal(state, "nonexistent")


class TestAbandonGoal:
    def test_marks_abandoned(self):
        state = _fresh_state()
        gid = add_goal(state, "Bad idea")
        abandon_goal(state, gid, "Changed priorities")
        goal = get_goal(state, gid)
        assert goal["status"] == "abandoned"


class TestBuildGoalsBlock:
    def test_no_goals_returns_empty(self):
        state = _fresh_state()
        assert build_goals_block(state) == ""

    def test_with_goals_returns_text(self):
        state = _fresh_state()
        add_goal(state, "Research topic X", target_heartbeats=5)
        block = build_goals_block(state)
        assert "ACTIVE GOALS" in block
        assert "Research topic X" in block


class TestAutoExpireGoals:
    def test_expires_overdue_goals(self):
        state = _fresh_state()
        gid = add_goal(state, "Should expire", target_heartbeats=2)
        # Simulate 5 heartbeats spent (> 2 * target)
        goal = get_goal(state, gid)
        goal["heartbeats_spent"] = 5
        goal["status"] = "in_progress"
        auto_expire_goals(state)
        assert get_goal(state, gid)["status"] == "abandoned"

    def test_does_not_expire_healthy_goal(self):
        state = _fresh_state()
        gid = add_goal(state, "Healthy", target_heartbeats=10)
        advance_goal(state, gid, "Progress")
        auto_expire_goals(state)
        assert get_goal(state, gid)["status"] == "in_progress"


# ======================================================================
# B. ingest.py
# ======================================================================

class TestChunkText:
    def test_short_text_single_chunk(self):
        chunks = _chunk_text("Hello world", max_tokens=500)
        assert len(chunks) == 1
        assert chunks[0] == "Hello world"

    def test_long_text_splits(self):
        text = " ".join(f"word{i}" for i in range(1000))
        chunks = _chunk_text(text, max_tokens=100, overlap=10)
        assert len(chunks) > 1

    def test_overlap_present(self):
        text = " ".join(f"w{i}" for i in range(200))
        chunks = _chunk_text(text, max_tokens=50, overlap=10)
        # Last words of chunk 0 should appear at start of chunk 1
        assert len(chunks) >= 2
        words_c0 = chunks[0].split()
        words_c1 = chunks[1].split()
        # Overlap words should be shared
        assert words_c0[-1] in chunks[1]

    def test_empty_text(self):
        chunks = _chunk_text("")
        assert chunks == []


class TestFirstLineSummary:
    def test_extracts_first_line(self):
        text = "First line here\nSecond line\nThird"
        assert _first_line_summary(text) == "First line here"

    def test_truncates_long_line(self):
        text = "A" * 200
        result = _first_line_summary(text)
        assert len(result) <= 120


class TestDetectTags:
    def test_detects_python_extension(self):
        tags = _detect_tags("def hello():\n    pass  # .py file")
        assert "py" in tags or "python" in tags

    def test_detects_domain_keywords(self):
        tags = _detect_tags("This is about insurance claims processing")
        assert "insurance" in tags or "claims" in tags

    def test_no_tags_for_plain_text(self):
        tags = _detect_tags("Hello world")
        # Should still return a list (possibly empty)
        assert isinstance(tags, list)


class TestIngestFile:
    def test_ingest_file_creates_notes(self, tmp_path):
        brain = _fresh_brain()
        md_file = tmp_path / "test.md"
        md_file.write_text("# My Document\n\nSome important knowledge about AI agents.")
        ids = asyncio.run(ingest_file(brain, str(md_file)))
        assert len(brain.notes) >= 1
        # Check source provenance
        first_note = list(brain.notes.values())[0]
        assert "test.md" in str(first_note.get("source", ""))

    def test_ingest_missing_file_no_error(self, tmp_path):
        brain = _fresh_brain()
        ids = asyncio.run(ingest_file(brain, str(tmp_path / "nonexistent.txt")))
        assert len(brain.notes) == 0


class TestIngestText:
    def test_ingest_text_creates_notes(self):
        brain = _fresh_brain()
        asyncio.run(
            ingest_text(brain, "AI agents can use tools to interact with the world", source="manual")
        )
        assert len(brain.notes) >= 1

    def test_ingest_text_with_source(self):
        brain = _fresh_brain()
        asyncio.run(
            ingest_text(brain, "Some text content here", source="api:feed")
        )
        note = list(brain.notes.values())[0]
        assert "api:feed" in str(note.get("source", ""))


# ======================================================================
# C. Brain lint (second_brain.py)
# ======================================================================

class TestLintBrain:
    def test_empty_brain_no_issues(self):
        brain = _fresh_brain()
        issues = lint_brain(brain)
        assert issues == []

    def test_orphan_detection(self):
        brain = _fresh_brain()
        add_note(brain, content="Orphan note with enough content to pass length check")
        issues = lint_brain(brain)
        types = [i["type"] for i in issues]
        assert "orphan" in types

    def test_empty_content_detection(self):
        brain = _fresh_brain()
        add_note(brain, content="x")  # Very short
        issues = lint_brain(brain)
        types = [i["type"] for i in issues]
        assert "empty_content" in types

    def test_low_density_detection(self):
        brain = _fresh_brain()
        add_note(brain, content="Note A with some real content here")
        add_note(brain, content="Note B with some real content here")
        add_note(brain, content="Note C with some real content here")
        # No connections → density = 0
        issues = lint_brain(brain)
        types = [i["type"] for i in issues]
        assert "low_density" in types

    def test_no_orphan_when_connected(self):
        brain = _fresh_brain()
        n1 = add_note(brain, content="Note A has enough content for the check")
        n2 = add_note(brain, content="Note B has enough content for the check")
        from second_brain import connect_notes
        connect_notes(brain, n1, n2, "related")
        issues = lint_brain(brain)
        orphans = [i for i in issues if i["type"] == "orphan" and i["note_id"] in (n1, n2)]
        assert len(orphans) == 0

    def test_stale_detection(self):
        brain = _fresh_brain()
        nid = add_note(brain, content="Old note with long enough content for detection")
        # Backdate to 60 days ago
        old_date = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        brain.notes[nid]["created_at"] = old_date
        issues = lint_brain(brain)
        types = [i["type"] for i in issues]
        assert "stale" in types


class TestBuildLintBlock:
    def test_empty_brain_returns_empty(self):
        brain = _fresh_brain()
        assert build_lint_block(brain) == ""

    def test_issues_produce_text(self):
        brain = _fresh_brain()
        add_note(brain, content="x")  # empty_content + orphan + low_density
        block = build_lint_block(brain)
        assert "BRAIN HEALTH" in block


# ======================================================================
# D. Source citation tracking (workflow.py _store_capture)
# ======================================================================

class TestSourceCitation:
    def test_store_capture_with_provenance(self):
        brain = _fresh_brain()
        raw = json.dumps({
            "topic": "Test",
            "content": "Some insight captured",
            "tags": ["test"],
            "category": "resources",
        })
        note_id = _store_capture(
            brain, raw,
            persona_name="researcher",
            heartbeat_number=42,
        )
        assert note_id is not None
        note = brain.notes[note_id]
        source = note["source"]
        assert isinstance(source, dict)
        assert source["type"] == "heartbeat"
        assert source["persona"] == "researcher"
        assert source["heartbeat_number"] == 42

    def test_store_capture_without_persona_uses_string(self):
        brain = _fresh_brain()
        raw = json.dumps({
            "topic": "Test",
            "content": "Some insight",
            "tags": [],
            "category": "resources",
        })
        note_id = _store_capture(brain, raw)
        note = brain.notes[note_id]
        assert note["source"] == "heartbeat"

    def test_store_capture_fallback_on_bad_json(self):
        brain = _fresh_brain()
        note_id = _store_capture(brain, "not valid json at all")
        assert note_id is not None
        # Fallback should use string source
        note = brain.notes[note_id]
        assert note["source"] == "heartbeat"


# ======================================================================
# E. Multi-persona orchestration (coordinator workflow)
# ======================================================================

class TestCoordinatorWorkflow:
    def test_persona_has_roster_fields(self):
        p = Persona(
            name="coord",
            description="Coordinator",
            instructions="Coordinate stuff",
            workflow="coordinator",
            roster=["researcher", "python_developer"],
            schedule="round_robin",
        )
        assert p.roster == ["researcher", "python_developer"]
        assert p.schedule == "round_robin"

    def test_persona_roster_defaults(self):
        p = Persona(name="x", description="", instructions="")
        assert p.roster == []
        assert p.schedule == "round_robin"

    def test_coordinator_round_robin(self):
        """Coordinator with round_robin runs one persona per heartbeat."""
        brain = _fresh_brain()
        state = _fresh_state()
        config = _fresh_config()
        agent = MagicMock()
        agent.run = AsyncMock(return_value="Synthesis done")

        persona = Persona(
            name="coord",
            description="Coordinator",
            instructions="Coordinate",
            workflow="coordinator",
            roster=["default"],
            schedule="round_robin",
        )

        # We need to mock load_persona to return a simple persona
        default_p = Persona(
            name="default",
            description="Default agent",
            instructions="Default instructions",
            heartbeat_task="Do research",
            workflow="second_brain",
        )
        with patch("workflow.load_persona", return_value=default_p):
            asyncio.run(run_heartbeat(agent, state, brain, config, persona))

        # Agent.run should have been called (status, capture, connect/review from sub + synthesis)
        assert agent.run.call_count >= 2
        # Coordinator index should advance
        assert state.context.get("coordinator_coord_idx", 0) == 1

    def test_coordinator_empty_roster_fallback(self):
        """Empty roster falls back to freeform."""
        brain = _fresh_brain()
        state = _fresh_state()
        config = _fresh_config()
        agent = MagicMock()
        agent.run = AsyncMock(return_value='{"topic":"t","content":"c","tags":[],"category":"resources"}')

        persona = Persona(
            name="coord",
            description="Coordinator",
            instructions="Coordinate",
            workflow="coordinator",
            roster=[],
            schedule="all",
        )

        asyncio.run(run_heartbeat(agent, state, brain, config, persona))
        # Should still complete without error (freeform fallback)
        assert agent.run.call_count >= 1


# ======================================================================
# F. Integration: lint block in workflow prompts
# ======================================================================

class TestLintBlockInWorkflow:
    def test_lint_block_injected_on_10th_heartbeat(self):
        """On heartbeat 10, the lint block should appear in the status prompt."""
        brain = _fresh_brain()
        # Add a note with empty content to trigger lint issues
        add_note(brain, content="x")

        state = _fresh_state()
        state.execution_count = 10  # 10 % 10 == 0
        config = _fresh_config()
        agent = MagicMock()
        agent.run = AsyncMock(return_value='{"topic":"t","content":"c","tags":[],"category":"resources"}')

        persona = Persona(
            name="test",
            description="Test persona",
            instructions="Test",
            heartbeat_task="Do stuff",
            workflow="second_brain",
        )

        asyncio.run(run_heartbeat(agent, state, brain, config, persona))

        # First call should be status_check — its prompt should contain BRAIN HEALTH
        first_call_prompt = agent.run.call_args_list[0][0][0]
        assert "BRAIN HEALTH" in first_call_prompt

    def test_lint_block_absent_on_non_10th_heartbeat(self):
        """On heartbeat 7, no lint block."""
        brain = _fresh_brain()
        add_note(brain, content="x")

        state = _fresh_state()
        state.execution_count = 7
        config = _fresh_config()
        agent = MagicMock()
        agent.run = AsyncMock(return_value='{"topic":"t","content":"c","tags":[],"category":"resources"}')

        persona = Persona(
            name="test",
            description="Test persona",
            instructions="Test",
            heartbeat_task="Do stuff",
            workflow="second_brain",
        )

        asyncio.run(run_heartbeat(agent, state, brain, config, persona))

        first_call_prompt = agent.run.call_args_list[0][0][0]
        assert "BRAIN HEALTH" not in first_call_prompt
