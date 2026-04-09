"""Category A: Persona tools <-> workflow pipeline integration tests.

Verifies that persona-loaded tool functions are callable, that the
security layer (option parsing, path validation) is active through the
full loaded-tool chain, and that tools are correctly isolated per persona.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from persona_loader import load_persona, list_personas
from config import AppConfig
from state import AgentState
from second_brain import BrainState
from workflow import _run_step
from execution_log import set_db_path, recent_entries, total_count, clear_log


@pytest.fixture(autouse=True)
def _use_temp_db(tmp_path):
    """Point the execution log at a per-test temp DB."""
    set_db_path(str(tmp_path / "execution_log.db"))
    yield
    clear_log()


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

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


def _make_agent(response_text: str = "OK") -> AsyncMock:
    agent = AsyncMock()
    resp = MagicMock()
    resp.text = response_text
    agent.run.return_value = resp
    return agent


# ──────────────────────────────────────────────────────────────────────
# A1: Loaded persona tools are callable by workflow
# ──────────────────────────────────────────────────────────────────────

class TestPersonaToolsLoadedAndCallable:
    """Verify persona_loader surfaces callable tool functions."""

    def test_python_developer_tools_include_run_shell(self):
        persona = load_persona("python_developer")
        tool_names = [t.__name__ for t in persona.tools]
        assert "run_shell_command" in tool_names
        assert "list_files" in tool_names
        assert "read_source_file" in tool_names
        assert "write_source_file" in tool_names

    def test_devops_tools_include_run_shell(self):
        persona = load_persona("devops_engineer")
        tool_names = [t.__name__ for t in persona.tools]
        assert "run_shell_command" in tool_names
        assert "list_files" in tool_names
        assert "read_source_file" in tool_names

    def test_react_developer_tools_include_run_shell(self):
        persona = load_persona("react_developer")
        tool_names = [t.__name__ for t in persona.tools]
        assert "run_shell_command" in tool_names
        assert "list_files" in tool_names

    def test_loaded_tool_is_actually_callable(self):
        persona = load_persona("python_developer")
        run_shell = next(t for t in persona.tools if t.__name__ == "run_shell_command")
        # Should be callable — invoke with a safe command (mocked subprocess)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = "hello"
            mock_run.return_value.stderr = ""
            result = run_shell("echo hello")
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_workflow_step_can_invoke_tool_and_record_history(self):
        """Simulate a workflow step whose response references a tool result,
        then verify execution log records it."""
        agent = _make_agent("Tool result: files listed successfully")
        state = AgentState()
        result = await _run_step(agent, "tool_invocation", "List project files", state)
        assert "files listed" in result
        log_entries = recent_entries(100)
        assert len(log_entries) == 1
        assert log_entries[0]["step"] == "tool_invocation"


# ──────────────────────────────────────────────────────────────────────
# A2: Security layer active through persona->tool chain
# ──────────────────────────────────────────────────────────────────────

class TestSecurityThroughPersonaChain:
    """Verify validate_and_execute_command is not bypassed by persona loading."""

    @pytest.mark.parametrize("persona_name", ["devops_engineer", "python_developer", "react_developer"])
    def test_blocked_command_through_loaded_persona(self, persona_name):
        persona = load_persona(persona_name)
        run_shell = next(t for t in persona.tools if t.__name__ == "run_shell_command")
        result = run_shell("rm -rf /")
        assert "Blocked" in result or "not allowed" in result.lower()

    @pytest.mark.parametrize("persona_name", ["devops_engineer", "python_developer", "react_developer"])
    def test_allowed_command_through_loaded_persona(self, persona_name):
        persona = load_persona(persona_name)
        run_shell = next(t for t in persona.tools if t.__name__ == "run_shell_command")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = "output"
            mock_run.return_value.stderr = ""
            result = run_shell("echo hello")
        assert "Blocked" not in result


# ──────────────────────────────────────────────────────────────────────
# A3: Combined short options through run_shell_command
# ──────────────────────────────────────────────────────────────────────

class TestCombinedShortOptions:
    """Recently-fixed: -la should not be split incorrectly."""

    @pytest.mark.parametrize("persona_name", ["devops_engineer", "python_developer", "react_developer"])
    def test_ls_la_through_loaded_tool(self, persona_name):
        persona = load_persona(persona_name)
        run_shell = next(t for t in persona.tools if t.__name__ == "run_shell_command")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = "drwxr-xr-x 2 user user 4096 Jan 1 00:00 ."
            mock_run.return_value.stderr = ""
            result = run_shell("ls -la")
        assert "Blocked" not in result


# ──────────────────────────────────────────────────────────────────────
# A4: --option=value path security
# ──────────────────────────────────────────────────────────────────────

class TestOptionValuePathSecurity:
    """Recently-fixed: --junitxml=../../etc/passwd should be blocked."""

    @pytest.mark.parametrize("persona_name", ["devops_engineer", "python_developer", "react_developer"])
    def test_option_value_path_traversal_blocked(self, persona_name):
        persona = load_persona(persona_name)
        run_shell = next(t for t in persona.tools if t.__name__ == "run_shell_command")
        result = run_shell("pytest --junitxml=../../etc/passwd")
        assert "Blocked" in result or "not allowed" in result.lower()

    @pytest.mark.parametrize("persona_name", ["devops_engineer", "python_developer", "react_developer"])
    def test_option_value_absolute_path_blocked(self, persona_name):
        persona = load_persona(persona_name)
        run_shell = next(t for t in persona.tools if t.__name__ == "run_shell_command")
        result = run_shell("pytest --junitxml=/etc/results.xml")
        assert "Blocked" in result or "not allowed" in result.lower()

    @pytest.mark.parametrize("persona_name", ["devops_engineer", "python_developer", "react_developer"])
    def test_option_value_safe_path_allowed(self, persona_name):
        persona = load_persona(persona_name)
        run_shell = next(t for t in persona.tools if t.__name__ == "run_shell_command")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = "ok"
            mock_run.return_value.stderr = ""
            result = run_shell("pytest --junitxml=results.xml")
        assert "Blocked" not in result


# ──────────────────────────────────────────────────────────────────────
# A5: +format option acceptance (date)
# ──────────────────────────────────────────────────────────────────────

class TestPlusFormatOption:
    """Recently-fixed: date +%Y-%m-%d should be accepted."""

    @pytest.mark.parametrize("persona_name", ["devops_engineer", "python_developer", "react_developer"])
    def test_date_plus_format_through_loaded_tool(self, persona_name):
        persona = load_persona(persona_name)
        run_shell = next(t for t in persona.tools if t.__name__ == "run_shell_command")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = "2026-04-08"
            mock_run.return_value.stderr = ""
            result = run_shell("date +%Y-%m-%d")
        assert "Blocked" not in result
        assert "2026" in result


# ──────────────────────────────────────────────────────────────────────
# A6: Path security in option arguments
# ──────────────────────────────────────────────────────────────────────

class TestOptionArgPathSecurity:
    """Recently-fixed: absolute paths in option args should be blocked."""

    @pytest.mark.parametrize("persona_name", ["devops_engineer", "python_developer", "react_developer"])
    def test_option_arg_absolute_path_blocked(self, persona_name):
        persona = load_persona(persona_name)
        run_shell = next(t for t in persona.tools if t.__name__ == "run_shell_command")
        result = run_shell("head -n 10 /etc/passwd")
        assert "Blocked" in result or "not allowed" in result.lower()

    @pytest.mark.parametrize("persona_name", ["devops_engineer", "python_developer", "react_developer"])
    def test_option_arg_traversal_blocked(self, persona_name):
        persona = load_persona(persona_name)
        run_shell = next(t for t in persona.tools if t.__name__ == "run_shell_command")
        result = run_shell("tail -n 10 ../../../etc/shadow")
        assert "Blocked" in result or "not allowed" in result.lower()


# ──────────────────────────────────────────────────────────────────────
# A7: Cross-persona tool isolation
# ──────────────────────────────────────────────────────────────────────

class TestCrossPersonaIsolation:
    """Each persona should only expose its own tools."""

    def test_devops_does_not_have_write_source_file(self):
        persona = load_persona("devops_engineer")
        tool_names = [t.__name__ for t in persona.tools]
        assert "write_source_file" not in tool_names

    def test_python_developer_has_write_source_file(self):
        persona = load_persona("python_developer")
        tool_names = [t.__name__ for t in persona.tools]
        assert "write_source_file" in tool_names

    def test_different_personas_have_independent_modules(self):
        devops = load_persona("devops_engineer")
        pydev = load_persona("python_developer")
        devops_shell = next(t for t in devops.tools if t.__name__ == "run_shell_command")
        pydev_shell = next(t for t in pydev.tools if t.__name__ == "run_shell_command")
        # They should be separate function objects from separate modules
        assert devops_shell.__module__ != pydev_shell.__module__
