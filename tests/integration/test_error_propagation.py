"""Category F: Error propagation across module boundaries.

Tests that partial failures preserve state, brain save failures don't
lose state, and retry handles real file contention.
"""
from __future__ import annotations

import asyncio
import json
import os
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from config import AppConfig
from second_brain import BrainState, add_note, save_brain, load_brain
from state import AgentState, save_state, load_state
from workflow import _run_step, _run_second_brain_heartbeat

# Import retry from scheduler — must mock agent_setup's external deps first
# to avoid missing dependencies
import sys
from unittest.mock import MagicMock as _MagicMock
for _mod in ("agent_framework_github_copilot", "copilot", "agent_framework",
             "agent_framework._agents"):
    if _mod not in sys.modules:
        sys.modules[_mod] = _MagicMock()
from scheduler import retry


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


def _make_agent_failing_on_step(fail_step: str, responses: dict[str, str]) -> AsyncMock:
    """Create an agent that raises on a specific step."""
    agent = AsyncMock()
    call_count = {"n": 0}

    async def side_effect(prompt):
        call_count["n"] += 1
        # Determine which step we're on by looking at known prompt patterns
        for key, response_text in responses.items():
            if key in prompt.lower():
                if key == fail_step:
                    raise RuntimeError(f"Agent crashed during {fail_step}")
                resp = MagicMock()
                resp.text = response_text
                return resp
        # Default response
        resp = MagicMock()
        resp.text = "default response"
        return resp

    agent.run.side_effect = side_effect
    return agent


class TestPartialFailurePreservesState:
    """F1: Multi-step partial failure preserves execution history."""

    @pytest.mark.asyncio
    async def test_agent_crash_mid_heartbeat_preserves_history(self):
        """If agent crashes on capture step, status_check history is preserved."""
        state = AgentState()
        brain = BrainState()
        config = _make_config()

        call_count = {"n": 0}

        async def agent_run(prompt):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # status_check succeeds
                resp = MagicMock()
                resp.text = "System is running normally"
                return resp
            else:
                # capture step crashes
                raise RuntimeError("LLM connection lost")

        agent = AsyncMock()
        agent.run.side_effect = agent_run

        # run_second_brain_heartbeat catches exceptions internally
        from persona_loader import Persona
        persona = Persona(name="test", description="test", instructions="test",
                          heartbeat_task="Capture something")
        await _run_second_brain_heartbeat(agent, state, brain, config, persona)

        # Status check should have been recorded before the crash
        assert len(state.execution_history) >= 1
        assert state.execution_history[0]["step"] == "status_check"


class TestBrainSaveFailurePreservesState:
    """F2: State is persisted even when brain save fails."""

    @pytest.mark.asyncio
    async def test_state_saved_when_brain_save_raises(self, tmp_path):
        state_file = str(tmp_path / "state.json")
        state = AgentState(execution_count=5)
        state.execution_history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "step": "test",
            "prompt": "test",
            "response": "test",
        })

        # Save state succeeds
        await save_state(state, state_file)

        # Verify state is persisted
        loaded = await load_state(state_file)
        assert loaded.execution_count == 5
        assert len(loaded.execution_history) == 1

        # Even if brain save would fail, state is already safe
        brain = BrainState()
        add_note(brain, content="Important note")

        with patch("second_brain._write_brain", side_effect=IOError("Disk full")):
            with pytest.raises(IOError):
                await save_brain(brain, state_file)

        # State should still be loadable
        still_loaded = await load_state(state_file)
        assert still_loaded.execution_count == 5


class TestRetryWithFileContention:
    """F3: Retry decorator handles transient file errors."""

    @pytest.mark.asyncio
    async def test_retry_recovers_from_transient_os_error(self, tmp_path):
        state_file = str(tmp_path / "state.json")
        state = AgentState(execution_count=42)

        call_count = {"n": 0}
        original_write = None

        # Import the actual write function
        from state import _write_state
        original_write = _write_state

        def flaky_write(s, p, m):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise OSError("Temporary file lock")
            return original_write(s, p, m)

        with patch("state._write_state", side_effect=flaky_write):
            await retry(max_attempts=3, delay=0.01)(save_state)(state, state_file)

        # Should have retried and succeeded
        assert call_count["n"] == 2
        loaded = await load_state(state_file)
        assert loaded.execution_count == 42

    @pytest.mark.asyncio
    async def test_retry_exhaustion_raises_runtime_error(self, tmp_path):
        state_file = str(tmp_path / "state.json")
        state = AgentState(execution_count=1)

        with patch("state._write_state", side_effect=OSError("Permanent failure")):
            with pytest.raises(RuntimeError, match="All 3 attempts failed"):
                await retry(max_attempts=3, delay=0.01)(save_state)(state, state_file)
