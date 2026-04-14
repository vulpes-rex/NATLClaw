"""Comprehensive tests for workflow.py — all workflow modes and brain helpers.

Covers:
- _store_capture: valid JSON, malformed JSON, markdown-wrapped, dedup, source citations
- _store_connection: valid JSON, invalid IDs, markdown-wrapped
- _run_second_brain_heartbeat: end-to-end with mock agent
- _run_freeform_heartbeat: end-to-end with mock agent
- _run_steps_heartbeat: all-at-once and stepwise pointer
- _run_coordinator_heartbeat: round-robin and all schedule modes
- run_task_heartbeat: plan→execute→check→capture integration
- _distil_to_brain: end-to-end with mock agent
- _relate_cooccurring_tags: topic graph wiring
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Mock external dependencies before importing workflow
with patch.dict("sys.modules", {
    "agent_framework_github_copilot": MagicMock(),
    "agent_framework": MagicMock(),
    "agent_framework.foundry": MagicMock(),
    "agent_framework.openai": MagicMock(),
    "agent_framework.ollama": MagicMock(),
    "azure.identity": MagicMock(),
}):
    import workflow as wf_mod
    from workflow import (
        _distil_to_brain,
        _extract_json,
        _extract_deliverables,
        _relate_cooccurring_tags,
        _run_all_steps,
        _run_coordinator_heartbeat,
        _run_freeform_heartbeat,
        _run_one_step,
        _run_second_brain_heartbeat,
        _run_steps_heartbeat,
        _store_capture,
        _store_connection,
        run_heartbeat,
        run_task_heartbeat,
    )

from config import AppConfig
from persona_loader import Persona
from second_brain import BrainState, add_note
from state import AgentState

logging.basicConfig(level=logging.DEBUG)


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def brain():
    return BrainState()


@pytest.fixture
def state():
    return AgentState(execution_count=1)


@pytest.fixture
def config():
    cfg = MagicMock(spec=AppConfig)
    cfg.provider = "foundry"
    cfg.model = "test-model"
    cfg.agent_name = "TestClaw"
    cfg.heartbeat_interval_sec = 60
    cfg.state_file = "data/test.json"
    cfg.max_history = 100
    cfg.agent_instructions = ""
    return cfg


@pytest.fixture
def persona():
    p = MagicMock(spec=Persona)
    p.name = "tester"
    p.description = "Test persona"
    p.instructions = "Test instructions"
    p.workflow = "second_brain"
    p.heartbeat_task = "Research something interesting"
    p.tools = []
    p.mcp_servers = {}
    p.steps = []
    p.stepwise = False
    p.roster = []
    p.schedule = "round_robin"
    # Tiered memory defaults — disable consolidation/lint in most tests
    p.consolidation_interval = 0
    p.consolidation_threshold = 999
    p.lint_wiki_interval = 0
    return p


def _json_agent(*responses: str) -> MagicMock:
    """Build a mock agent that returns the given texts in sequence."""
    agent = MagicMock()
    agent.run = AsyncMock(
        side_effect=[MagicMock(text=r) for r in responses]
    )
    return agent


# ══════════════════════════════════════════════════════════════════════
# A. _extract_json tests
# ══════════════════════════════════════════════════════════════════════

class TestExtractJson:
    """Test the _extract_json helper used by _store_capture and _store_connection."""

    def test_plain_json(self):
        raw = '{"content": "hello", "tags": []}'
        assert _extract_json(raw) == {"content": "hello", "tags": []}

    def test_code_fenced_json(self):
        raw = '```json\n{"content": "fenced"}\n```'
        assert _extract_json(raw)["content"] == "fenced"

    def test_json_with_preamble_text(self):
        """LLM returns commentary before the JSON object."""
        raw = (
            "Let me first check the workspace.\n\n"
            '{"topic": "Insight", "content": "Real data", "tags": ["a"], "category": "resources"}'
        )
        data = _extract_json(raw, required_key="content")
        assert data is not None
        assert data["content"] == "Real data"

    def test_json_with_tool_artifacts(self):
        """LLM returns tool-call artifacts before the actual JSON."""
        raw = (
            'Based on the output:\n'
            '{"toolu_vrtx_abc": "Tool Output: stuff"}\n'
            '{"topic": "Real", "content": "Correct", "tags": [], "category": "resources"}'
        )
        data = _extract_json(raw, required_key="content")
        assert data is not None
        assert data["content"] == "Correct"

    def test_no_json_returns_none(self):
        assert _extract_json("Just plain text") is None

    def test_empty_string_returns_none(self):
        assert _extract_json("") is None

    def test_no_required_key_accepts_any_dict(self):
        raw = '{"foo": 1}'
        assert _extract_json(raw, required_key="") == {"foo": 1}


# ══════════════════════════════════════════════════════════════════════
# B. _store_capture tests
# ══════════════════════════════════════════════════════════════════════

class TestStoreCapture:
    """Test the _store_capture brain helper."""

    def test_valid_json(self, brain):
        raw = json.dumps({
            "topic": "Testing",
            "content": "Unit tests improve reliability",
            "tags": ["testing", "quality"],
            "category": "resources",
        })
        nid = _store_capture(brain, raw)
        assert nid is not None
        assert nid in brain.notes
        assert brain.notes[nid]["content"] == "Unit tests improve reliability"
        assert brain.notes[nid]["summary"] == "Testing"
        assert "testing" in brain.notes[nid]["tags"]

    def test_markdown_wrapped_json(self, brain):
        raw = '```json\n{"topic": "MD", "content": "Wrapped", "tags": [], "category": "resources"}\n```'
        nid = _store_capture(brain, raw)
        assert nid is not None
        assert brain.notes[nid]["content"] == "Wrapped"

    def test_json_with_preamble(self, brain):
        """LLM emits commentary/tool artifacts before the JSON — should still parse."""
        raw = (
            "Let me gather evidence.\n"
            '{"toolu_response": "Tool Output"}\n'
            '{"topic": "Embedded", "content": "Found it", "tags": ["test"], "category": "resources"}'
        )
        nid = _store_capture(brain, raw)
        assert nid is not None
        assert brain.notes[nid]["content"] == "Found it"
        assert brain.notes[nid]["summary"] == "Embedded"

    def test_malformed_json_fallback(self, brain):
        """Malformed JSON falls back to storing raw content as a note."""
        nid = _store_capture(brain, "Not valid JSON at all")
        assert nid is not None
        assert brain.notes[nid]["content"] == "Not valid JSON at all"
        assert brain.notes[nid]["source"] == "heartbeat"

    def test_empty_string_fallback(self, brain):
        nid = _store_capture(brain, "")
        assert nid is not None  # fallback note created

    def test_dedup_merges_existing(self, brain):
        """Near-duplicate content should merge into the existing note."""
        raw1 = json.dumps({
            "topic": "CI",
            "content": "Parallel tests reduce CI time significantly by running in parallel",
            "tags": ["ci"],
            "category": "projects",
        })
        nid1 = _store_capture(brain, raw1)

        # Second capture with nearly identical content
        raw2 = json.dumps({
            "topic": "CI v2",
            "content": "Parallel tests reduce CI time significantly by running in parallel",
            "tags": ["ci", "performance"],
            "category": "projects",
        })
        nid2 = _store_capture(brain, raw2)

        assert nid2 == nid1, "Near-duplicate should merge into existing note"
        assert "performance" in brain.notes[nid1]["tags"]

    def test_source_citation_metadata(self, brain):
        """When persona_name is provided, source is a structured dict."""
        raw = json.dumps({
            "topic": "Meta",
            "content": "Source tracking test",
            "tags": [],
            "category": "resources",
        })
        nid = _store_capture(
            brain, raw,
            persona_name="researcher",
            heartbeat_number=42,
            step="capture",
        )
        source = brain.notes[nid]["source"]
        assert isinstance(source, dict)
        assert source["type"] == "heartbeat"
        assert source["persona"] == "researcher"
        assert source["heartbeat_number"] == 42
        assert source["step"] == "capture"

    def test_no_persona_source_string(self, brain):
        """When persona_name is empty, source is the string 'heartbeat'."""
        raw = json.dumps({
            "topic": "X",
            "content": "No persona",
            "tags": [],
            "category": "resources",
        })
        nid = _store_capture(brain, raw, persona_name="")
        assert brain.notes[nid]["source"] == "heartbeat"

    def test_topic_graph_wiring(self, brain):
        """Tags are wired into the topic graph."""
        raw = json.dumps({
            "topic": "React hooks",
            "content": "Hooks simplify state management",
            "tags": ["react", "hooks"],
            "category": "resources",
        })
        nid = _store_capture(brain, raw)
        # Topics should exist
        topic_names = {t["name"].lower() for t in brain.topics.values()}
        assert "react" in topic_names
        assert "hooks" in topic_names
        # Note should be assigned to topics
        react_topic = next(t for t in brain.topics.values() if t["name"].lower() == "react")
        assert nid in react_topic.get("note_ids", [])

    def test_missing_content_key(self, brain):
        """JSON without 'content' key should use fallback."""
        raw = json.dumps({"topic": "Oops", "tags": ["test"]})
        nid = _store_capture(brain, raw)
        # content should default to raw[:300]
        assert nid is not None


# ══════════════════════════════════════════════════════════════════════
# B. _store_connection tests
# ══════════════════════════════════════════════════════════════════════

class TestStoreConnection:
    """Test the _store_connection brain helper."""

    def test_valid_connection(self, brain):
        add_note(brain, content="Note A")
        add_note(brain, content="Note B")
        raw = json.dumps({"from": "n0001", "to": "n0002", "reason": "related"})
        _store_connection(brain, raw)
        assert len(brain.connections) == 1
        assert brain.connections[0]["from"] == "n0001"
        assert brain.connections[0]["to"] == "n0002"

    def test_invalid_note_ids(self, brain):
        """Connection with non-existent IDs should be silently ignored."""
        raw = json.dumps({"from": "n9999", "to": "n8888", "reason": "fake"})
        _store_connection(brain, raw)
        assert len(brain.connections) == 0

    def test_malformed_json(self, brain):
        _store_connection(brain, "not json")
        assert len(brain.connections) == 0

    def test_markdown_wrapped(self, brain):
        add_note(brain, content="X")
        add_note(brain, content="Y")
        raw = '```json\n{"from": "n0001", "to": "n0002", "reason": "linked"}\n```'
        _store_connection(brain, raw)
        assert len(brain.connections) == 1

    def test_json_with_preamble(self, brain):
        """LLM emits text before the JSON — should still parse."""
        add_note(brain, content="X")
        add_note(brain, content="Y")
        raw = (
            'I will check the git log.\n'
            '{"from": "n0001", "to": "n0002", "reason": "related work"}'
        )
        _store_connection(brain, raw)
        assert len(brain.connections) == 1
        assert brain.connections[0]["reason"] == "related work"

    def test_missing_from_field(self, brain):
        add_note(brain, content="X")
        raw = json.dumps({"to": "n0001", "reason": "half"})
        _store_connection(brain, raw)
        assert len(brain.connections) == 0

    def test_empty_json_object(self, brain):
        _store_connection(brain, "{}")
        assert len(brain.connections) == 0


# ══════════════════════════════════════════════════════════════════════
# C. _relate_cooccurring_tags tests
# ══════════════════════════════════════════════════════════════════════

class TestRelateCooccurringTags:

    def test_two_tags(self, brain):
        _relate_cooccurring_tags(brain, ["react", "hooks"])
        # Should create both topics and relate them
        assert len(brain.topics) == 2

    def test_three_tags_creates_all_pairs(self, brain):
        _relate_cooccurring_tags(brain, ["a", "b", "c"])
        # 3 topics, each related to the other 2
        assert len(brain.topics) == 3
        for t in brain.topics.values():
            assert len(t.get("related_topics", [])) == 2

    def test_empty_tags(self, brain):
        _relate_cooccurring_tags(brain, [])
        assert len(brain.topics) == 0

    def test_single_tag(self, brain):
        _relate_cooccurring_tags(brain, ["solo"])
        assert len(brain.topics) == 0  # no pairs to relate


# ══════════════════════════════════════════════════════════════════════
# D. _run_second_brain_heartbeat — end-to-end
# ══════════════════════════════════════════════════════════════════════

class TestSecondBrainHeartbeat:

    def test_full_cycle(self, state, brain, config, persona):
        """All 4 steps run: status → capture → connect → review."""
        # Pre-populate brain so connect step triggers (needs ≥ 2 notes)
        add_note(brain, content="Pre-existing knowledge")

        capture_json = json.dumps({
            "topic": "AI agents",
            "content": "Agents need persistent memory",
            "tags": ["ai", "memory"],
            "category": "resources",
        })
        connect_json = json.dumps({
            "from": "n0001",
            "to": "n0002",
            "reason": "related",
        })
        agent = _json_agent(
            "System is healthy",   # status_check
            capture_json,          # capture
            connect_json,          # connect
            "Good heartbeat",      # review
        )

        asyncio.run(_run_second_brain_heartbeat(agent, state, brain, config, persona))

        assert agent.run.call_count == 4
        assert len(brain.notes) >= 2  # pre-existing + capture
        assert brain.last_review is not None
        assert len(brain.review_log) == 1

    def test_fewer_than_two_notes_skips_connect(self, state, brain, config, persona):
        """With <2 recent notes, connect step is skipped (only 3 agent calls)."""
        capture_json = json.dumps({
            "topic": "X",
            "content": "First note ever",
            "tags": [],
            "category": "resources",
        })
        agent = _json_agent(
            "Status ok",
            capture_json,
            # No connect call
            "Review done",
        )

        # Brain starts empty → after capture only 1 note → connect skipped
        asyncio.run(_run_second_brain_heartbeat(agent, state, brain, config, persona))
        assert agent.run.call_count == 3  # status + capture + review (no connect)

    def test_exception_in_step_does_not_crash(self, state, brain, config, persona):
        """Workflow catches errors gracefully."""
        agent = MagicMock()
        agent.run = AsyncMock(side_effect=Exception("boom"))
        # Should not raise
        asyncio.run(_run_second_brain_heartbeat(agent, state, brain, config, persona))

    def test_lint_block_included_on_10th_heartbeat(self, state, brain, config, persona):
        """Lint block is included when execution_count % 10 == 0."""
        state.execution_count = 10
        capture_json = json.dumps({
            "topic": "Lint",
            "content": "Health check noted",
            "tags": [],
            "category": "resources",
        })
        agent = _json_agent("Status", capture_json, "Review")
        asyncio.run(_run_second_brain_heartbeat(agent, state, brain, config, persona))
        # Verify the status prompt contains lint info (brain is empty → low density)
        first_call = agent.run.call_args_list[0]
        prompt = first_call[0][0]
        assert "BRAIN HEALTH" in prompt or "status" in prompt.lower()


# ══════════════════════════════════════════════════════════════════════
# E. _run_freeform_heartbeat — end-to-end
# ══════════════════════════════════════════════════════════════════════

class TestFreeformHeartbeat:

    def test_full_cycle(self, state, brain, config, persona):
        """All 4 steps: status → task → capture → review."""
        capture_json = json.dumps({
            "topic": "Deploy fix",
            "content": "Fixed the pipeline",
            "tags": ["devops"],
            "category": "projects",
        })
        agent = _json_agent(
            "All systems normal",
            "Deployed to staging",
            capture_json,
            "Good cycle",
        )

        asyncio.run(_run_freeform_heartbeat(agent, state, brain, config, persona))
        assert agent.run.call_count == 4
        assert len(brain.notes) >= 1
        assert brain.last_review is not None

    def test_exception_does_not_crash(self, state, brain, config, persona):
        agent = MagicMock()
        agent.run = AsyncMock(side_effect=Exception("task failed"))
        asyncio.run(_run_freeform_heartbeat(agent, state, brain, config, persona))


# ══════════════════════════════════════════════════════════════════════
# F. _run_steps_heartbeat — all-at-once and stepwise
# ══════════════════════════════════════════════════════════════════════

class TestStepsHeartbeat:

    def test_no_steps_falls_back_to_freeform(self, state, brain, config, persona):
        """Empty steps list → falls back to freeform workflow."""
        persona.steps = []
        persona.stepwise = False

        capture_json = json.dumps({
            "topic": "Fallback",
            "content": "Fell back",
            "tags": [],
            "category": "resources",
        })
        agent = _json_agent("status", "task", capture_json, "review")

        asyncio.run(_run_steps_heartbeat(agent, state, brain, config, persona))
        assert agent.run.call_count == 4  # freeform has 4 steps

    def test_all_steps_run_in_sequence(self, state, brain, config, persona):
        """When stepwise=False, all steps run in one heartbeat."""
        persona.steps = [
            {"name": "step_a", "prompt": "Do A. Context: {prev}", "storeToBrain": False},
            {"name": "step_b", "prompt": "Do B. Context: {prev}", "storeToBrain": False},
        ]
        persona.stepwise = False

        agent = _json_agent("result A", "result B")
        asyncio.run(_run_steps_heartbeat(agent, state, brain, config, persona))
        assert agent.run.call_count == 2

    def test_store_to_brain_triggers_distil(self, state, brain, config, persona):
        """Steps with storeToBrain=True trigger an extra distil call."""
        persona.steps = [
            {"name": "research", "prompt": "Research {prev}", "storeToBrain": True},
        ]
        persona.stepwise = False

        distil_json = json.dumps({
            "topic": "Research finding",
            "content": "Found something",
            "tags": ["research"],
            "category": "resources",
        })
        agent = _json_agent("Research result", distil_json)
        asyncio.run(_run_steps_heartbeat(agent, state, brain, config, persona))
        # 1 step + 1 distil = 2 calls
        assert agent.run.call_count == 2
        assert len(brain.notes) >= 1

    def test_stepwise_advances_pointer(self, state, brain, config, persona):
        """Stepwise mode runs one step per heartbeat and advances the pointer."""
        persona.steps = [
            {"name": "s1", "prompt": "Do 1 {prev}"},
            {"name": "s2", "prompt": "Do 2 {prev}"},
            {"name": "s3", "prompt": "Do 3 {prev}"},
        ]
        persona.stepwise = True

        idx_key = f"steps_{persona.name}_idx"

        # Heartbeat 1 — step 0
        agent = _json_agent("result 1")
        asyncio.run(_run_steps_heartbeat(agent, state, brain, config, persona))
        assert state.context.get(idx_key) == 1

        # Heartbeat 2 — step 1
        agent = _json_agent("result 2")
        asyncio.run(_run_steps_heartbeat(agent, state, brain, config, persona))
        assert state.context.get(idx_key) == 2

    def test_stepwise_resets_after_all_complete(self, state, brain, config, persona):
        """Pointer resets to 0 when all steps are done."""
        persona.steps = [
            {"name": "only", "prompt": "Do it {prev}"},
        ]
        persona.stepwise = True

        idx_key = f"steps_{persona.name}_idx"

        # Heartbeat 1 — runs step 0, pointer becomes 1
        agent = _json_agent("done")
        asyncio.run(_run_steps_heartbeat(agent, state, brain, config, persona))
        assert state.context.get(idx_key) == 1

        # Heartbeat 2 — pointer >= total → reset to 0
        agent = _json_agent()  # no call expected (reset path)
        asyncio.run(_run_steps_heartbeat(agent, state, brain, config, persona))
        assert state.context.get(idx_key) == 0

    def test_prev_placeholder_substitution(self, state, brain, config, persona):
        """Steps receive the previous step's output via {prev}."""
        persona.steps = [
            {"name": "a", "prompt": "Start {prev}"},
            {"name": "b", "prompt": "Continue: {prev}"},
        ]
        persona.stepwise = False

        agent = _json_agent("output_A", "output_B")
        asyncio.run(_run_steps_heartbeat(agent, state, brain, config, persona))

        # Second call should contain the first step's result in its prompt
        second_prompt = agent.run.call_args_list[1][0][0]
        assert "output_A" in second_prompt

    def test_brain_placeholder_substitution(self, state, brain, config, persona):
        """Steps receive brain summary via {brain}."""
        add_note(brain, content="Existing knowledge")
        persona.steps = [
            {"name": "a", "prompt": "Brain context: {brain}"},
        ]
        persona.stepwise = False

        agent = _json_agent("ok")
        asyncio.run(_run_steps_heartbeat(agent, state, brain, config, persona))

        prompt = agent.run.call_args_list[0][0][0]
        assert "SECOND BRAIN" in prompt


# ══════════════════════════════════════════════════════════════════════
# G. _run_coordinator_heartbeat
# ══════════════════════════════════════════════════════════════════════

class TestCoordinatorHeartbeat:

    def test_round_robin_selects_one(self, state, brain, config, persona):
        """Round-robin mode runs one persona per heartbeat, cycling."""
        persona.roster = ["researcher", "developer"]
        persona.schedule = "round_robin"

        # Mock both the sub-persona loader and run_heartbeat
        mock_sub = MagicMock()
        mock_sub.workflow = "second_brain"

        agent = _json_agent("synthesis result")

        with patch.object(wf_mod, "load_persona", return_value=mock_sub), \
             patch.object(wf_mod, "run_heartbeat", new_callable=AsyncMock):
            asyncio.run(_run_coordinator_heartbeat(agent, state, brain, config, persona))

        # Round robin at idx=0 → "researcher" selected first
        idx_key = f"coordinator_{persona.name}_idx"
        assert state.context.get(idx_key) == 1

    def test_round_robin_cycles(self, state, brain, config, persona):
        """Second call picks the next persona."""
        persona.roster = ["a", "b", "c"]
        persona.schedule = "round_robin"
        idx_key = f"coordinator_{persona.name}_idx"
        state.context[idx_key] = 2  # already ran a and b

        agent = _json_agent("synth")
        mock_sub = MagicMock()

        with patch.object(wf_mod, "load_persona", return_value=mock_sub), \
             patch.object(wf_mod, "run_heartbeat", new_callable=AsyncMock):
            asyncio.run(_run_coordinator_heartbeat(agent, state, brain, config, persona))

        assert state.context[idx_key] == 3  # advanced

    def test_all_schedule_runs_every_persona(self, state, brain, config, persona):
        """Schedule=all runs all roster personas."""
        persona.roster = ["x", "y"]
        persona.schedule = "all"

        agent = _json_agent("synth")
        call_log = []

        async def mock_run_hb(ag, st, br, cfg, pers):
            call_log.append(pers.name)

        mock_sub_x = MagicMock()
        mock_sub_x.name = "x"
        mock_sub_x.workflow = "second_brain"
        mock_sub_y = MagicMock()
        mock_sub_y.name = "y"
        mock_sub_y.workflow = "second_brain"

        def load_side(name):
            return mock_sub_x if name == "x" else mock_sub_y

        with patch.object(wf_mod, "load_persona", side_effect=load_side), \
             patch.object(wf_mod, "run_heartbeat", side_effect=mock_run_hb):
            asyncio.run(_run_coordinator_heartbeat(agent, state, brain, config, persona))

        assert len(call_log) == 2

    def test_empty_roster_falls_back(self, state, brain, config, persona):
        """Empty roster falls back to freeform workflow."""
        persona.roster = []
        capture_json = json.dumps({
            "topic": "Fallback",
            "content": "Coordinator fallback",
            "tags": [],
            "category": "resources",
        })
        agent = _json_agent("status", "task", capture_json, "review")

        asyncio.run(_run_coordinator_heartbeat(agent, state, brain, config, persona))
        assert agent.run.call_count == 4  # freeform

    def test_sub_persona_failure_doesnt_crash(self, state, brain, config, persona):
        """A failing sub-persona is logged but doesn't stop the coordinator."""
        persona.roster = ["broken"]
        persona.schedule = "all"

        agent = _json_agent("synth")

        with patch.object(wf_mod, "load_persona", side_effect=Exception("bad persona")):
            asyncio.run(_run_coordinator_heartbeat(agent, state, brain, config, persona))

        # Synthesis step still ran
        assert agent.run.call_count == 1
        assert brain.last_review is not None


# ══════════════════════════════════════════════════════════════════════
# H. _distil_to_brain
# ══════════════════════════════════════════════════════════════════════

class TestDistilToBrain:

    def test_stores_note(self, state, brain):
        distil_json = json.dumps({
            "topic": "Distilled",
            "content": "Key insight from output",
            "tags": ["insight"],
            "category": "resources",
        })
        agent = _json_agent(distil_json)

        asyncio.run(_distil_to_brain(agent, state, brain, "test_step", "Some output"))
        assert len(brain.notes) == 1

    def test_exception_does_not_crash(self, state, brain):
        agent = MagicMock()
        agent.run = AsyncMock(side_effect=Exception("distil failed"))
        asyncio.run(_distil_to_brain(agent, state, brain, "test_step", "output"))
        # Should not raise

    def test_persona_name_passed_through(self, state, brain):
        distil_json = json.dumps({
            "topic": "T",
            "content": "C",
            "tags": [],
            "category": "resources",
        })
        agent = _json_agent(distil_json)

        asyncio.run(_distil_to_brain(
            agent, state, brain, "step", "output", persona_name="dev"
        ))
        note = list(brain.notes.values())[0]
        assert note["source"]["persona"] == "dev"


# ══════════════════════════════════════════════════════════════════════
# I. run_heartbeat dispatcher
# ══════════════════════════════════════════════════════════════════════

class TestRunHeartbeatDispatcher:

    def test_dispatches_to_second_brain(self, state, brain, config, persona):
        persona.workflow = "second_brain"
        with patch.object(wf_mod, "_run_second_brain_heartbeat", new_callable=AsyncMock) as m:
            asyncio.run(run_heartbeat(MagicMock(), state, brain, config, persona))
            m.assert_called_once()

    def test_dispatches_to_freeform(self, state, brain, config, persona):
        persona.workflow = "freeform"
        with patch.object(wf_mod, "_run_freeform_heartbeat", new_callable=AsyncMock) as m:
            asyncio.run(run_heartbeat(MagicMock(), state, brain, config, persona))
            m.assert_called_once()

    def test_dispatches_to_steps(self, state, brain, config, persona):
        persona.workflow = "steps"
        with patch.object(wf_mod, "_run_steps_heartbeat", new_callable=AsyncMock) as m:
            asyncio.run(run_heartbeat(MagicMock(), state, brain, config, persona))
            m.assert_called_once()

    def test_dispatches_to_coordinator(self, state, brain, config, persona):
        persona.workflow = "coordinator"
        with patch.object(wf_mod, "_run_coordinator_heartbeat", new_callable=AsyncMock) as m:
            asyncio.run(run_heartbeat(MagicMock(), state, brain, config, persona))
            m.assert_called_once()

    def test_unknown_mode_defaults_to_second_brain(self, state, brain, config, persona):
        persona.workflow = "unknown_xyz"
        with patch.object(wf_mod, "_run_second_brain_heartbeat", new_callable=AsyncMock) as m:
            asyncio.run(run_heartbeat(MagicMock(), state, brain, config, persona))
            m.assert_called_once()


# ══════════════════════════════════════════════════════════════════════
# J. run_task_heartbeat — integration tests
# ══════════════════════════════════════════════════════════════════════

class TestRunTaskHeartbeat:
    """Integration tests for the plan → execute → check → capture flow."""

    def _make_task(self, **overrides):
        from tasks import create_task
        kwargs = {"title": "Fix the login bug", "description": "Users can't log in", "priority": "high"}
        kwargs.update(overrides)
        return create_task(**kwargs)

    def test_done_verdict_completes_task(self, state, brain, config, persona):
        """DONE verdict marks task as completed and captures insight."""
        task = self._make_task()
        capture_json = json.dumps({
            "topic": "Login fix",
            "content": "Fixed auth token refresh",
            "tags": ["auth", "bugfix"],
            "category": "projects",
        })
        agent = _json_agent(
            "Plan: Check the token refresh logic",  # plan
            "Fixed the token refresh in auth.py",    # execute
            "DONE\n- src/auth.py\n- tests/test_auth.py",  # check verdict
            capture_json,                            # capture
        )

        asyncio.run(run_task_heartbeat(agent, state, brain, config, persona, task))

        assert task.status == "completed"
        assert task.completed_at is not None
        assert agent.run.call_count == 4
        # Deliverables should include file paths from verdict
        assert any("auth.py" in d for d in task.deliverables)
        # Insight should be captured in brain
        assert len(brain.notes) >= 1

    def test_continue_verdict_advances_task(self, state, brain, config, persona):
        """CONTINUE verdict increments heartbeats and records progress."""
        task = self._make_task()
        capture_json = json.dumps({
            "topic": "Partial progress",
            "content": "Identified the root cause",
            "tags": ["debug"],
            "category": "resources",
        })
        agent = _json_agent(
            "Plan: Investigate the error logs",      # plan
            "Found error in connection pooling",     # execute
            "CONTINUE — need another cycle to fix",  # check verdict
            capture_json,                            # capture
        )

        asyncio.run(run_task_heartbeat(agent, state, brain, config, persona, task))

        assert task.status == "in_progress"
        assert task.heartbeats_spent == 1
        assert len(task.progress_notes) >= 1

    def test_blocked_verdict_blocks_task(self, state, brain, config, persona):
        """BLOCKED verdict sets task to blocked with a question."""
        task = self._make_task()
        capture_json = json.dumps({
            "topic": "Blocked",
            "content": "Need database credentials",
            "tags": ["blocked"],
            "category": "resources",
        })
        agent = _json_agent(
            "Plan: Connect to the database",                # plan
            "Cannot connect — missing credentials",         # execute
            "BLOCKED: What are the database credentials?",  # check verdict
            capture_json,                                   # capture
        )

        asyncio.run(run_task_heartbeat(agent, state, brain, config, persona, task))

        assert task.status == "blocked"
        assert len(task.questions) == 1
        assert "credentials" in task.questions[0]["question"].lower()

    def test_failed_verdict_fails_task(self, state, brain, config, persona):
        """FAILED verdict marks the task as failed."""
        task = self._make_task()
        capture_json = json.dumps({
            "topic": "Failure",
            "content": "Incompatible API version",
            "tags": ["failure"],
            "category": "resources",
        })
        agent = _json_agent(
            "Plan: Upgrade the API client",          # plan
            "API v1 is deprecated, v2 incompatible", # execute
            "FAILED: API v2 has breaking changes",   # check verdict
            capture_json,                            # capture
        )

        asyncio.run(run_task_heartbeat(agent, state, brain, config, persona, task))

        assert task.status == "failed"
        assert task.completed_at is not None

    def test_task_transitions_to_in_progress(self, state, brain, config, persona):
        """Task should be moved to in_progress at start of heartbeat."""
        task = self._make_task()
        assert task.status == "pending"

        capture_json = json.dumps({
            "topic": "Test", "content": "Test", "tags": [], "category": "resources"
        })
        agent = _json_agent("plan", "execute", "CONTINUE", capture_json)

        asyncio.run(run_task_heartbeat(agent, state, brain, config, persona, task))

        # start_task sets in_progress; CONTINUE keeps it there
        assert task.status == "in_progress"
        assert task.started_at is not None

    def test_capture_stores_note_in_brain(self, state, brain, config, persona):
        """The capture step should produce a note linked to the task."""
        task = self._make_task()
        capture_json = json.dumps({
            "topic": "Auth insight",
            "content": "Token refresh needs 30s buffer",
            "tags": ["auth"],
            "category": "resources",
        })
        agent = _json_agent("plan", "execute", "DONE", capture_json)

        asyncio.run(run_task_heartbeat(agent, state, brain, config, persona, task))

        assert len(brain.notes) >= 1
        # Task should have a note:xxx deliverable
        note_deliverables = [d for d in task.deliverables if d.startswith("note:")]
        assert len(note_deliverables) >= 1

    def test_exception_during_heartbeat_advances_with_error(self, state, brain, config, persona):
        """Exceptions in the heartbeat should be caught and logged as progress."""
        task = self._make_task()
        agent = MagicMock()
        agent.run = AsyncMock(side_effect=Exception("LLM timeout"))

        asyncio.run(run_task_heartbeat(agent, state, brain, config, persona, task))

        # Task should have an error note
        assert any("ERROR" in n for n in task.progress_notes)

    def test_multiple_heartbeats_accumulate_progress(self, state, brain, config, persona):
        """Running multiple heartbeats on the same task accumulates notes."""
        task = self._make_task()

        for i in range(3):
            capture_json = json.dumps({
                "topic": f"Step {i}", "content": f"Progress {i}",
                "tags": [], "category": "resources",
            })
            agent = _json_agent(
                f"Plan step {i}", f"Execute step {i}", "CONTINUE", capture_json
            )
            asyncio.run(run_task_heartbeat(agent, state, brain, config, persona, task))

        assert task.heartbeats_spent == 3
        assert len(task.progress_notes) >= 3
        assert task.status == "in_progress"

    def test_blocked_answered_resumed_to_done_round_trip(self, state, brain, config, persona):
        """Blocked task can be answered and then completed in a later heartbeat."""
        from tasks import answer_task

        task = self._make_task()

        first_capture = json.dumps({
            "topic": "Blocked on config",
            "content": "Need environment value from developer",
            "tags": ["blocked"],
            "category": "resources",
        })
        blocked_agent = _json_agent(
            "Plan: read deployment config",
            "Config key is missing from env",
            "BLOCKED: What should APP_REGION be set to?",
            first_capture,
        )

        asyncio.run(run_task_heartbeat(blocked_agent, state, brain, config, persona, task))
        assert task.status == "blocked"
        assert len(task.questions) == 1
        assert "APP_REGION" in task.questions[0]["question"]

        answer_task(task, "Set APP_REGION=us-east")
        assert task.status == "assigned"
        assert len(task.answers) == 1

        second_capture = json.dumps({
            "topic": "Region configured",
            "content": "Applied APP_REGION and validated deployment path",
            "tags": ["config", "deploy"],
            "category": "projects",
        })
        resumed_agent = _json_agent(
            "Plan: apply provided region value",
            "Updated env and verified startup",
            "DONE\n- src/config.py\n- docs/deploy.md",
            second_capture,
        )

        asyncio.run(run_task_heartbeat(resumed_agent, state, brain, config, persona, task))
        assert task.status == "completed"
        assert task.completed_at is not None
        assert any("config.py" in d for d in task.deliverables)
        assert len(task.answers) == 1


# ══════════════════════════════════════════════════════════════════════
# K. _extract_deliverables tests
# ══════════════════════════════════════════════════════════════════════

class TestExtractDeliverables:

    def test_extracts_file_paths(self):
        verdict = "DONE\n- src/auth.py\n- tests/test_auth.py\n- Updated the login flow"
        result = _extract_deliverables(verdict)
        assert "src/auth.py" in result
        assert "tests/test_auth.py" in result

    def test_extracts_note_ids(self):
        verdict = "DONE\n- note:n0042\n- n0001"
        result = _extract_deliverables(verdict)
        assert "note:n0042" in result
        assert "n0001" in result

    def test_empty_verdict(self):
        assert _extract_deliverables("DONE") == []

    def test_limits_to_20(self):
        lines = "\n".join(f"- file{i}.py" for i in range(30))
        result = _extract_deliverables(f"DONE\n{lines}")
        assert len(result) <= 20

    def test_truncates_long_paths(self):
        long_path = "a/" * 200 + "file.py"
        verdict = f"DONE\n- {long_path}"
        result = _extract_deliverables(verdict)
        assert len(result[0]) <= 200
