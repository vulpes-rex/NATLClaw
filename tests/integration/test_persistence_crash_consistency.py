"""Crash-consistency tests for atomic persistence paths."""
from __future__ import annotations

import asyncio
import os
import sys
from contextlib import ExitStack
from unittest.mock import patch
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.modules.setdefault("copilot", MagicMock())
sys.modules.setdefault("agent_framework_github_copilot", MagicMock())
sys.modules.setdefault("agent_framework", MagicMock())
sys.modules.setdefault("agent_framework.foundry", MagicMock())
sys.modules.setdefault("agent_framework.openai", MagicMock())
sys.modules.setdefault("agent_framework.ollama", MagicMock())
sys.modules.setdefault("azure.identity", MagicMock())

from config import AppConfig
from messaging import create_message, load_outbox, save_outbox
from second_brain import _brain_path
from second_brain import BrainState, add_note, load_brain, save_brain
from scheduler import run_scheduler
from state import AgentState, load_state, save_state
from tasks import create_task, load_tasks, save_tasks


@pytest.mark.asyncio
async def test_state_save_replace_failure_preserves_last_good_file(tmp_path):
    """If atomic replace fails, previously committed state remains valid."""
    state_file = str(tmp_path / "agent_state.json")
    baseline = AgentState(execution_count=1, memory={"checkpoint": "stable"})
    await save_state(baseline, state_file)

    updated = AgentState(execution_count=2, memory={"checkpoint": "new"})
    with patch("state.os.replace", side_effect=OSError("simulated replace crash")):
        with pytest.raises(OSError):
            await save_state(updated, state_file)

    loaded = await load_state(state_file)
    assert loaded.execution_count == 1
    assert loaded.memory.get("checkpoint") == "stable"


@pytest.mark.asyncio
async def test_tasks_save_replace_failure_preserves_last_good_file(tmp_path):
    """tasks.json should remain readable/unchanged if replace fails."""
    state_file = str(tmp_path / "agent_state.json")
    t1 = create_task("baseline task", priority="high")
    await save_tasks([t1], state_file)

    t2 = create_task("updated task", priority="urgent")
    with patch("tasks.os.replace", side_effect=OSError("simulated replace crash")):
        with pytest.raises(OSError):
            await save_tasks([t2], state_file)

    loaded = await load_tasks(state_file)
    assert len(loaded) == 1
    assert loaded[0].title == "baseline task"
    assert loaded[0].priority == "high"


@pytest.mark.asyncio
async def test_outbox_save_replace_failure_preserves_last_good_file(tmp_path):
    """outbox.json should remain readable/unchanged if replace fails."""
    state_file = str(tmp_path / "agent_state.json")
    m1 = create_message("status", "Baseline message")
    await save_outbox([m1], state_file)

    m2 = create_message("alert", "Updated message")
    with patch("messaging.os.replace", side_effect=OSError("simulated replace crash")):
        with pytest.raises(OSError):
            await save_outbox([m2], state_file)

    loaded = await load_outbox(state_file)
    assert len(loaded) == 1
    assert loaded[0].title == "Baseline message"


@pytest.mark.asyncio
async def test_brain_save_replace_failure_preserves_last_good_snapshot(tmp_path):
    """brain.json snapshot should remain readable if atomic replace fails."""
    state_file = str(tmp_path / "agent_state.json")
    baseline = BrainState()
    add_note(baseline, content="stable brain note", summary="stable")
    await save_brain(baseline, state_file)

    updated = BrainState()
    add_note(updated, content="new brain note", summary="new")
    with patch("second_brain.os.replace", side_effect=OSError("simulated replace crash")):
        with pytest.raises(OSError):
            await save_brain(updated, state_file)

    loaded = await load_brain(state_file)
    summaries = [n.get("summary", "") for n in loaded.notes.values()]
    # SQLite is the primary store; it should contain the latest committed note.
    assert "new" in summaries

    # Snapshot replace failure should keep the previous JSON snapshot intact.
    snapshot_path = _brain_path(state_file)
    with open(snapshot_path, "r", encoding="utf-8") as f:
        snapshot = f.read()
    assert "stable" in snapshot


@pytest.mark.asyncio
async def test_cross_file_reload_consistency_after_mixed_persistence_failures(tmp_path):
    """After simulated crashes, state/tasks/outbox should still reload consistently."""
    state_file = str(tmp_path / "agent_state.json")

    baseline_state = AgentState(execution_count=3, context={"session": "baseline"})
    baseline_task = create_task("baseline task", priority="medium")
    baseline_msg = create_message("status", "Baseline message")

    await save_state(baseline_state, state_file)
    await save_tasks([baseline_task], state_file)
    await save_outbox([baseline_msg], state_file)

    with patch("state.os.replace", side_effect=OSError("simulated state crash")):
        with pytest.raises(OSError):
            await save_state(AgentState(execution_count=4), state_file)
    with patch("tasks.os.replace", side_effect=OSError("simulated tasks crash")):
        with pytest.raises(OSError):
            await save_tasks([create_task("new task", priority="urgent")], state_file)
    with patch("messaging.os.replace", side_effect=OSError("simulated outbox crash")):
        with pytest.raises(OSError):
            await save_outbox([create_message("alert", "new message")], state_file)

    # Simulate restart/recovery by reloading all persisted artifacts.
    loaded_state = await load_state(state_file)
    loaded_tasks = await load_tasks(state_file)
    loaded_outbox = await load_outbox(state_file)

    assert loaded_state.execution_count == 3
    assert loaded_state.context.get("session") == "baseline"

    assert len(loaded_tasks) == 1
    assert loaded_tasks[0].id == baseline_task.id
    assert loaded_tasks[0].title == "baseline task"

    assert len(loaded_outbox) == 1
    assert loaded_outbox[0].id == baseline_msg.id
    assert loaded_outbox[0].title == "Baseline message"


@pytest.mark.asyncio
async def test_scheduler_restart_path_remains_consistent_when_state_persistence_fails(tmp_path):
    """Scheduler should keep crash-safe persisted state readable across restart paths."""
    state_file = str(tmp_path / "agent_state.json")
    await save_state(AgentState(execution_count=5, memory={"checkpoint": "stable"}), state_file)

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

    persona = MagicMock(name="persona")
    persona.name = "default"
    persona.instructions = "test"
    persona.tools = []
    persona.mcp_servers = {}
    persona.workflow = "second_brain"
    persona.heartbeat_schema = ""
    persona.brain_schema = ""
    persona.decision_policy = {}

    decision = MagicMock()
    decision.chosen.action.value = "run_heartbeat"
    decision.chosen.score = 50.0
    decision.chosen.rationale = "run work"
    decision.supplementary_actions = []

    with ExitStack() as stack:
        stack.enter_context(patch("scheduler.load_persona", return_value=persona))
        stack.enter_context(patch("scheduler.load_brain", new_callable=AsyncMock, return_value=BrainState()))
        stack.enter_context(patch("scheduler.save_state", new_callable=AsyncMock, side_effect=OSError("simulated save crash")))
        stack.enter_context(patch("scheduler.save_brain", new_callable=AsyncMock))
        stack.enter_context(patch("scheduler.load_tasks", new_callable=AsyncMock, return_value=[]))
        stack.enter_context(patch("scheduler.save_tasks", new_callable=AsyncMock))
        stack.enter_context(patch("scheduler.load_outbox", new_callable=AsyncMock, return_value=[]))
        stack.enter_context(patch("scheduler.save_outbox", new_callable=AsyncMock))
        stack.enter_context(patch("scheduler.load_projects", new_callable=AsyncMock, return_value=[]))
        stack.enter_context(patch("scheduler.detect_and_save_project", return_value=None))
        stack.enter_context(patch("scheduler.create_agent", return_value=MagicMock()))
        stack.enter_context(patch("scheduler.run_heartbeat", new_callable=AsyncMock))
        stack.enter_context(patch("scheduler.decay_stale_notes_from_store", return_value=0))
        stack.enter_context(
            patch(
                "scheduler._wait_for_event_or_timeout",
                new_callable=AsyncMock,
                return_value=(3, "test_tick", {}),
            )
        )
        stack.enter_context(patch("daily_digest.is_first_run_today", return_value=False))
        stack.enter_context(
            patch("decision_engine.build_decision_context", return_value=MagicMock())
        )
        stack.enter_context(patch("decision_engine.evaluate_heartbeat", return_value=decision))
        stack.enter_context(
            patch(
                "decision_engine.apply_decision",
                return_value={
                    "action": "run_heartbeat",
                    "active_task": None,
                    "workflow_override": None,
                    "skip_agent": False,
                    "extra_context": "",
                    "outbox_messages": [],
                },
            )
        )
        stack.enter_context(patch("decision_engine.record_decision", return_value="note"))
        stack.enter_context(patch("decision_engine.record_decision_outcome"))
        stack.enter_context(patch("decision_engine.update_consecutive_empty"))
        stack.enter_context(patch("event_watcher.EventWatcher", return_value=MagicMock(start=MagicMock(), stop=MagicMock())))
        stack.enter_context(patch("event_watcher.drain_pending_events", return_value=0))
        stack.enter_context(patch("scheduler.MetricsStore", return_value=MagicMock(record_heartbeat=MagicMock(), close=MagicMock())))

        await run_scheduler(config, max_iterations=1, event_queue=asyncio.PriorityQueue())

    # Restart/reload path should still see the previously committed, readable state.
    loaded = await load_state(state_file)
    assert loaded.execution_count == 5
    assert loaded.memory.get("checkpoint") == "stable"


@pytest.mark.asyncio
async def test_scheduler_restart_path_recovers_brain_after_snapshot_replace_failures(tmp_path):
    """Scheduler restart should recover brain state from SQLite after snapshot replace failures."""
    state_file = str(tmp_path / "agent_state.json")
    snapshot_path = _brain_path(state_file)

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

    persona = MagicMock(name="persona")
    persona.name = "default"
    persona.instructions = "test"
    persona.tools = []
    persona.mcp_servers = {}
    persona.workflow = "second_brain"
    persona.heartbeat_schema = ""
    persona.brain_schema = ""
    persona.decision_policy = {}

    decision = MagicMock()
    decision.chosen.action.value = "run_heartbeat"
    decision.chosen.score = 50.0
    decision.chosen.rationale = "run work"
    decision.supplementary_actions = []

    class _FakeMetricsStore:
        def __init__(self, *_args, **_kwargs):
            self.records = 0

        def record_heartbeat(self, **_kwargs):
            self.records += 1

        def close(self):
            return None

    heartbeat_counter = 0

    async def _append_note(_agent, _state, brain, _config, _persona):
        nonlocal heartbeat_counter
        heartbeat_counter += 1
        add_note(
            brain,
            content=f"scheduler note {heartbeat_counter}",
            summary=f"scheduler-{heartbeat_counter}",
        )

    def _patch_scheduler_for_restart_run():
        return [
            patch("scheduler.load_persona", return_value=persona),
            patch("scheduler.load_projects", new_callable=AsyncMock, return_value=[]),
            patch("scheduler.detect_and_save_project", return_value=None),
            patch("scheduler.create_agent", return_value=MagicMock()),
            patch("scheduler.run_heartbeat", side_effect=_append_note),
            patch("scheduler.decay_stale_notes_from_store", return_value=0),
            patch(
                "scheduler._wait_for_event_or_timeout",
                new_callable=AsyncMock,
                return_value=(3, "test_tick", {}),
            ),
            patch("daily_digest.is_first_run_today", return_value=False),
            patch("decision_engine.build_decision_context", return_value=MagicMock()),
            patch("decision_engine.evaluate_heartbeat", return_value=decision),
            patch(
                "decision_engine.apply_decision",
                return_value={
                    "action": "run_heartbeat",
                    "active_task": None,
                    "workflow_override": None,
                    "skip_agent": False,
                    "extra_context": "",
                    "outbox_messages": [],
                },
            ),
            patch("decision_engine.record_decision", return_value="note"),
            patch("decision_engine.record_decision_outcome"),
            patch("decision_engine.update_consecutive_empty"),
            patch(
                "event_watcher.EventWatcher",
                return_value=MagicMock(start=MagicMock(), stop=MagicMock()),
            ),
            patch("event_watcher.drain_pending_events", return_value=0),
            patch("scheduler.MetricsStore", _FakeMetricsStore),
        ]

    with ExitStack() as stack:
        # Simulate repeated snapshot replace failures during first scheduler run.
        stack.enter_context(
            patch("second_brain.os.replace", side_effect=OSError("simulated snapshot replace crash"))
        )
        for p in _patch_scheduler_for_restart_run():
            stack.enter_context(p)
        await run_scheduler(config, max_iterations=2, event_queue=asyncio.PriorityQueue())

    # Restart path should recover persisted brain data from SQLite.
    recovered_after_failures = await load_brain(state_file)
    recovered_summaries = [note.get("summary", "") for note in recovered_after_failures.notes.values()]
    assert "scheduler-1" in recovered_summaries
    assert "scheduler-2" in recovered_summaries
    assert not os.path.exists(snapshot_path)

    with ExitStack() as stack:
        for p in _patch_scheduler_for_restart_run():
            stack.enter_context(p)
        await run_scheduler(config, max_iterations=2, event_queue=asyncio.PriorityQueue())

    # Snapshot writes should reconcile once replace succeeds on restart.
    reloaded = await load_brain(state_file)
    final_summaries = [note.get("summary", "") for note in reloaded.notes.values()]
    assert "scheduler-4" in final_summaries
    assert os.path.exists(snapshot_path)

