from __future__ import annotations

import argparse
import asyncio
import json
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from config import AppConfig
from learning import build_context_block
from scheduler import run_scheduler
from scheduler_control import SchedulerControlState
from second_brain import BrainState
from state import load_state, save_state


async def run_smoke(output_path: Path, state_path: Path) -> dict[str, object]:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    persona = SimpleNamespace(
        name="smoke",
        instructions="Smoke-test persona for deterministic heartbeat checks.",
        tools=[],
        mcp_servers={},
        workflow="second_brain",
        decision_policy={},
        heartbeat_schema="",
        brain_schema="",
    )
    control_state = SchedulerControlState()
    shared_brain = BrainState()
    instructions_seen: list[str] = []

    async def _load_state(path: str):
        return await load_state(path)

    async def _save_state(state, path: str, max_history: int = 100):
        await save_state(state, path, max_history=max_history)

    async def _load_brain(_path: str):
        return shared_brain

    async def _save_brain(_brain, _path: str):
        return None

    async def _load_tasks(_path: str):
        return []

    async def _save_tasks(_tasks, _path: str):
        return None

    async def _load_outbox(_path: str):
        return []

    async def _save_outbox(_outbox, _path: str):
        return None

    async def _load_projects(_path: str):
        return []

    async def _load_scheduler_control(_path: str):
        return control_state

    async def _save_scheduler_control(_control, _path: str):
        return None

    def _create_agent(_config, instructions, tools=None, mcp_servers=None):
        _ = (tools, mcp_servers)
        instructions_seen.append(instructions)
        return object()

    async def _run_heartbeat(_agent, state, _brain, _config, _persona):
        state.lessons_learned.append(
            {
                "type": "success_achieved",
                "step": "smoke",
                "description": f"Smoke lesson from heartbeat #{state.execution_count}",
                "confidence": 80,
                "timestamp": state.last_heartbeat or "unknown",
            }
        )

    mock_decision = MagicMock()
    mock_decision.chosen.action.value = "run_heartbeat"
    mock_decision.chosen.score = 100.0
    mock_decision.chosen.rationale = "smoke"
    mock_decision.supplementary_actions = []

    config = AppConfig(
        provider="foundry",
        model="smoke-model",
        heartbeat_interval_sec=1,
        state_file=str(state_path),
        max_history=100,
        watch_path=str(state_path.parent),
    )

    with ExitStack() as stack:
        stack.enter_context(patch("scheduler.load_persona", return_value=persona))
        stack.enter_context(patch("scheduler.load_state", new=_load_state))
        stack.enter_context(patch("scheduler.save_state", new=_save_state))
        stack.enter_context(patch("scheduler.load_brain", new=_load_brain))
        stack.enter_context(patch("scheduler.save_brain", new=_save_brain))
        stack.enter_context(patch("scheduler.load_tasks", new=_load_tasks))
        stack.enter_context(patch("scheduler.save_tasks", new=_save_tasks))
        stack.enter_context(patch("scheduler.load_outbox", new=_load_outbox))
        stack.enter_context(patch("scheduler.save_outbox", new=_save_outbox))
        stack.enter_context(patch("scheduler.load_projects", new=_load_projects))
        stack.enter_context(
            patch("scheduler.load_scheduler_control", new=_load_scheduler_control)
        )
        stack.enter_context(
            patch("scheduler.save_scheduler_control", new=_save_scheduler_control)
        )
        stack.enter_context(patch("scheduler.detect_and_save_project", return_value=None))
        stack.enter_context(patch("scheduler.create_agent", side_effect=_create_agent))
        stack.enter_context(patch("scheduler.run_heartbeat", new=_run_heartbeat))
        stack.enter_context(patch("scheduler.acquire_scheduler_lock", return_value=True))
        stack.enter_context(patch("scheduler.release_scheduler_lock"))
        stack.enter_context(
            patch("scheduler._wait_for_event_or_timeout", new=AsyncMock(return_value=None))
        )
        mock_watcher = stack.enter_context(patch("event_watcher.EventWatcher"))
        stack.enter_context(patch("event_watcher.drain_pending_events", return_value=0))
        stack.enter_context(
            patch("decision_engine.build_decision_context", return_value=MagicMock())
        )
        stack.enter_context(
            patch("decision_engine.evaluate_heartbeat", return_value=mock_decision)
        )
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
        stack.enter_context(
            patch("decision_engine.record_decision", return_value="smoke-note-id")
        )
        stack.enter_context(patch("decision_engine.record_decision_outcome"))
        stack.enter_context(patch("decision_engine.update_consecutive_empty"))

        mock_watcher.return_value.start = MagicMock()
        mock_watcher.return_value.stop = MagicMock()
        await run_scheduler(config, max_iterations=3)

    final_state = await load_state(str(state_path))
    context_block = build_context_block(final_state)

    checks = {
        "three_cycles_completed": final_state.execution_count == 3,
        "state_persisted": state_path.exists(),
        "lessons_present_in_context": "Smoke lesson from heartbeat #1" in context_block,
        "context_injected_across_cycles": any(
            "Smoke lesson from heartbeat #1" in item for item in instructions_seen[1:]
        ),
    }
    artifact = {
        "cycles_target": 3,
        "execution_count": final_state.execution_count,
        "checks": checks,
        "lessons_count": len(final_state.lessons_learned),
        "state_file": str(state_path),
    }
    output_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="poc_smoke.py",
        description="Run deterministic 3-cycle smoke validation for Core POC.",
    )
    parser.add_argument(
        "--output",
        default="artifacts/poc-smoke-evidence.json",
        help="Path to write smoke evidence JSON.",
    )
    parser.add_argument(
        "--state-file",
        default="artifacts/poc-smoke-state.json",
        help="State file path used during smoke execution.",
    )
    args = parser.parse_args()

    artifact = asyncio.run(run_smoke(Path(args.output), Path(args.state_file)))
    passed = all(bool(v) for v in artifact["checks"].values())
    print(f"Smoke artifact written: {args.output}")
    print("Smoke status:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
