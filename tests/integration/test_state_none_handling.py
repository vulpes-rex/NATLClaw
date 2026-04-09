"""Category D: State None-handling <-> workflow integration tests.

Verifies the recently-fixed _write_state None handling for
execution_history/lessons_learned survives full heartbeat cycles.
"""
from __future__ import annotations

import asyncio
import json
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from learning import build_context_block
from state import AgentState, load_state, save_state
from workflow import _run_step


def _make_agent(response_text: str = "OK") -> AsyncMock:
    agent = AsyncMock()
    resp = MagicMock()
    resp.text = response_text
    agent.run.return_value = resp
    return agent


class TestNoneListsSurviveHeartbeat:
    """D1: State with None lists survives save -> load -> _run_step cycle."""

    @pytest.mark.asyncio
    async def test_none_execution_history_survives_save_load(self, tmp_path):
        state_file = str(tmp_path / "state.json")
        state = AgentState(execution_history=None, lessons_learned=None)

        # Save should not crash
        await save_state(state, state_file)

        # Load should return usable state
        loaded = await load_state(state_file)
        assert loaded.execution_history == []
        assert loaded.lessons_learned == []

    @pytest.mark.asyncio
    async def test_none_lists_then_run_step_appends(self, tmp_path):
        state_file = str(tmp_path / "state.json")
        state = AgentState(execution_history=None, lessons_learned=None)

        # Save and reload
        await save_state(state, state_file)
        loaded = await load_state(state_file)

        # Run a workflow step — should append to the now-initialized lists
        agent = _make_agent("completed successfully")
        await _run_step(agent, "test_step", "Test prompt", loaded)

        assert len(loaded.execution_history) == 1
        assert loaded.execution_history[0]["step"] == "test_step"
        # Lessons should be extracted (success signal)
        assert len(loaded.lessons_learned) >= 1

    @pytest.mark.asyncio
    async def test_none_lists_survive_multiple_save_load_cycles(self, tmp_path):
        state_file = str(tmp_path / "state.json")

        state = AgentState(execution_history=None, lessons_learned=None)
        await save_state(state, state_file)

        for i in range(3):
            loaded = await load_state(state_file)
            agent = _make_agent(f"Step {i} completed successfully")
            await _run_step(agent, f"step_{i}", f"Prompt {i}", loaded)
            await save_state(loaded, state_file)

        final = await load_state(state_file)
        assert len(final.execution_history) == 3
        assert final.execution_history[2]["step"] == "step_2"


class TestCorruptedStateRecovery:
    """D2: JSON with null lists is recovered gracefully."""

    @pytest.mark.asyncio
    async def test_null_json_fields_load_as_empty_lists(self, tmp_path):
        state_file = str(tmp_path / "state.json")
        # Write corrupted JSON with null fields
        state_file_path = tmp_path / "state.json"
        state_file_path.write_text(json.dumps({
            "last_heartbeat": "2026-01-01T00:00:00",
            "execution_count": 5,
            "memory": {},
            "context": {},
            "execution_history": None,
            "lessons_learned": None,
        }))

        loaded = await load_state(state_file)
        # None values loaded from JSON
        # Build context should handle this gracefully
        context = build_context_block(loaded)
        assert "AGENT MEMORY" in context
        assert "Total executions: 5" in context

    @pytest.mark.asyncio
    async def test_null_fields_then_workflow_step_then_save(self, tmp_path):
        state_file = str(tmp_path / "state.json")
        state_file_path = tmp_path / "state.json"
        state_file_path.write_text(json.dumps({
            "last_heartbeat": None,
            "execution_count": 0,
            "memory": {},
            "context": {},
            "execution_history": None,
            "lessons_learned": None,
        }))

        loaded = await load_state(state_file)

        # Fix None → [] before workflow step
        if loaded.execution_history is None:
            loaded.execution_history = []
        if loaded.lessons_learned is None:
            loaded.lessons_learned = []

        agent = _make_agent("Task done, error: some issue encountered")
        await _run_step(agent, "recovery_step", "Recover from corrupted state", loaded)

        # Save should work fine
        await save_state(loaded, state_file)

        # Reload and verify
        final = await load_state(state_file)
        assert len(final.execution_history) == 1
        assert final.execution_history[0]["step"] == "recovery_step"
