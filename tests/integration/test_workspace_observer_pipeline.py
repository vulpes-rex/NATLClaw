from __future__ import annotations

import asyncio
import json
import sys
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.modules.setdefault("copilot", MagicMock())
sys.modules.setdefault("agent_framework_github_copilot", MagicMock())
sys.modules.setdefault("agent_framework", MagicMock())
sys.modules.setdefault("agent_framework.foundry", MagicMock())
sys.modules.setdefault("agent_framework.openai", MagicMock())
sys.modules.setdefault("agent_framework.ollama", MagicMock())
sys.modules.setdefault("azure.identity", MagicMock())

from config import AppConfig
from decision_engine import ActionCandidate, ActionType, DecisionContext, HeartbeatDecision
from messaging import load_outbox
from scheduler import run_scheduler
from second_brain import load_brain
from workflow import _store_capture


@pytest.mark.asyncio
async def test_workspace_observer_pipeline_event_to_evidence_and_alert(tmp_path):
    """Simulate observer pipeline: events -> scheduler wake -> note + alert."""
    state_file = str(tmp_path / "state.json")
    config = AppConfig(
        provider="openai",
        model="test-model",
        openai_api_key="test-key",
        heartbeat_interval_sec=1,
        state_file=state_file,
        max_history=50,
        persona="workspace_observer",
        watch_path=str(tmp_path),
    )

    persona = MagicMock(name="workspace_observer")
    persona.name = "workspace_observer"
    persona.instructions = "observe"
    persona.tools = []
    persona.mcp_servers = {}
    persona.workflow = "steps"
    persona.heartbeat_schema = ""
    persona.brain_schema = ""
    persona.decision_policy = {}

    decision = HeartbeatDecision(
        chosen=ActionCandidate(
            action=ActionType.RUN_HEARTBEAT,
            score=50.0,
            confidence=0.9,
            rationale="run observer heartbeat",
        ),
        supplementary_actions=[],
    )

    bug_events = [
        (1, "git_commit", {"message": "fix bug in scheduler", "files": ["scheduler.py"]}),
        (2, "file_modified", {"path": "scheduler.py", "reason": "bugfix"}),
    ]
    decision_ctx = DecisionContext(
        events=bug_events,
        heartbeat_number=1,
        persona_name="workspace_observer",
        workflow_mode="steps",
        last_heartbeat_iso=(datetime.now(timezone.utc) - timedelta(hours=8)).isoformat(),
    )

    async def _observer_heartbeat(_agent, state, brain, _config, _persona):
        raw = json.dumps(
            {
                "topic": "Observer evidence note",
                "content": "Scheduler bugfix activity is concentrated in scheduler.py.",
                "tags": ["observer", "scheduler"],
                "category": "resources",
                "evidence": ["scheduler.py", "commit:abc1234"],
                "confidence": 84,
            }
        )
        _store_capture(
            brain,
            raw,
            persona_name="workspace_observer",
            heartbeat_number=state.execution_count,
            step="analyse",
        )

    q = asyncio.PriorityQueue()
    for item in bug_events:
        q.put_nowait(item)

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

    with ExitStack() as stack:
        stack.enter_context(patch("scheduler.load_persona", return_value=persona))
        stack.enter_context(patch("scheduler.load_projects", new_callable=AsyncMock, return_value=[]))
        stack.enter_context(patch("scheduler.detect_and_save_project", return_value=None))
        stack.enter_context(patch("scheduler.create_agent", return_value=MagicMock()))
        stack.enter_context(patch("scheduler.run_heartbeat", side_effect=_observer_heartbeat))
        stack.enter_context(patch("scheduler.decay_stale_notes_from_store", return_value=0))
        stack.enter_context(
            patch("scheduler._wait_for_event_or_timeout", new_callable=AsyncMock, return_value=None)
        )
        stack.enter_context(patch("daily_digest.is_first_run_today", return_value=False))
        stack.enter_context(patch("decision_engine.build_decision_context", return_value=decision_ctx))
        stack.enter_context(patch("decision_engine.evaluate_heartbeat", return_value=decision))
        stack.enter_context(patch("decision_engine.record_decision", return_value="note"))
        stack.enter_context(patch("decision_engine.record_decision_outcome"))
        stack.enter_context(patch("decision_engine.update_consecutive_empty"))
        stack.enter_context(patch("event_watcher.EventWatcher", return_value=watcher))
        stack.enter_context(patch("event_watcher.drain_pending_events", return_value=0))
        stack.enter_context(patch("scheduler.MetricsStore", _FakeMetricsStore))
        await run_scheduler(config, max_iterations=1, event_queue=q)

    brain = await load_brain(state_file)
    assert any(
        note.get("source", {}).get("persona") == "workspace_observer"
        and note.get("evidence")
        for note in brain.notes.values()
    )

    outbox = await load_outbox(state_file)
    assert any(
        msg.payload.get("escalation_type") in {"repeated_bug_work", "long_inactivity"}
        for msg in outbox
    )
