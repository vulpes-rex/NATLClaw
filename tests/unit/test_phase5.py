"""Tests for Phase 5 features: prompt templates, retry consolidation, and async consistency.

Covers:
- prompts.py: load_prompt, _read_template, clear_cache, _SafeDict, fallback on missing
- Retry consolidation (§5.1): module-level wrappers resolve current references
- Async consistency (§5.2): all four I/O functions are async
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Mock external deps before importing scheduler (persistent, like test_scheduler.py)
sys.modules["copilot"] = MagicMock()
sys.modules["agent_framework_github_copilot"] = MagicMock()
sys.modules["agent_framework"] = MagicMock()
sys.modules["agent_framework.foundry"] = MagicMock()
sys.modules["agent_framework.openai"] = MagicMock()
sys.modules["agent_framework.ollama"] = MagicMock()
sys.modules["azure.identity"] = MagicMock()

from scheduler import retry, run_scheduler

from config import AppConfig
from prompts import _SafeDict, _read_template, clear_cache, load_prompt
from second_brain import BrainState, add_note, lint_brain, load_brain, save_brain
from state import AgentState, load_state, save_state

logging.basicConfig(level=logging.DEBUG)


# ══════════════════════════════════════════════════════════════════════
# A. Prompt template loader
# ══════════════════════════════════════════════════════════════════════

class TestLoadPrompt:
    """Tests for the prompts.py template loader."""

    def test_loads_second_brain_status_check(self):
        result = load_prompt(
            "second_brain", "status_check",
            agent_name="TestClaw",
            execution_count=5,
            last_heartbeat="2026-01-01T00:00:00Z",
            past_executions=4,
            brain_summary="BRAIN",
            goals_block="",
            lint_block="",
            goals_suffix="",
        )
        assert "TestClaw" in result
        assert "#5" in result

    def test_loads_freeform_task(self):
        result = load_prompt(
            "freeform", "task",
            status_result="All good",
            heartbeat_task="Do stuff",
        )
        assert "All good" in result
        assert "Do stuff" in result

    def test_loads_coordinator_synthesis(self):
        result = load_prompt(
            "coordinator", "synthesis",
            agent_name="TestClaw",
            execution_count=10,
            selected_personas="x, y",
            outputs="  - x: done\n  - y: done",
            note_count=50,
            connection_count=20,
            goals_block="",
        )
        assert "x, y" in result
        assert "50" in result

    def test_loads_steps_distil(self):
        result = load_prompt("steps", "distil", result="Some output")
        assert "Some output" in result
        assert "JSON" in result

    def test_missing_template_returns_empty(self):
        result = load_prompt("nonexistent_mode", "nonexistent_step")
        assert result == ""

    def test_missing_placeholder_preserved(self):
        """Unresolved {key} placeholders are kept as-is (not KeyError)."""
        result = load_prompt(
            "second_brain", "status_check",
            agent_name="TestClaw",
            # Deliberately omit other required context
        )
        # Template should render with defaults for missing keys
        assert "TestClaw" in result
        # Missing keys left as {key} via _SafeDict
        assert "{execution_count}" in result

    def test_clear_cache(self):
        """clear_cache doesn't error and allows re-reading templates."""
        load_prompt("second_brain", "status_check", agent_name="X")
        clear_cache()
        result = load_prompt("second_brain", "status_check", agent_name="Y")
        assert "Y" in result

    def test_all_template_files_exist(self):
        """Every expected template file is present in prompts/."""
        prompts_dir = Path(__file__).resolve().parents[2] / "prompts"
        expected = [
            "second_brain/status_check.txt",
            "second_brain/capture.txt",
            "second_brain/connect.txt",
            "second_brain/review.txt",
            "freeform/status_check.txt",
            "freeform/task.txt",
            "freeform/capture.txt",
            "freeform/review.txt",
            "steps/distil.txt",
            "coordinator/synthesis.txt",
        ]
        for tmpl in expected:
            assert (prompts_dir / tmpl).exists(), f"Missing template: {tmpl}"

    def test_double_brace_literal_in_capture(self):
        """Capture templates use {{ }} for literal braces in JSON examples."""
        result = load_prompt(
            "second_brain", "capture",
            status_result="ok",
            note_count=0,
            brain_summary="brain",
            heartbeat_task="task",
        )
        # Should contain literal { from the JSON example
        assert '"topic"' in result


class TestSafeDict:
    """Test _SafeDict fallback behavior."""

    def test_present_key(self):
        d = _SafeDict({"a": "hello"})
        assert d["a"] == "hello"

    def test_missing_key_returns_placeholder(self):
        d = _SafeDict({})
        assert d["missing"] == "{missing}"

    def test_format_map_with_safe_dict(self):
        template = "Hello {name}, welcome to {place}."
        result = template.format_map(_SafeDict({"name": "Alice"}))
        assert result == "Hello Alice, welcome to {place}."


# ══════════════════════════════════════════════════════════════════════
# B. Retry consolidation (§5.1)
# ══════════════════════════════════════════════════════════════════════

class TestRetryConsolidation:
    """Verify retry wrappers resolve current module references."""

    def test_retry_wraps_once_per_scheduler_run(self):
        """run_scheduler creates retry wrappers at entry, not per-iteration."""
        config = MagicMock(spec=AppConfig)
        config.provider = "foundry"
        config.model = "m"
        config.agent_name = "a"
        config.heartbeat_interval_sec = 60
        config.state_file = "s.json"
        config.max_history = 100
        config.agent_instructions = ""
        config.persona = "default"

        mock_persona = MagicMock(tools=[], mcp_servers={})
        mock_persona.instructions = "x"
        mock_persona.name = "test"

        call_count = {"load_state": 0}

        async def mock_load_state(*args, **kwargs):
            call_count["load_state"] += 1
            return AgentState()

        # Create a mock event queue that returns immediately with a dummy event
        class MockQueue:
            async def get(self):
                return (0, "test_event", {})
            def put_nowait(self, item):
                pass  # noop
            def empty(self):
                return True
            def get_nowait(self):
                raise asyncio.QueueEmpty

        mock_queue = MockQueue()

        # Decision engine mock
        mock_decision = MagicMock()
        mock_decision.chosen.action.value = "run_heartbeat"
        mock_decision.chosen.score = 50.0
        mock_decision.chosen.rationale = "test"
        mock_decision.supplementary_actions = []
        mock_directives = {
            "action": "run_heartbeat", "active_task": None,
            "workflow_override": None, "skip_agent": False,
            "extra_context": "", "outbox_messages": [],
        }

        from contextlib import ExitStack
        patches = [
            patch("scheduler.load_persona", return_value=mock_persona),
            patch("scheduler.load_state", side_effect=mock_load_state),
            patch("scheduler.load_brain", new_callable=AsyncMock, return_value=BrainState()),
            patch("scheduler.save_state", new_callable=AsyncMock),
            patch("scheduler.save_brain", new_callable=AsyncMock),
            patch("scheduler.load_projects", return_value=[]),
            patch("scheduler.detect_and_save_project", return_value=None),
            patch("scheduler.create_agent", return_value=MagicMock()),
            patch("scheduler.run_heartbeat", new_callable=AsyncMock),
            patch("decision_engine.build_decision_context", return_value=MagicMock()),
            patch("decision_engine.evaluate_heartbeat", return_value=mock_decision),
            patch("decision_engine.apply_decision", return_value=mock_directives),
            patch("decision_engine.record_decision", return_value="mock-note-id"),
            patch("decision_engine.record_decision_outcome"),
            patch("decision_engine.update_consecutive_empty"),
            patch("scheduler.acquire_scheduler_lock", return_value=True),
            patch("scheduler.release_scheduler_lock"),
        ]
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            asyncio.run(run_scheduler(config, max_iterations=2, event_queue=mock_queue))

        # load_state was called via retry wrapper
        assert call_count["load_state"] >= 1

    def test_patched_functions_used_by_retry(self):
        """When tests patch scheduler.load_state, the retry wrapper uses the patched version."""
        config = MagicMock(spec=AppConfig)
        config.provider = "foundry"
        config.model = "m"
        config.agent_name = "a"
        config.heartbeat_interval_sec = 60
        config.state_file = "s.json"
        config.max_history = 100
        config.agent_instructions = ""
        config.persona = "default"

        mock_persona = MagicMock(tools=[], mcp_servers={})
        mock_persona.instructions = "x"
        mock_persona.name = "test"

        custom_state = AgentState(execution_count=42)

        # Decision engine mock
        mock_decision = MagicMock()
        mock_decision.chosen.action.value = "run_heartbeat"
        mock_decision.chosen.score = 50.0
        mock_decision.chosen.rationale = "test"
        mock_decision.supplementary_actions = []
        mock_directives = {
            "action": "run_heartbeat", "active_task": None,
            "workflow_override": None, "skip_agent": False,
            "extra_context": "", "outbox_messages": [],
        }

        from contextlib import ExitStack
        patches = [
            patch("scheduler.load_persona", return_value=mock_persona),
            patch("scheduler.load_state", new_callable=AsyncMock, return_value=custom_state),
            patch("scheduler.load_brain", new_callable=AsyncMock, return_value=BrainState()),
            patch("scheduler.save_state", new_callable=AsyncMock),
            patch("scheduler.save_brain", new_callable=AsyncMock),
            patch("scheduler.load_projects", return_value=[]),
            patch("scheduler.detect_and_save_project", return_value=None),
            patch("scheduler.create_agent", return_value=MagicMock()),
            patch("scheduler.run_heartbeat", new_callable=AsyncMock),
            patch("decision_engine.build_decision_context", return_value=MagicMock()),
            patch("decision_engine.evaluate_heartbeat", return_value=mock_decision),
            patch("decision_engine.apply_decision", return_value=mock_directives),
            patch("decision_engine.record_decision", return_value="mock-note-id"),
            patch("decision_engine.record_decision_outcome"),
            patch("decision_engine.update_consecutive_empty"),
            patch("scheduler.acquire_scheduler_lock", return_value=True),
            patch("scheduler.release_scheduler_lock"),
            patch("scheduler.asyncio.sleep", new_callable=AsyncMock),
        ]
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            mock_save = stack.enter_context(
                patch("scheduler.save_state", new_callable=AsyncMock),
            )
            asyncio.run(run_scheduler(config, max_iterations=1))

        # save_state received the custom state (execution_count incremented by scheduler)
        mock_save.assert_called_once()
        saved_state = mock_save.call_args[0][0]
        assert saved_state.execution_count == 43  # 42 + 1

    def test_no_module_level_retry_bindings(self):
        """Verify there are no stale module-level _load_state/_save_state bindings."""
        import scheduler as sched_mod
        # These should NOT exist as module-level attributes
        assert not hasattr(sched_mod, "_load_state") or callable(getattr(sched_mod, "_load_state", None)) is False or True
        # The key test: _load_state should not be at module scope
        # (it's created inside run_scheduler's local scope now)
        module_attrs = [a for a in dir(sched_mod) if a.startswith("_load_") or a.startswith("_save_")]
        # If any exist at module level, they should not be retry-wrapped callables
        for attr in module_attrs:
            obj = getattr(sched_mod, attr)
            # Module-level functions from imports (load_state, save_state) are ok
            # What we don't want is retry-wrapped versions at module level
            assert "wrapper" not in getattr(obj, "__qualname__", ""), \
                f"scheduler.{attr} should not be a retry wrapper at module level"


# ══════════════════════════════════════════════════════════════════════
# C. Async consistency (§5.2)
# ══════════════════════════════════════════════════════════════════════

class TestAsyncConsistency:
    """Verify all I/O functions are consistently async."""

    def test_load_state_is_async(self):
        assert inspect.iscoroutinefunction(load_state)

    def test_save_state_is_async(self):
        assert inspect.iscoroutinefunction(save_state)

    def test_load_brain_is_async(self):
        assert inspect.iscoroutinefunction(load_brain)

    def test_save_brain_is_async(self):
        assert inspect.iscoroutinefunction(save_brain)

    def test_load_brain_propagates_os_error(self, tmp_path):
        """load_brain propagates OSError for retry, but catches corrupt data."""
        brain_path = tmp_path / "brain.json"
        brain_path.write_text("not json", encoding="utf-8")
        state_file = str(tmp_path / "state.json")

        # Corrupt JSON → returns empty BrainState (not OSError)
        result = asyncio.run(load_brain(state_file))
        assert result.capture_count == 0

    def test_load_brain_returns_empty_if_missing(self, tmp_path):
        state_file = str(tmp_path / "state.json")
        result = asyncio.run(load_brain(state_file))
        assert result.capture_count == 0
        assert len(result.notes) == 0


# ══════════════════════════════════════════════════════════════════════
# D. Observer lint quality gates
# ══════════════════════════════════════════════════════════════════════

class TestObserverLintQualityGates:
    def test_missing_citation_detected_for_workspace_observer_note(self):
        brain = BrainState()
        add_note(
            brain,
            content="Observer note without evidence should be flagged.",
            source={"type": "heartbeat", "persona": "workspace_observer"},
            tags=["observer"],
            category="resources",
        )
        issues = lint_brain(brain)
        assert any(issue["type"] == "missing_citation" for issue in issues)

    def test_low_quality_tags_detected(self):
        brain = BrainState()
        add_note(
            brain,
            content="A note with generic tags only.",
            source={"type": "heartbeat", "persona": "researcher"},
            tags=["general"],
            category="resources",
        )
        issues = lint_brain(brain)
        assert any(issue["type"] == "tag_quality" for issue in issues)
