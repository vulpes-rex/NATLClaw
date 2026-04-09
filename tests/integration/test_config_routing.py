"""Category G: Config persona choice → scheduler → workflow routing.

Verifies that the persona configured via AppConfig correctly routes
through persona_loader, the scheduler, and into the right workflow mode.
"""
from __future__ import annotations

import asyncio
import json
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from config import AppConfig
from persona_loader import Persona, load_persona
from second_brain import BrainState, build_brain_summary
from state import AgentState
from workflow import run_heartbeat, _run_step
from execution_log import set_db_path, recent_entries, total_count, clear_log


@pytest.fixture(autouse=True)
def _use_temp_db(tmp_path):
    """Point the execution log at a per-test temp DB."""
    set_db_path(str(tmp_path / "execution_log.db"))
    yield
    clear_log()


def _make_config(**overrides) -> AppConfig:
    return AppConfig(**{
        "provider": "copilot",
        "model": "test-model",
        "state_file": "data/agent_state.json",
        "heartbeat_interval_sec": 120,
        "max_history": 100,
        "agent_name": "NATLClaw",
        "persona": "default",
        **overrides,
    })


def _make_agent(responses: list[str] | None = None) -> AsyncMock:
    """Create a mock agent that returns responses in order."""
    agent = AsyncMock()
    if responses is None:
        responses = ["OK"]
    call_idx = {"n": 0}

    async def side_effect(prompt):
        resp = MagicMock()
        idx = min(call_idx["n"], len(responses) - 1)
        resp.text = responses[idx]
        call_idx["n"] += 1
        return resp

    agent.run.side_effect = side_effect
    return agent


# ──────────────────────────────────────────────────────────────────────
# G1: Config persona routes to correct workflow in scheduler
# ──────────────────────────────────────────────────────────────────────

class TestConfigPersonaRoutesToWorkflow:
    """Verify that persona.workflow drives which heartbeat mode is executed."""

    @pytest.mark.asyncio
    async def test_project_manager_routes_to_freeform(self):
        """project_manager has workflow=freeform — run_heartbeat should
        execute _run_freeform_heartbeat (which includes a 'task' step)."""
        persona = load_persona("project_manager")
        assert persona.workflow == "freeform"

        state = AgentState()
        brain = BrainState()
        config = _make_config(persona="project_manager")

        # Freeform has 4 steps: status_check, task, capture, review
        agent = _make_agent([
            "All tasks are on track",                   # status_check
            "Updated the sprint board",                 # task
            '{"topic":"sprint","content":"Sprint update reviewed","tags":["pm"],"category":"resources"}',  # capture
            "Reviewed: sprint is healthy",              # review
        ])

        await run_heartbeat(agent, state, brain, config, persona)

        # Verify freeform steps were executed (execution log is in SQLite now)
        step_names = [h["step"] for h in recent_entries(100)]
        assert "status_check" in step_names
        assert "task" in step_names
        assert "capture" in step_names
        assert "review" in step_names

    @pytest.mark.asyncio
    async def test_default_persona_routes_to_second_brain(self):
        """default persona has workflow=second_brain."""
        persona = load_persona("default")
        assert persona.workflow == "second_brain"

        state = AgentState()
        brain = BrainState()
        config = _make_config(persona="default")

        # Pre-populate brain with an existing note so the connect step triggers
        # (connect requires len(recent) >= 2; capture adds 1, so we need 1 already)
        from second_brain import add_note
        add_note(brain, content="Existing seed note about agent architecture", tags=["seed"])

        # second_brain has 4 steps: status_check, capture, connect, review
        agent = _make_agent([
            "System running normally",
            '{"topic":"AI agents","content":"Discovered new insight about AI agent autonomy","tags":["ai"],"category":"resources"}',
            '{"from":"n0001","to":"n0002","reason":"architecture builds on autonomy"}',
            "Review complete",
        ])

        await run_heartbeat(agent, state, brain, config, persona)

        step_names = [h["step"] for h in recent_entries(100)]
        assert "status_check" in step_names
        assert "capture" in step_names
        assert "connect" in step_names
        assert "review" in step_names

    @pytest.mark.asyncio
    async def test_react_developer_routes_to_steps(self):
        """react_developer has workflow=steps (non-stepwise, all at once)."""
        persona = load_persona("react_developer")
        assert persona.workflow == "steps"
        assert persona.stepwise is False
        assert persona.steps is not None and len(persona.steps) >= 2

        state = AgentState()
        brain = BrainState()
        config = _make_config(persona="react_developer")

        # Steps with storeToBrain=True cause an extra _distil_to_brain call
        # which adds a <step_name>_capture entry to execution_history
        store_count = sum(1 for s in persona.steps if s.get("storeToBrain"))
        expected_entries = len(persona.steps) + store_count
        responses = [f"Step {i} done" for i in range(expected_entries)]
        agent = _make_agent(responses)

        await run_heartbeat(agent, state, brain, config, persona)

        # All steps + capture entries for storeToBrain steps
        assert total_count() == expected_entries

    @pytest.mark.asyncio
    async def test_researcher_routes_to_second_brain(self):
        """researcher persona has workflow=second_brain."""
        persona = load_persona("researcher")
        assert persona.workflow == "second_brain"

    @pytest.mark.asyncio
    async def test_python_developer_routes_to_freeform(self):
        """python_developer has workflow=freeform."""
        persona = load_persona("python_developer")
        assert persona.workflow == "freeform"

    @pytest.mark.asyncio
    async def test_devops_engineer_routes_to_freeform(self):
        """devops_engineer has workflow=freeform."""
        persona = load_persona("devops_engineer")
        assert persona.workflow == "freeform"


# ──────────────────────────────────────────────────────────────────────
# G2: Stepwise state persistence across scheduler iterations
# ──────────────────────────────────────────────────────────────────────

class TestStepwiseStatePersistence:
    """Verify _run_one_step advances the step index across heartbeats."""

    @pytest.mark.asyncio
    async def test_stepwise_index_increments_across_heartbeats(self):
        """react_site_builder has stepwise=True. Each heartbeat should
        advance the step index by 1."""
        persona = load_persona("react_site_builder")
        assert persona.stepwise is True
        total_steps = len(persona.steps)
        assert total_steps >= 2

        state = AgentState()
        brain = BrainState()
        config = _make_config(persona="react_site_builder")

        # First heartbeat — should execute step 0 ("plan" has storeToBrain=True
        # so it produces an extra plan_capture entry)
        step_0_has_store = persona.steps[0].get("storeToBrain", False)
        first_hb_entries = 2 if step_0_has_store else 1
        agent = _make_agent(["Plan output for step 0", '{"topic":"plan","content":"insight","tags":[],"category":"resources"}'])
        await run_heartbeat(agent, state, brain, config, persona)

        idx_key = f"steps_{persona.name}_idx"
        assert state.context.get(idx_key) == 1
        assert total_count() == first_hb_entries

        # Second heartbeat — should execute step 1 ("scaffold" has no storeToBrain)
        step_1_has_store = persona.steps[1].get("storeToBrain", False)
        second_hb_entries = first_hb_entries + (2 if step_1_has_store else 1)
        agent = _make_agent(["Scaffold output for step 1"])
        await run_heartbeat(agent, state, brain, config, persona)

        assert state.context.get(idx_key) == 2
        assert total_count() == second_hb_entries

    @pytest.mark.asyncio
    async def test_stepwise_resets_after_all_steps_complete(self):
        """After all steps are done, index should reset to 0."""
        persona = load_persona("react_site_builder")
        total_steps = len(persona.steps)

        state = AgentState()
        brain = BrainState()
        config = _make_config(persona="react_site_builder")

        idx_key = f"steps_{persona.name}_idx"

        # Simulate running all steps one at a time
        for i in range(total_steps):
            agent = _make_agent([f"Step {i} completed"])
            await run_heartbeat(agent, state, brain, config, persona)

        # All steps complete — index should be at total_steps
        assert state.context.get(idx_key) == total_steps

        # Next heartbeat should detect completion and reset
        agent = _make_agent(["Should not run"])
        await run_heartbeat(agent, state, brain, config, persona)

        assert state.context.get(idx_key) == 0

    @pytest.mark.asyncio
    async def test_stepwise_prev_context_carries_forward(self):
        """Previous step output is stored and injected into next step's prompt."""
        persona = load_persona("react_site_builder")
        state = AgentState()
        brain = BrainState()
        config = _make_config(persona="react_site_builder")

        prev_key = f"steps_{persona.name}_prev"

        # First heartbeat
        agent = _make_agent(["Detailed plan: React portfolio with 6 components"])
        await run_heartbeat(agent, state, brain, config, persona)

        # Previous output should be stored in state.context
        assert prev_key in state.context
        assert "portfolio" in state.context[prev_key]

    @pytest.mark.asyncio
    async def test_stepwise_store_to_brain_flag(self):
        """Steps with storeToBrain=True should cause brain notes to be created."""
        persona = load_persona("react_site_builder")
        state = AgentState()
        brain = BrainState()
        config = _make_config(persona="react_site_builder")

        # The first step (plan) has storeToBrain=True in react_site_builder
        # Provide two responses: one for the step itself, one for the distil prompt
        agent = _make_agent([
            "Complete plan for portfolio site",
            '{"topic":"React Portfolio Plan","content":"Detailed component plan","tags":["react"],"category":"resources"}',
        ])
        await run_heartbeat(agent, state, brain, config, persona)

        # Brain should have a note from the distil step
        assert len(brain.notes) >= 1
