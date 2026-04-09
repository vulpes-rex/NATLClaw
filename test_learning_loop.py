"""Learning-loop validation tests.

Unlike test_integration.py which validates plumbing (data flows, serialization,
step ordering), these tests validate that the system actually LEARNS:

  1. Knowledge from heartbeat N appears in heartbeat N+1's prompts
  2. Brain content is injected into agent prompts (not just generated)
  3. Lessons extracted from errors appear in future context blocks
  4. The enrichment pipeline (state + brain → enriched instructions → agent)
     composes correctly across the scheduler boundary
  5. Accumulated knowledge changes what the agent sees over time

The mock agents here CAPTURE their inputs so we can assert on the prompts
the system actually constructs — not just what the agent returns.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile

import pytest
import sys
from unittest.mock import MagicMock, patch, AsyncMock, call

# ---------------------------------------------------------------------------
# Mock only the external LLM SDKs
# ---------------------------------------------------------------------------
sys.modules.setdefault('copilot', MagicMock())
sys.modules.setdefault('agent_framework_github_copilot', MagicMock())
sys.modules.setdefault('agent_framework', MagicMock())
sys.modules.setdefault('agent_framework.foundry', MagicMock())
sys.modules.setdefault('agent_framework.openai', MagicMock())
sys.modules.setdefault('agent_framework.ollama', MagicMock())
sys.modules.setdefault('azure.identity', MagicMock())

from state import AgentState, load_state, save_state
from second_brain import (
    BrainState, add_note, connect_notes,
    build_brain_summary, load_brain, save_brain,
)
from workflow import (
    _run_step, _store_capture, _store_connection,
    _run_second_brain_heartbeat, _run_freeform_heartbeat,
    run_heartbeat,
)
from learning import extract_lessons, build_context_block
from config import AppConfig
from persona_loader import load_persona


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def tmp_data_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def state_file(tmp_data_dir):
    return os.path.join(tmp_data_dir, "agent_state.json")


def _make_capturing_agent(responses: list[str]):
    """Create a mock agent that records every prompt it receives.

    Returns (agent, captured_prompts_list).
    The captured list fills up as agent.run() is called.
    """
    captured = []
    idx = [0]

    async def _run(prompt):
        captured.append(prompt)
        text = responses[idx[0]] if idx[0] < len(responses) else "ok"
        idx[0] += 1
        return MagicMock(text=text)

    agent = MagicMock()
    agent.run = _run
    return agent, captured


# ═══════════════════════════════════════════════════════════════════════
# 1. Brain content appears in the prompts the agent receives
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.learning_loop
class TestBrainFeedsIntoPrompts:
    """Verify that existing brain notes are injected into workflow prompts."""

    def test_brain_notes_appear_in_status_prompt(self):
        """The status_check prompt includes the brain summary with existing notes."""
        state = AgentState(execution_count=1)
        brain = BrainState()
        add_note(brain, content="Docker containers should use health checks",
                 summary="docker health checks", tags=["devops"], category="areas")
        add_note(brain, content="Python asyncio tasks need proper cancellation",
                 summary="asyncio cancellation", tags=["python"], category="resources")

        config = AppConfig(agent_name="TestClaw", persona="default")
        persona = load_persona("default")

        agent, captured = _make_capturing_agent([
            "Status OK.",
            '{"topic": "t", "content": "c", "tags": [], "category": "resources"}',
            "No connections.",
            "Review done.",
        ])

        asyncio.get_event_loop().run_until_complete(
            _run_second_brain_heartbeat(agent, state, brain, config, persona)
        )

        # The FIRST prompt (status_check) must contain brain notes
        status_prompt = captured[0]
        assert "docker health checks" in status_prompt, \
            f"Brain note 'docker health checks' not found in status prompt:\n{status_prompt[:500]}"
        assert "asyncio cancellation" in status_prompt, \
            f"Brain note 'asyncio cancellation' not found in status prompt:\n{status_prompt[:500]}"

    def test_brain_notes_appear_in_capture_prompt(self):
        """The capture prompt includes the brain summary so the agent can avoid duplicates."""
        state = AgentState(execution_count=1)
        brain = BrainState()
        add_note(brain, content="React hooks simplify state management",
                 summary="react hooks", tags=["react"], category="resources")

        config = AppConfig(agent_name="TestClaw", persona="default")
        persona = load_persona("default")

        agent, captured = _make_capturing_agent([
            "Status OK.",
            '{"topic": "new topic", "content": "fresh insight", "tags": [], "category": "resources"}',
            "No conn.",
            "Done.",
        ])

        asyncio.get_event_loop().run_until_complete(
            _run_second_brain_heartbeat(agent, state, brain, config, persona)
        )

        # The SECOND prompt (capture) must include the brain summary
        capture_prompt = captured[1]
        assert "react hooks" in capture_prompt, \
            f"Brain note 'react hooks' not found in capture prompt:\n{capture_prompt[:500]}"
        # It should also mention the current brain size
        assert "1 notes" in capture_prompt or "has 1" in capture_prompt

    def test_recent_note_ids_appear_in_connect_prompt(self):
        """The connect prompt lists recent note IDs so the agent can link them."""
        state = AgentState(execution_count=1)
        brain = BrainState()
        add_note(brain, content="Note A", summary="first note", tags=["a"])
        add_note(brain, content="Note B", summary="second note", tags=["b"])

        config = AppConfig(agent_name="TestClaw", persona="default")
        persona = load_persona("default")

        agent, captured = _make_capturing_agent([
            "Status OK.",
            '{"topic": "Note C", "content": "third insight", "tags": ["c"], "category": "resources"}',
            '{"from": "n0001", "to": "n0002", "reason": "related"}',
            "Review done.",
        ])

        asyncio.get_event_loop().run_until_complete(
            _run_second_brain_heartbeat(agent, state, brain, config, persona)
        )

        # The THIRD prompt (connect) must list note IDs
        connect_prompt = captured[2]
        assert "n0001" in connect_prompt
        assert "n0002" in connect_prompt
        assert "n0003" in connect_prompt  # newly captured note


# ═══════════════════════════════════════════════════════════════════════
# 2. Knowledge from heartbeat N enriches heartbeat N+1
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.learning_loop
class TestCrossHeartbeatLearning:
    """Run multiple heartbeats and verify knowledge accumulates in prompts."""

    def test_heartbeat_2_sees_heartbeat_1_knowledge(self, state_file):
        """Brain notes captured in heartbeat 1 appear in heartbeat 2's prompts."""
        loop = asyncio.get_event_loop()
        config = AppConfig(agent_name="TestClaw", persona="default", state_file=state_file)
        persona = load_persona("default")

        # ── Heartbeat 1: capture a specific insight ──
        state = AgentState(execution_count=1)
        brain = BrainState()

        agent1, _ = _make_capturing_agent([
            "Initial status. System starting up.",
            '{"topic": "Kubernetes pod scheduling", "content": "Pod affinity rules improve availability", "tags": ["k8s", "reliability"], "category": "areas"}',
            "No connections yet.",
            "First heartbeat complete.",
        ])

        loop.run_until_complete(
            _run_second_brain_heartbeat(agent1, state, brain, config, persona)
        )

        # Persist
        loop.run_until_complete(save_state(state, state_file, config.max_history))
        loop.run_until_complete(save_brain(brain, state_file))

        # ── Heartbeat 2: load persisted state, verify it sees heartbeat 1's knowledge ──
        state2 = loop.run_until_complete(load_state(state_file))
        brain2 = loop.run_until_complete(load_brain(state_file))
        state2.execution_count += 1

        agent2, captured2 = _make_capturing_agent([
            "Status: brain has knowledge from previous cycle.",
            '{"topic": "New insight", "content": "Something new", "tags": [], "category": "resources"}',
            '{"from": "n0001", "to": "n0002", "reason": "both operational"}',
            "Second heartbeat done.",
        ])

        loop.run_until_complete(
            _run_second_brain_heartbeat(agent2, state2, brain2, config, persona)
        )

        # Heartbeat 2's status prompt MUST mention heartbeat 1's captured note
        hb2_status_prompt = captured2[0]
        assert "Kubernetes pod scheduling" in hb2_status_prompt, \
            f"Heartbeat 1 knowledge not found in heartbeat 2 status prompt:\n{hb2_status_prompt[:600]}"

        # Heartbeat 2's capture prompt should also reference it
        hb2_capture_prompt = captured2[1]
        assert "Kubernetes pod scheduling" in hb2_capture_prompt or "n0001" in hb2_capture_prompt

    def test_three_heartbeats_accumulate_progressively(self, state_file):
        """Each successive heartbeat sees more knowledge in its prompts."""
        loop = asyncio.get_event_loop()
        config = AppConfig(agent_name="TestClaw", persona="default", state_file=state_file)
        persona = load_persona("default")

        insights = [
            ("Docker networking", "Bridge networks isolate container traffic"),
            ("Python GIL", "GIL limits CPU-bound threading in CPython"),
            ("React suspense", "Suspense boundaries improve loading UX"),
        ]

        all_captured_prompts = []

        for i, (topic, content) in enumerate(insights):
            state = loop.run_until_complete(load_state(state_file))
            brain = loop.run_until_complete(load_brain(state_file))
            state.execution_count += 1

            agent, captured = _make_capturing_agent([
                f"Status for heartbeat {i+1}.",
                json.dumps({"topic": topic, "content": content, "tags": [f"hb{i+1}"], "category": "resources"}),
                "No new connections.",
                f"Review for heartbeat {i+1}.",
            ])

            loop.run_until_complete(
                _run_second_brain_heartbeat(agent, state, brain, config, persona)
            )

            loop.run_until_complete(save_state(state, state_file, config.max_history))
            loop.run_until_complete(save_brain(brain, state_file))
            all_captured_prompts.append(captured)

        # Heartbeat 2 should see heartbeat 1's "Docker networking"
        hb2_prompts = all_captured_prompts[1]
        assert "Docker networking" in hb2_prompts[0], \
            "Heartbeat 2 status prompt doesn't contain heartbeat 1 knowledge"

        # Heartbeat 3 should see BOTH "Docker networking" AND "Python GIL"
        hb3_prompts = all_captured_prompts[2]
        hb3_status = hb3_prompts[0]
        assert "Docker networking" in hb3_status, \
            "Heartbeat 3 doesn't see heartbeat 1 knowledge"
        assert "Python GIL" in hb3_status, \
            "Heartbeat 3 doesn't see heartbeat 2 knowledge"


# ═══════════════════════════════════════════════════════════════════════
# 3. Lessons learned feed back into context
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.learning_loop
class TestLessonsEnrichContext:
    """Verify that lessons extracted from agent responses appear in future context."""

    def test_error_lesson_appears_in_context_block(self):
        """An error in step 1 generates a lesson that appears in the context block."""
        state = AgentState(execution_count=1)

        # Simulate a step that produced an error
        state.execution_history.append({
            "timestamp": "2026-04-07T10:00:00Z",
            "step": "deploy",
            "prompt": "Deploy to production",
            "response": "Error: failed to connect to the database cluster",
        })
        lessons = extract_lessons(
            "deploy", "Deploy to production",
            "Error: failed to connect to the database cluster"
        )
        state.lessons_learned.extend(lessons)

        # Now build the context block that would be injected into the next prompt
        context = build_context_block(state)

        # The error lesson MUST appear
        assert "error_encountered" in context
        assert "deploy" in context
        assert "database" in context.lower() or "failed to connect" in context

    def test_success_lesson_appears_in_context_block(self):
        """A success signal generates a lesson that's visible in future context."""
        state = AgentState(execution_count=2)

        lessons = extract_lessons(
            "migration", "Run database migration",
            "Migration completed successfully in 45 seconds"
        )
        state.lessons_learned.extend(lessons)

        context = build_context_block(state)
        assert "success_achieved" in context
        assert "migration" in context

    def test_multiple_lessons_all_visible(self):
        """Lessons from different steps all appear in the context block."""
        state = AgentState(execution_count=3)

        # Error from step 1
        state.lessons_learned.extend(extract_lessons(
            "build", "Build project", "Error: compilation failed due to syntax error"
        ))
        # Warning from step 2
        state.lessons_learned.extend(extract_lessons(
            "lint", "Lint code", "Warning: 12 unused imports found"
        ))
        # Success from step 3
        state.lessons_learned.extend(extract_lessons(
            "test", "Run tests", "All 47 tests completed successfully"
        ))

        context = build_context_block(state)
        assert "error_encountered" in context
        assert "warning_noted" in context
        assert "success_achieved" in context

    def test_lessons_from_heartbeat_1_appear_in_heartbeat_2_prompts(self, state_file):
        """The complete loop: error in HB1 → lesson → context block → HB2 prompt."""
        loop = asyncio.get_event_loop()
        config = AppConfig(agent_name="TestClaw", persona="default", state_file=state_file)
        persona = load_persona("default")

        # ── Heartbeat 1: agent produces an error response ──
        state = AgentState(execution_count=1)
        brain = BrainState()

        agent1, _ = _make_capturing_agent([
            "Error: failed to reach the monitoring endpoint. Traceback follows.",
            '{"topic": "monitoring gap", "content": "Endpoint unreachable", "tags": ["error"], "category": "areas"}',
            "No connections.",
            "Review: encountered monitoring failure.",
        ])

        loop.run_until_complete(
            _run_second_brain_heartbeat(agent1, state, brain, config, persona)
        )
        loop.run_until_complete(save_state(state, state_file, config.max_history))
        loop.run_until_complete(save_brain(brain, state_file))

        # Verify a lesson was extracted from the error response
        error_lessons = [l for l in state.lessons_learned if l["type"] == "error_encountered"]
        assert len(error_lessons) >= 1, "No error lesson was extracted from the error response"

        # ── Heartbeat 2: verify the context block injected into prompts contains the lesson ──
        state2 = loop.run_until_complete(load_state(state_file))
        brain2 = loop.run_until_complete(load_brain(state_file))

        # Build the context block as the scheduler would
        context_block = build_context_block(state2)
        assert "error_encountered" in context_block, \
            f"Error lesson from HB1 not in context block:\n{context_block}"

        # Now verify it would appear in enriched instructions (scheduler path)
        brain_block = build_brain_summary(brain2, max_notes=5)
        enriched = f"{persona.instructions}\n\n{context_block}\n\n{brain_block}"

        assert "error_encountered" in enriched
        assert "monitoring" in enriched.lower()


# ═══════════════════════════════════════════════════════════════════════
# 4. Enrichment pipeline (scheduler path)
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.learning_loop
class TestEnrichmentPipeline:
    """Test that the scheduler correctly composes enriched instructions."""

    def test_enriched_instructions_contain_state_and_brain(self, state_file):
        """Replicate the scheduler's enrichment logic and verify composition."""
        # Simulate accumulated state from prior heartbeats
        state = AgentState(
            execution_count=5,
            last_heartbeat="2026-04-07T15:00:00Z",
            execution_history=[
                {"timestamp": "2026-04-07T14:00:00Z", "step": "capture",
                 "prompt": "p", "response": "Discovered API rate limiting best practices"},
            ],
            lessons_learned=[
                {"type": "warning_noted", "step": "api_check",
                 "description": "Warning signal during 'api_check': Caution: rate limit at 80%",
                 "timestamp": "2026-04-07T14:30:00Z"},
            ],
        )

        brain = BrainState()
        add_note(brain, content="API rate limiting should use exponential backoff",
                 summary="rate limit backoff", tags=["api", "reliability"], category="resources")
        add_note(brain, content="Circuit breaker pattern prevents cascade failures",
                 summary="circuit breaker", tags=["resilience"], category="areas")

        # This is exactly what scheduler.py lines 78-86 do
        persona = load_persona("default")
        base_instructions = persona.instructions
        context_block = build_context_block(state)
        brain_block = build_brain_summary(brain, max_notes=5)
        enriched = f"{base_instructions}\n\n{context_block}\n\n{brain_block}"

        # Verify ALL three components are present
        assert "AGENT MEMORY" in enriched, "Context block missing"
        assert "SECOND BRAIN" in enriched, "Brain summary missing"
        assert len(base_instructions) > 0 and base_instructions in enriched, \
            "Base persona instructions missing"

        # Verify specific accumulated knowledge is present
        assert "rate limit backoff" in enriched
        assert "circuit breaker" in enriched
        assert "warning_noted" in enriched
        assert "Total executions: 5" in enriched
        assert "rate limit at 80%" in enriched

    def test_enriched_instructions_passed_to_create_agent(self, state_file):
        """In the real scheduler, enriched instructions are passed to create_agent."""
        from scheduler import run_scheduler

        config = AppConfig(
            provider="foundry",
            model="test-model",
            agent_name="TestClaw",
            persona="default",
            state_file=state_file,
            heartbeat_interval_sec=1,
        )

        # Pre-populate state and brain with knowledge
        state = AgentState(
            execution_count=3,
            lessons_learned=[{
                "type": "error_encountered", "step": "deploy",
                "description": "Error signal during 'deploy': Traceback: connection refused",
                "timestamp": "2026-04-07T12:00:00Z",
            }],
        )

        brain = BrainState()
        add_note(brain, content="Deployment requires VPN connection",
                 summary="VPN required for deploy", tags=["devops"], category="areas")

        # Track what create_agent receives
        captured_instructions = []

        def mock_create_agent(cfg, instructions, **kwargs):
            captured_instructions.append(instructions)
            agent = MagicMock()
            agent.run = AsyncMock(return_value=MagicMock(text="ok"))
            return agent

        # Mock all I/O to avoid run_in_executor sensitivity to stale executors
        with patch("scheduler.create_agent", side_effect=mock_create_agent), \
             patch("scheduler.load_state", new_callable=AsyncMock, return_value=state), \
             patch("scheduler.load_brain", new_callable=AsyncMock, return_value=brain), \
             patch("scheduler.save_state", new_callable=AsyncMock), \
             patch("scheduler.save_brain", new_callable=AsyncMock), \
             patch("scheduler.asyncio.sleep", new_callable=AsyncMock):
            asyncio.run(run_scheduler(config, max_iterations=1))

        # create_agent was called with enriched instructions
        assert len(captured_instructions) == 1
        enriched = captured_instructions[0]

        # Must contain the brain knowledge from prior heartbeats
        assert "VPN required for deploy" in enriched, \
            f"Brain knowledge not passed to create_agent:\n{enriched[:500]}"
        # Must contain the lesson from prior heartbeats
        assert "error_encountered" in enriched, \
            f"Lesson not passed to create_agent:\n{enriched[:500]}"
        # Must contain the persona instructions
        assert "AGENT MEMORY" in enriched
        assert "SECOND BRAIN" in enriched


# ═══════════════════════════════════════════════════════════════════════
# 5. Knowledge quality — does capture actually accumulate useful data?
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.learning_loop
class TestKnowledgeQuality:
    """Verify that the learning pipeline captures structured, queryable knowledge."""

    def test_captured_notes_are_categorized_and_tagged(self):
        """Notes captured via _store_capture retain their categories and tags."""
        brain = BrainState()
        raw = json.dumps({
            "topic": "CI pipeline optimization",
            "content": "Parallel test execution reduces CI time by 60%",
            "tags": ["ci", "performance", "testing"],
            "category": "projects",
        })
        note_id = _store_capture(brain, raw)

        assert note_id is not None
        note = brain.notes[note_id]
        assert note["category"] == "projects"
        assert set(note["tags"]) == {"ci", "performance", "testing"}
        assert note["summary"] == "CI pipeline optimization"

        # Verify it's queryable by category
        from second_brain import get_notes_by_category
        projects = get_notes_by_category(brain, "projects")
        assert len(projects) == 1
        assert projects[0]["id"] == note_id

    def test_connections_create_a_knowledge_graph(self):
        """Connected notes form a navigable graph structure."""
        brain = BrainState()
        id1 = add_note(brain, content="Docker compose for local dev",
                        summary="docker compose", tags=["docker"])
        id2 = add_note(brain, content="Kubernetes for production deployment",
                        summary="k8s deployment", tags=["k8s"])
        id3 = add_note(brain, content="Helm charts simplify K8s config",
                        summary="helm charts", tags=["k8s", "config"])

        connect_notes(brain, id1, id2, reason="Container orchestration spectrum")
        connect_notes(brain, id2, id3, reason="Both K8s ecosystem tools")

        # id2 should be a hub connecting id1 and id3
        assert id1 in brain.notes[id2]["connections"]
        assert id3 in brain.notes[id2]["connections"]
        assert len(brain.notes[id2]["connections"]) == 2

        # The graph is navigable: from id1, you can reach id3 through id2
        id1_neighbors = brain.notes[id1]["connections"]
        assert id2 in id1_neighbors
        # Follow the link
        id2_neighbors = brain.notes[id2]["connections"]
        assert id3 in id2_neighbors

    def test_brain_summary_reflects_accumulated_knowledge(self):
        """Brain summary provides a meaningful snapshot for prompt injection."""
        brain = BrainState()
        add_note(brain, content="API rate limits require backoff",
                 summary="rate limiting", tags=["api"], category="resources")
        add_note(brain, content="Database connection pooling reduces latency",
                 summary="connection pooling", tags=["database"], category="areas")
        add_note(brain, content="React memo prevents unnecessary re-renders",
                 summary="React.memo", tags=["react", "performance"], category="projects")

        connect_notes(brain, "n0001", "n0002", reason="Both improve system reliability")

        summary = build_brain_summary(brain)

        # Summary must contain actionable information
        assert "Total notes: 3" in summary
        assert "Total connections: 1" in summary
        assert "rate limiting" in summary
        assert "connection pooling" in summary
        assert "React.memo" in summary
        # Category breakdown
        assert "resources=1" in summary
        assert "areas=1" in summary
        assert "projects=1" in summary

    def test_context_block_shows_trajectory(self):
        """Context block reveals the agent's trajectory — what it did and learned."""
        state = AgentState(
            execution_count=10,
            last_heartbeat="2026-04-07T18:00:00Z",
            memory={"focus": "infrastructure reliability"},
            execution_history=[
                {"timestamp": "2026-04-07T17:00:00Z", "step": "capture",
                 "prompt": "...", "response": "Discovered pod eviction patterns"},
                {"timestamp": "2026-04-07T17:30:00Z", "step": "review",
                 "prompt": "...", "response": "Knowledge gaps in monitoring"},
                {"timestamp": "2026-04-07T18:00:00Z", "step": "capture",
                 "prompt": "...", "response": "Prometheus alerting best practices"},
            ],
            lessons_learned=[
                {"type": "error_encountered", "step": "deploy",
                 "description": "Error signal during 'deploy': Timeout connecting to API gateway",
                 "timestamp": "2026-04-07T16:00:00Z"},
                {"type": "success_achieved", "step": "rollback",
                 "description": "Success signal during 'rollback': Rollback completed successfully",
                 "timestamp": "2026-04-07T16:30:00Z"},
            ],
        )

        context = build_context_block(state, max_recent=3)

        # Execution trajectory
        assert "Total executions: 10" in context
        assert "infrastructure reliability" in context  # stored memory

        # Recent activity visible
        assert "pod eviction" in context.lower() or "Discovered pod eviction" in context
        assert "Prometheus" in context

        # Lessons visible
        assert "error_encountered" in context
        assert "success_achieved" in context
        assert "Timeout" in context or "gateway" in context.lower()


# ═══════════════════════════════════════════════════════════════════════
# 6. Edge cases in the learning loop
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.learning_loop
class TestLearningEdgeCases:
    """Edge cases that could silently break the learning cycle."""

    def test_no_lessons_from_neutral_response(self):
        """A normal response without signal words produces zero lessons."""
        lessons = extract_lessons(
            "status_check", "Check status",
            "System is running normally. 3 services active. No issues."
        )
        assert len(lessons) == 0, \
            f"Neutral response should not produce lessons, got: {lessons}"

    def test_malformed_capture_still_preserves_knowledge(self):
        """If the agent returns non-JSON, the raw text is still stored."""
        brain = BrainState()
        raw = "I think the key insight here is that distributed locking is hard."
        note_id = _store_capture(brain, raw)

        assert note_id is not None
        assert "distributed locking" in brain.notes[note_id]["content"]

    def test_empty_brain_still_produces_valid_summary(self):
        """An empty brain produces a summary that won't break prompt composition."""
        brain = BrainState()
        summary = build_brain_summary(brain)

        assert "Total notes: 0" in summary
        assert "SECOND BRAIN" in summary
        # Should be safe to concatenate into a prompt
        enriched = f"You are an agent.\n\n{summary}"
        assert isinstance(enriched, str)
        assert len(enriched) > 20

    def test_empty_state_still_produces_valid_context(self):
        """A fresh state produces a context block that won't break enrichment."""
        state = AgentState()
        context = build_context_block(state)

        assert "AGENT MEMORY" in context
        assert "never" in context
        enriched = f"Instructions here.\n\n{context}\n\nBrain here."
        assert isinstance(enriched, str)

    def test_truncated_history_keeps_recent_lessons(self, state_file):
        """After history truncation, the most recent lessons survive."""
        state = AgentState(execution_count=200)

        # Add 200 lessons
        for i in range(200):
            state.lessons_learned.append({
                "type": "success_achieved",
                "step": f"step_{i}",
                "description": f"Lesson {i}",
                "timestamp": f"2026-04-07T{i % 24:02d}:00:00Z",
            })

        # Save with max_history=50 (truncates)
        loop = asyncio.get_event_loop()
        loop.run_until_complete(save_state(state, state_file, max_history=50))
        loaded = loop.run_until_complete(load_state(state_file))

        # Recent lessons should survive
        assert len(loaded.lessons_learned) == 50
        assert loaded.lessons_learned[-1]["description"] == "Lesson 199"

        # Context block should show the recent ones
        context = build_context_block(loaded, max_recent=3)
        assert "Lesson 199" in context
        assert "Lesson 198" in context

    def test_brain_with_many_notes_summarizes_recent(self):
        """Brain summary with 50 notes only shows the most recent max_notes."""
        brain = BrainState()
        for i in range(50):
            add_note(brain, content=f"Insight number {i}",
                     summary=f"insight_{i}", tags=[f"tag_{i}"])

        summary = build_brain_summary(brain, max_notes=5)
        assert "Total notes: 50" in summary
        # Should show only the 5 most recent
        assert "insight_49" in summary
        assert "insight_45" in summary
        # Should NOT show old notes
        assert "insight_0" not in summary
        assert "insight_10" not in summary
