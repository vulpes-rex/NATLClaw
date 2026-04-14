"""Integration tests for scheduler lock recovery behavior."""
from __future__ import annotations

import asyncio
import sys
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Mock external provider dependencies before importing scheduler.
sys.modules.setdefault("copilot", MagicMock())
sys.modules.setdefault("agent_framework_github_copilot", MagicMock())
sys.modules.setdefault("agent_framework", MagicMock())
sys.modules.setdefault("agent_framework.foundry", MagicMock())
sys.modules.setdefault("agent_framework.openai", MagicMock())
sys.modules.setdefault("agent_framework.ollama", MagicMock())
sys.modules.setdefault("azure.identity", MagicMock())

from config import AppConfig
from scheduler import run_scheduler
from second_brain import BrainState
from state import AgentState


def _decision_directives() -> dict:
    return {
        "action": "run_heartbeat",
        "active_task": None,
        "workflow_override": None,
        "skip_agent": False,
        "extra_context": "",
        "outbox_messages": [],
    }


@pytest.mark.asyncio
async def test_scheduler_recovers_from_stale_lock_file(tmp_path):
    """A stale lock file should not block scheduler startup."""
    state_file = str(tmp_path / "state.json")
    lock_file = tmp_path / "scheduler.lock"
    lock_file.write_text("999999", encoding="utf-8")

    config = AppConfig(
        provider="openai",
        model="test-model",
        openai_api_key="test-key",
        heartbeat_interval_sec=10,
        state_file=state_file,
        max_history=50,
        persona="default",
        watch_path=str(tmp_path),
    )

    mock_persona = MagicMock(name="persona")
    mock_persona.name = "default"
    mock_persona.instructions = "test instructions"
    mock_persona.tools = []
    mock_persona.mcp_servers = {}
    mock_persona.workflow = "second_brain"
    mock_persona.heartbeat_schema = ""
    mock_persona.brain_schema = ""
    mock_persona.decision_policy = {}

    mock_decision = MagicMock()
    mock_decision.chosen.action.value = "run_heartbeat"
    mock_decision.chosen.score = 50.0
    mock_decision.chosen.rationale = "test"
    mock_decision.supplementary_actions = []

    watcher = MagicMock()
    watcher.start = MagicMock()
    watcher.stop = MagicMock()

    ran_heartbeat = False

    async def _noop_heartbeat(*args, **kwargs):
        nonlocal ran_heartbeat
        ran_heartbeat = True
        # Lock should exist while scheduler is actively running.
        assert lock_file.exists()

    q = asyncio.PriorityQueue()
    q.put_nowait((3, "test_tick", {}))

    patchers = [
        patch("scheduler._is_pid_alive", return_value=False),
        patch("scheduler.load_persona", return_value=mock_persona),
        patch("scheduler.load_state", new_callable=AsyncMock, return_value=AgentState()),
        patch("scheduler.load_brain", new_callable=AsyncMock, return_value=BrainState()),
        patch("scheduler.save_state", new_callable=AsyncMock),
        patch("scheduler.save_brain", new_callable=AsyncMock),
        patch("scheduler.load_tasks", new_callable=AsyncMock, return_value=[]),
        patch("scheduler.save_tasks", new_callable=AsyncMock),
        patch("scheduler.load_outbox", new_callable=AsyncMock, return_value=[]),
        patch("scheduler.save_outbox", new_callable=AsyncMock),
        patch("scheduler.load_projects", new_callable=AsyncMock, return_value=[]),
        patch("scheduler.detect_and_save_project", return_value=None),
        patch("scheduler.create_agent", return_value=MagicMock()),
        patch("scheduler.run_heartbeat", side_effect=_noop_heartbeat),
        patch("scheduler.decay_stale_notes_from_store", return_value=0),
        patch("daily_digest.is_first_run_today", return_value=False),
        patch("decision_engine.build_decision_context", return_value=MagicMock()),
        patch("decision_engine.evaluate_heartbeat", return_value=mock_decision),
        patch("decision_engine.apply_decision", return_value=_decision_directives()),
        patch("decision_engine.record_decision", return_value="mock-note-id"),
        patch("decision_engine.record_decision_outcome"),
        patch("decision_engine.update_consecutive_empty"),
        patch("event_watcher.EventWatcher", return_value=watcher),
        patch("event_watcher.drain_pending_events", return_value=0),
    ]

    with ExitStack() as stack:
        for p in patchers:
            stack.enter_context(p)
        await run_scheduler(config, max_iterations=1, event_queue=q)

    assert ran_heartbeat is True
    assert lock_file.exists() is False
