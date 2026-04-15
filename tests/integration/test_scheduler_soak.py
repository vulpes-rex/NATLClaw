"""Reliability soak tests for scheduler stability under transient failures."""
from __future__ import annotations

import asyncio
import json
import sys
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Mock provider modules before importing scheduler.
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
from state import AgentState, load_state


def _directives() -> dict:
    return {
        "action": "run_heartbeat",
        "active_task": None,
        "workflow_override": None,
        "skip_agent": False,
        "extra_context": "",
        "outbox_messages": [],
    }


@pytest.mark.asyncio
async def test_scheduler_soak_survives_transient_errors(tmp_path):
    """Scheduler should continue through intermittent runtime errors/timeouts."""
    state_file = str(tmp_path / "state.json")
    config = AppConfig(
        provider="openai",
        model="test-model",
        openai_api_key="test-key",
        heartbeat_interval_sec=1,
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

    watcher = MagicMock()
    watcher.start = MagicMock()
    watcher.stop = MagicMock()

    heartbeat_calls = 0

    async def flaky_heartbeat(*_args, **_kwargs):
        nonlocal heartbeat_calls
        heartbeat_calls += 1
        # Inject recurring transient failure patterns.
        if heartbeat_calls % 7 == 0:
            raise asyncio.TimeoutError("simulated timeout")
        if heartbeat_calls % 5 == 0:
            raise RuntimeError("simulated network connection refused")

    class _FakeMetricsStore:
        def __init__(self, *_args, **_kwargs):
            self.records = 0

        def record_heartbeat(self, **_kwargs):
            self.records += 1

        def close(self):
            return None

    q = asyncio.PriorityQueue()

    with ExitStack() as stack:
        stack.enter_context(patch("scheduler.load_persona", return_value=persona))
        stack.enter_context(
            patch("scheduler.load_state", new_callable=AsyncMock, return_value=AgentState())
        )
        stack.enter_context(
            patch("scheduler.load_brain", new_callable=AsyncMock, return_value=BrainState())
        )
        stack.enter_context(patch("scheduler.save_state", new_callable=AsyncMock))
        stack.enter_context(patch("scheduler.save_brain", new_callable=AsyncMock))
        stack.enter_context(
            patch("scheduler.load_tasks", new_callable=AsyncMock, return_value=[])
        )
        stack.enter_context(patch("scheduler.save_tasks", new_callable=AsyncMock))
        stack.enter_context(
            patch("scheduler.load_outbox", new_callable=AsyncMock, return_value=[])
        )
        stack.enter_context(patch("scheduler.save_outbox", new_callable=AsyncMock))
        stack.enter_context(
            patch("scheduler.load_projects", new_callable=AsyncMock, return_value=[])
        )
        stack.enter_context(patch("scheduler.detect_and_save_project", return_value=None))
        stack.enter_context(patch("scheduler.create_agent", return_value=MagicMock()))
        stack.enter_context(patch("scheduler.run_heartbeat", side_effect=flaky_heartbeat))
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
        stack.enter_context(patch("decision_engine.apply_decision", return_value=_directives()))
        stack.enter_context(patch("decision_engine.record_decision", return_value="note"))
        stack.enter_context(patch("decision_engine.record_decision_outcome"))
        stack.enter_context(patch("decision_engine.update_consecutive_empty"))
        stack.enter_context(patch("event_watcher.EventWatcher", return_value=watcher))
        stack.enter_context(patch("event_watcher.drain_pending_events", return_value=0))
        stack.enter_context(patch("scheduler.MetricsStore", _FakeMetricsStore))
        await run_scheduler(config, max_iterations=25, event_queue=q)

    # Scheduler should have progressed through many cycles despite injected failures.
    assert heartbeat_calls >= 20
    watcher.stop.assert_called_once()


@pytest.mark.asyncio
async def test_scheduler_soak_restart_cycles_keep_lock_and_state_consistent(tmp_path):
    """Repeated scheduler restarts should not leave stale lock or corrupt persistence."""
    state_file = str(tmp_path / "state.json")
    lock_file = tmp_path / "scheduler.lock"
    tasks_file = tmp_path / "tasks.json"
    outbox_file = tmp_path / "outbox.json"

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

    heartbeat_calls = 0

    async def flaky_heartbeat(*_args, **_kwargs):
        nonlocal heartbeat_calls
        heartbeat_calls += 1
        # Lock must be held whenever heartbeat work is running.
        assert lock_file.exists()
        # Inject intermittent transient failures across cycles.
        if heartbeat_calls % 6 == 0:
            raise asyncio.TimeoutError("simulated timeout")
        if heartbeat_calls % 4 == 0:
            raise RuntimeError("simulated io error")

    class _FakeMetricsStore:
        def __init__(self, *_args, **_kwargs):
            self.records = 0

        def record_heartbeat(self, **_kwargs):
            self.records += 1

        def close(self):
            return None

    for _ in range(3):
        watcher = MagicMock()
        watcher.start = MagicMock()
        watcher.stop = MagicMock()

        with ExitStack() as stack:
            stack.enter_context(patch("scheduler.load_persona", return_value=persona))
            stack.enter_context(
                patch("scheduler.load_brain", new_callable=AsyncMock, return_value=BrainState())
            )
            stack.enter_context(patch("scheduler.save_brain", new_callable=AsyncMock))
            stack.enter_context(
                patch("scheduler.load_projects", new_callable=AsyncMock, return_value=[])
            )
            stack.enter_context(patch("scheduler.detect_and_save_project", return_value=None))
            stack.enter_context(patch("scheduler.create_agent", return_value=MagicMock()))
            stack.enter_context(patch("scheduler.run_heartbeat", side_effect=flaky_heartbeat))
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
            stack.enter_context(patch("decision_engine.apply_decision", return_value=_directives()))
            stack.enter_context(patch("decision_engine.record_decision", return_value="note"))
            stack.enter_context(patch("decision_engine.record_decision_outcome"))
            stack.enter_context(patch("decision_engine.update_consecutive_empty"))
            stack.enter_context(patch("event_watcher.EventWatcher", return_value=watcher))
            stack.enter_context(patch("event_watcher.drain_pending_events", return_value=0))
            stack.enter_context(patch("scheduler.MetricsStore", _FakeMetricsStore))
            await run_scheduler(config, max_iterations=4, event_queue=asyncio.PriorityQueue())

        # After each stop, lock must be released and persisted artifacts readable.
        assert lock_file.exists() is False
        assert tasks_file.exists() is True
        assert outbox_file.exists() is True
        assert (tmp_path / "state.json").exists() is True
        assert isinstance(json.loads(tasks_file.read_text(encoding="utf-8")), list)
        assert isinstance(json.loads(outbox_file.read_text(encoding="utf-8")), list)
        watcher.stop.assert_called_once()

    final_state = await load_state(state_file)
    # 3 runs * 4 heartbeats each
    assert final_state.execution_count == 12
    assert heartbeat_calls >= 12


@pytest.mark.asyncio
async def test_scheduler_burst_events_are_spilled_across_heartbeats(tmp_path):
    """Burst event queues should be processed in bounded chunks across heartbeats."""
    state_file = str(tmp_path / "state.json")
    config = AppConfig(
        provider="openai",
        model="test-model",
        openai_api_key="test-key",
        heartbeat_interval_sec=1,
        max_events_per_heartbeat=3,
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

    watcher = MagicMock()
    watcher.start = MagicMock()
    watcher.stop = MagicMock()

    class _FakeMetricsStore:
        def __init__(self, *_args, **_kwargs):
            self.records = 0

        def record_heartbeat(self, **_kwargs):
            self.records += 1

        def close(self):
            return None

    decision_event_batch_sizes: list[int] = []

    def _capture_decision_context(state, brain, tasks, outbox, pending_events, persona):
        decision_event_batch_sizes.append(len(pending_events))
        return MagicMock()

    q = asyncio.PriorityQueue()
    for i in range(9):
        q.put_nowait((3, f"burst_event_{i}", {"i": i}))

    with ExitStack() as stack:
        stack.enter_context(patch("scheduler.load_persona", return_value=persona))
        stack.enter_context(patch("scheduler.load_state", new_callable=AsyncMock, return_value=AgentState()))
        stack.enter_context(patch("scheduler.load_brain", new_callable=AsyncMock, return_value=BrainState()))
        stack.enter_context(patch("scheduler.save_state", new_callable=AsyncMock))
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
                return_value=None,
            )
        )
        stack.enter_context(patch("daily_digest.is_first_run_today", return_value=False))
        stack.enter_context(
            patch("decision_engine.build_decision_context", side_effect=_capture_decision_context)
        )
        stack.enter_context(patch("decision_engine.evaluate_heartbeat", return_value=decision))
        stack.enter_context(patch("decision_engine.apply_decision", return_value=_directives()))
        stack.enter_context(patch("decision_engine.record_decision", return_value="note"))
        stack.enter_context(patch("decision_engine.record_decision_outcome"))
        stack.enter_context(patch("decision_engine.update_consecutive_empty"))
        stack.enter_context(patch("event_watcher.EventWatcher", return_value=watcher))
        stack.enter_context(patch("event_watcher.drain_pending_events", return_value=0))
        stack.enter_context(patch("scheduler.MetricsStore", _FakeMetricsStore))
        await run_scheduler(config, max_iterations=3, event_queue=q)

    assert decision_event_batch_sizes == [3, 3, 3]


@pytest.mark.asyncio
async def test_scheduler_soak_observer_persona_bounded_under_event_storm(tmp_path):
    """Observer persona should stay bounded under bursty event storms."""
    state_file = str(tmp_path / "state.json")
    config = AppConfig(
        provider="openai",
        model="test-model",
        openai_api_key="test-key",
        heartbeat_interval_sec=1,
        max_events_per_heartbeat=4,
        state_file=state_file,
        max_history=50,
        persona="workspace_observer",
        watch_path=str(tmp_path),
    )

    persona = MagicMock(name="persona")
    persona.name = "workspace_observer"
    persona.instructions = "observer"
    persona.tools = []
    persona.mcp_servers = {}
    persona.workflow = "steps"
    persona.heartbeat_schema = ""
    persona.brain_schema = ""
    persona.decision_policy = {}

    decision = MagicMock()
    decision.chosen.action.value = "run_heartbeat"
    decision.chosen.score = 55.0
    decision.chosen.rationale = "observer work"
    decision.supplementary_actions = []

    watcher = MagicMock()
    watcher.start = MagicMock()
    watcher.stop = MagicMock()

    class _FakeMetricsStore:
        def __init__(self, *_args, **_kwargs):
            self.records = 0

        def record_heartbeat(self, **_kwargs):
            self.records += 1

        def close(self):
            return None

    decision_event_batch_sizes: list[int] = []

    def _capture_decision_context(state, brain, tasks, outbox, pending_events, persona):
        decision_event_batch_sizes.append(len(pending_events))
        return MagicMock()

    q = asyncio.PriorityQueue()
    for i in range(20):
        q.put_nowait((2, "file_modified", {"path": f"src/f{i}.py"}))

    with ExitStack() as stack:
        stack.enter_context(patch("scheduler.load_persona", return_value=persona))
        stack.enter_context(patch("scheduler.load_state", new_callable=AsyncMock, return_value=AgentState()))
        stack.enter_context(patch("scheduler.load_brain", new_callable=AsyncMock, return_value=BrainState()))
        stack.enter_context(patch("scheduler.save_state", new_callable=AsyncMock))
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
                return_value=None,
            )
        )
        stack.enter_context(patch("daily_digest.is_first_run_today", return_value=False))
        stack.enter_context(
            patch("decision_engine.build_decision_context", side_effect=_capture_decision_context)
        )
        stack.enter_context(patch("decision_engine.evaluate_heartbeat", return_value=decision))
        stack.enter_context(patch("decision_engine.apply_decision", return_value=_directives()))
        stack.enter_context(patch("decision_engine.record_decision", return_value="note"))
        stack.enter_context(patch("decision_engine.record_decision_outcome"))
        stack.enter_context(patch("decision_engine.update_consecutive_empty"))
        stack.enter_context(patch("event_watcher.EventWatcher", return_value=watcher))
        stack.enter_context(patch("event_watcher.drain_pending_events", return_value=0))
        stack.enter_context(patch("scheduler.MetricsStore", _FakeMetricsStore))
        await run_scheduler(config, max_iterations=3, event_queue=q)

    assert decision_event_batch_sizes == [4, 4, 4]
