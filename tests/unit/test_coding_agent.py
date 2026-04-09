"""Tests for coding agent CLI mode (cmd_code)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from io import StringIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Mock external dependencies before importing project modules
sys.modules.setdefault("copilot", MagicMock())
sys.modules.setdefault("agent_framework_github_copilot", MagicMock())
sys.modules.setdefault("agent_framework", MagicMock())
sys.modules.setdefault("agent_framework.foundry", MagicMock())
sys.modules.setdefault("agent_framework.openai", MagicMock())
sys.modules.setdefault("agent_framework.ollama", MagicMock())
sys.modules.setdefault("azure.identity", MagicMock())
sys.modules.setdefault("dotenv", MagicMock())

from cli import (
    build_parser,
    cmd_code,
    _DEFAULT_MAX_TURNS,
    _DONE_MARKER,
    _BLOCKED_MARKER,
    _describe_tools,
)
from config import AppConfig
from persona_loader import Persona
from second_brain import BrainState
from state import AgentState


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _config(**overrides) -> AppConfig:
    defaults = {
        "agent_name": "TestClaw",
        "state_file": "data/test.json",
        "provider": "foundry",
        "model": "test-model",
    }
    defaults.update(overrides)
    return AppConfig(**defaults)


def _persona(**overrides) -> Persona:
    defaults = {
        "name": "python_developer",
        "description": "Senior Python developer",
        "instructions": "You are a Python expert.",
    }
    defaults.update(overrides)
    return Persona(**defaults)


def _mock_agent(*responses: str) -> MagicMock:
    """Build a mock agent whose .run() yields responses sequentially."""
    agent = MagicMock()
    agent.run = AsyncMock(
        side_effect=[MagicMock(text=r) for r in responses],
    )
    return agent


def _args(**overrides) -> MagicMock:
    """Build a mock args namespace."""
    defaults = {
        "task": None,
        "persona": None,
        "cwd": None,
        "max_turns": None,
        "yes": True,  # auto-approve by default in tests
    }
    defaults.update(overrides)
    a = MagicMock()
    for k, v in defaults.items():
        setattr(a, k, v)
    return a


# ══════════════════════════════════════════════════════════════════════
# A. CLI parser — code subcommand
# ══════════════════════════════════════════════════════════════════════

class TestCodeParser:

    def test_code_subcommand_registered(self):
        parser = build_parser()
        ns = parser.parse_args(["code", "fix the tests"])
        assert ns.command == "code"
        assert ns.task == "fix the tests"

    def test_code_no_task_is_none(self):
        parser = build_parser()
        ns = parser.parse_args(["code"])
        assert ns.command == "code"
        assert ns.task is None

    def test_code_persona_flag(self):
        parser = build_parser()
        ns = parser.parse_args(["code", "--persona", "react_developer", "build UI"])
        assert ns.persona == "react_developer"
        assert ns.task == "build UI"

    def test_code_max_turns_flag(self):
        parser = build_parser()
        ns = parser.parse_args(["code", "--max-turns", "5", "quick task"])
        assert ns.max_turns == 5

    def test_code_yes_flag(self):
        parser = build_parser()
        ns = parser.parse_args(["code", "-y", "task"])
        assert ns.yes is True

    def test_code_cwd_flag(self):
        parser = build_parser()
        ns = parser.parse_args(["code", "--cwd", "/some/path", "task"])
        assert ns.cwd == "/some/path"


# ══════════════════════════════════════════════════════════════════════
# B. _describe_tools
# ══════════════════════════════════════════════════════════════════════

class TestDescribeTools:

    def test_describes_functions(self):
        def list_files(directory: str = ".") -> str:
            """List files and folders in a directory."""
            return ""

        def run_shell(command: str) -> str:
            """Run a shell command."""
            return ""

        desc = _describe_tools([list_files, run_shell])
        assert "list_files" in desc
        assert "run_shell" in desc
        assert "List files" in desc

    def test_empty_tools(self):
        desc = _describe_tools([])
        assert "no tools" in desc.lower()


# ══════════════════════════════════════════════════════════════════════
# C. Completion markers
# ══════════════════════════════════════════════════════════════════════

class TestMarkers:

    def test_done_marker_is_recognisable(self):
        assert "[TASK_COMPLETE]" == _DONE_MARKER

    def test_blocked_marker_is_recognisable(self):
        assert "[TASK_BLOCKED]" == _BLOCKED_MARKER

    def test_default_max_turns(self):
        assert _DEFAULT_MAX_TURNS == 20


# ══════════════════════════════════════════════════════════════════════
# D. One-shot coding agent (cmd_code with task)
# ══════════════════════════════════════════════════════════════════════

class TestOneShotCodeAgent:
    """Test cmd_code in one-shot mode (task provided on CLI)."""

    @patch("state.save_state", new_callable=AsyncMock)
    @patch("second_brain.save_brain", new_callable=AsyncMock)
    @patch("second_brain.load_brain", new_callable=AsyncMock)
    @patch("state.load_state", new_callable=AsyncMock)
    @patch("agent_setup.create_agent")
    @patch("cli.load_persona")
    def test_single_turn_done(
        self, mock_lp, mock_ca, mock_ls, mock_lb, mock_sb, mock_ss, capsys,
    ):
        """Agent completes task in one turn with [TASK_COMPLETE]."""
        mock_lp.return_value = _persona()
        mock_ls.return_value = AgentState()
        mock_lb.return_value = BrainState()

        agent = _mock_agent(
            "I fixed the bug. [TASK_COMPLETE]",
            # capture insight response
            json.dumps({"topic": "Fix", "content": "Fixed it", "tags": ["fix"], "category": "resources"}),
        )
        mock_ca.return_value = agent

        cmd_code(_args(task="fix the bug"), _config())

        output = capsys.readouterr().out
        assert "fix the bug" in output.lower() or "Task" in output
        assert "Task complete" in output
        # Agent was called at least once (the task) + capture
        assert agent.run.call_count >= 1

    @patch("state.save_state", new_callable=AsyncMock)
    @patch("second_brain.save_brain", new_callable=AsyncMock)
    @patch("second_brain.load_brain", new_callable=AsyncMock)
    @patch("state.load_state", new_callable=AsyncMock)
    @patch("agent_setup.create_agent")
    @patch("cli.load_persona")
    def test_multi_turn_then_done(
        self, mock_lp, mock_ca, mock_ls, mock_lb, mock_sb, mock_ss, capsys,
    ):
        """Agent takes 3 turns before completing."""
        mock_lp.return_value = _persona()
        mock_ls.return_value = AgentState()
        mock_lb.return_value = BrainState()

        agent = _mock_agent(
            "Reading files...",
            "Making changes...",
            "Tests pass. [TASK_COMPLETE]",
            # capture
            json.dumps({"topic": "T", "content": "C", "tags": [], "category": "resources"}),
        )
        mock_ca.return_value = agent

        cmd_code(_args(task="refactor the module", max_turns=10), _config())

        output = capsys.readouterr().out
        assert "Task complete" in output
        # 3 task turns + 1 capture = 4
        assert agent.run.call_count == 4

    @patch("state.save_state", new_callable=AsyncMock)
    @patch("second_brain.save_brain", new_callable=AsyncMock)
    @patch("second_brain.load_brain", new_callable=AsyncMock)
    @patch("state.load_state", new_callable=AsyncMock)
    @patch("agent_setup.create_agent")
    @patch("cli.load_persona")
    def test_max_turns_stops_agent(
        self, mock_lp, mock_ca, mock_ls, mock_lb, mock_sb, mock_ss, capsys,
    ):
        """Agent never says DONE — stops at max_turns."""
        mock_lp.return_value = _persona()
        mock_ls.return_value = AgentState()
        mock_lb.return_value = BrainState()

        # 5 responses, none with DONE marker
        agent = _mock_agent(
            "Still working...",
            "More work...",
            "Almost there...",
            # capture
            json.dumps({"topic": "T", "content": "C", "tags": [], "category": "resources"}),
        )
        mock_ca.return_value = agent

        cmd_code(_args(task="hard task", max_turns=3), _config())

        output = capsys.readouterr().out
        assert "max turns" in output.lower() or "Reached" in output

    @patch("state.save_state", new_callable=AsyncMock)
    @patch("second_brain.save_brain", new_callable=AsyncMock)
    @patch("second_brain.load_brain", new_callable=AsyncMock)
    @patch("state.load_state", new_callable=AsyncMock)
    @patch("agent_setup.create_agent")
    @patch("cli.load_persona")
    def test_blocked_stops_agent(
        self, mock_lp, mock_ca, mock_ls, mock_lb, mock_sb, mock_ss, capsys,
    ):
        """Agent says BLOCKED — loop stops."""
        mock_lp.return_value = _persona()
        mock_ls.return_value = AgentState()
        mock_lb.return_value = BrainState()

        agent = _mock_agent(
            "[TASK_BLOCKED] Missing credentials",
            json.dumps({"topic": "T", "content": "C", "tags": [], "category": "resources"}),
        )
        mock_ca.return_value = agent

        cmd_code(_args(task="deploy"), _config())

        output = capsys.readouterr().out
        assert "blocked" in output.lower()
        # Only 1 task turn + 1 capture
        assert agent.run.call_count == 2

    @patch("state.save_state", new_callable=AsyncMock)
    @patch("second_brain.save_brain", new_callable=AsyncMock)
    @patch("second_brain.load_brain", new_callable=AsyncMock)
    @patch("state.load_state", new_callable=AsyncMock)
    @patch("agent_setup.create_agent")
    @patch("cli.load_persona")
    def test_agent_error_handled(
        self, mock_lp, mock_ca, mock_ls, mock_lb, mock_sb, mock_ss, capsys,
    ):
        """Agent raises an exception — doesn't crash."""
        mock_lp.return_value = _persona()
        mock_ls.return_value = AgentState()
        mock_lb.return_value = BrainState()

        agent = MagicMock()
        agent.run = AsyncMock(side_effect=Exception("LLM unavailable"))
        mock_ca.return_value = agent

        # Should not raise
        cmd_code(_args(task="do something"), _config())

        output = capsys.readouterr().out
        assert "error" in output.lower()

    @patch("state.save_state", new_callable=AsyncMock)
    @patch("second_brain.save_brain", new_callable=AsyncMock)
    @patch("second_brain.load_brain", new_callable=AsyncMock)
    @patch("state.load_state", new_callable=AsyncMock)
    @patch("agent_setup.create_agent")
    @patch("cli.load_persona")
    def test_state_saved_after_task(
        self, mock_lp, mock_ca, mock_ls, mock_lb, mock_sb, mock_ss,
    ):
        """State and brain are saved after the coding task."""
        mock_lp.return_value = _persona()
        state = AgentState()
        mock_ls.return_value = state
        mock_lb.return_value = BrainState()

        agent = _mock_agent(
            "Done [TASK_COMPLETE]",
            json.dumps({"topic": "T", "content": "C", "tags": [], "category": "resources"}),
        )
        mock_ca.return_value = agent

        cmd_code(_args(task="task"), _config())

        mock_ss.assert_called_once()
        assert state.execution_count == 1  # incremented from 0

    @patch("state.save_state", new_callable=AsyncMock)
    @patch("second_brain.save_brain", new_callable=AsyncMock)
    @patch("second_brain.load_brain", new_callable=AsyncMock)
    @patch("state.load_state", new_callable=AsyncMock)
    @patch("agent_setup.create_agent")
    @patch("cli.load_persona")
    def test_custom_persona(
        self, mock_lp, mock_ca, mock_ls, mock_lb, mock_sb, mock_ss,
    ):
        """--persona flag selects a different persona."""
        mock_lp.return_value = _persona(name="react_developer")
        mock_ls.return_value = AgentState()
        mock_lb.return_value = BrainState()

        agent = _mock_agent(
            "Done [TASK_COMPLETE]",
            json.dumps({"topic": "T", "content": "C", "tags": [], "category": "resources"}),
        )
        mock_ca.return_value = agent

        cmd_code(_args(task="task", persona="react_developer"), _config())

        # load_persona was called with the CLI-specified persona
        mock_lp.assert_called_once_with("react_developer")


# ══════════════════════════════════════════════════════════════════════
# E. Brain capture after task
# ══════════════════════════════════════════════════════════════════════

class TestCodingAgentCapture:
    """After task completion the agent distils an insight into the brain."""

    @patch("state.save_state", new_callable=AsyncMock)
    @patch("second_brain.save_brain", new_callable=AsyncMock)
    @patch("second_brain.load_brain", new_callable=AsyncMock)
    @patch("state.load_state", new_callable=AsyncMock)
    @patch("agent_setup.create_agent")
    @patch("cli.load_persona")
    def test_insight_captured_to_brain(
        self, mock_lp, mock_ca, mock_ls, mock_lb, mock_sb, mock_ss,
    ):
        """Insight from agent response is saved as a brain note."""
        mock_lp.return_value = _persona()
        mock_ls.return_value = AgentState()
        brain = BrainState()
        mock_lb.return_value = brain

        insight = {
            "topic": "Test refactor",
            "content": "Always check for edge cases in date parsing.",
            "tags": ["testing", "python"],
            "category": "resources",
        }
        agent = _mock_agent(
            "Refactored tests. [TASK_COMPLETE]",
            json.dumps(insight),
        )
        mock_ca.return_value = agent

        cmd_code(_args(task="refactor tests"), _config())

        # Brain should have a new note
        assert len(brain.notes) == 1
        note = list(brain.notes.values())[0]
        assert "edge cases" in note["content"]
        assert note["source"]["type"] == "task_agent"
        assert note["source"]["persona"] == "python_developer"
        mock_sb.assert_called()  # save_brain called

    @patch("state.save_state", new_callable=AsyncMock)
    @patch("second_brain.save_brain", new_callable=AsyncMock)
    @patch("second_brain.load_brain", new_callable=AsyncMock)
    @patch("state.load_state", new_callable=AsyncMock)
    @patch("agent_setup.create_agent")
    @patch("cli.load_persona")
    def test_capture_handles_bad_json(
        self, mock_lp, mock_ca, mock_ls, mock_lb, mock_sb, mock_ss,
    ):
        """If the capture response is bad JSON, nothing crashes."""
        mock_lp.return_value = _persona()
        mock_ls.return_value = AgentState()
        brain = BrainState()
        mock_lb.return_value = brain

        agent = _mock_agent(
            "Done [TASK_COMPLETE]",
            "not valid json at all",
        )
        mock_ca.return_value = agent

        # Should not raise
        cmd_code(_args(task="task"), _config())

        # No note added since capture failed
        assert len(brain.notes) == 0

    @patch("state.save_state", new_callable=AsyncMock)
    @patch("second_brain.save_brain", new_callable=AsyncMock)
    @patch("second_brain.load_brain", new_callable=AsyncMock)
    @patch("state.load_state", new_callable=AsyncMock)
    @patch("agent_setup.create_agent")
    @patch("cli.load_persona")
    def test_capture_handles_markdown_wrapped_json(
        self, mock_lp, mock_ca, mock_ls, mock_lb, mock_sb, mock_ss,
    ):
        """Capture handles ```json ... ``` wrapped responses."""
        mock_lp.return_value = _persona()
        mock_ls.return_value = AgentState()
        brain = BrainState()
        mock_lb.return_value = brain

        insight = {"topic": "T", "content": "C", "tags": ["x"], "category": "resources"}
        agent = _mock_agent(
            "Done [TASK_COMPLETE]",
            f"```json\n{json.dumps(insight)}\n```",
        )
        mock_ca.return_value = agent

        cmd_code(_args(task="task"), _config())

        assert len(brain.notes) == 1


# ══════════════════════════════════════════════════════════════════════
# F. Prompt templates for coding agent
# ══════════════════════════════════════════════════════════════════════

class TestCodingAgentPromptTemplates:

    def test_system_template_exists(self):
        from prompts import load_prompt
        result = load_prompt(
            "coding_agent", "system",
            agent_name="Test",
            cwd=".",
            persona_name="dev",
            persona_description="developer",
            brain_summary="(none)",
            tools_description="(none)",
            done_marker="[TASK_COMPLETE]",
            blocked_marker="[TASK_BLOCKED]",
        )
        assert result
        assert "agent" in result.lower()

    def test_task_template_exists(self):
        from prompts import load_prompt
        result = load_prompt(
            "coding_agent", "task",
            task="fix the bug",
            done_marker="[TASK_COMPLETE]",
        )
        assert result
        assert "fix the bug" in result

    def test_followup_template_exists(self):
        from prompts import load_prompt
        result = load_prompt(
            "coding_agent", "followup",
            message="try again",
            done_marker="[TASK_COMPLETE]",
        )
        assert result
        assert "try again" in result

    def test_capture_template_exists(self):
        from prompts import load_prompt
        result = load_prompt("coding_agent", "capture", task_result="done")
        assert result
        assert "insight" in result.lower()


# ══════════════════════════════════════════════════════════════════════
# G. Interactive REPL (simulated via input mock)
# ══════════════════════════════════════════════════════════════════════

class TestCodingAgentREPL:
    """Test the interactive REPL mode (no task argument)."""

    @patch("state.save_state", new_callable=AsyncMock)
    @patch("second_brain.save_brain", new_callable=AsyncMock)
    @patch("second_brain.load_brain", new_callable=AsyncMock)
    @patch("state.load_state", new_callable=AsyncMock)
    @patch("agent_setup.create_agent")
    @patch("cli.load_persona")
    @patch("builtins.input")
    def test_repl_exit_command(
        self, mock_input, mock_lp, mock_ca, mock_ls, mock_lb, mock_sb, mock_ss,
        capsys,
    ):
        """Typing /exit quits the REPL."""
        mock_input.return_value = "/exit"
        mock_lp.return_value = _persona()
        mock_ls.return_value = AgentState()
        mock_lb.return_value = BrainState()
        mock_ca.return_value = _mock_agent()

        cmd_code(_args(task=None), _config())

        output = capsys.readouterr().out
        assert "Coding Agent" in output or "Goodbye" in output

    @patch("state.save_state", new_callable=AsyncMock)
    @patch("second_brain.save_brain", new_callable=AsyncMock)
    @patch("second_brain.load_brain", new_callable=AsyncMock)
    @patch("state.load_state", new_callable=AsyncMock)
    @patch("agent_setup.create_agent")
    @patch("cli.load_persona")
    @patch("builtins.input")
    def test_repl_runs_task_then_exits(
        self, mock_input, mock_lp, mock_ca, mock_ls, mock_lb, mock_sb, mock_ss,
        capsys,
    ):
        """User types a task, agent completes it, then user exits."""
        mock_input.side_effect = ["fix tests", "/exit"]
        mock_lp.return_value = _persona()
        mock_ls.return_value = AgentState()
        mock_lb.return_value = BrainState()

        agent = _mock_agent(
            "Fixed [TASK_COMPLETE]",
            json.dumps({"topic": "T", "content": "C", "tags": [], "category": "resources"}),
        )
        mock_ca.return_value = agent

        cmd_code(_args(task=None), _config())

        output = capsys.readouterr().out
        assert "Task complete" in output

    @patch("state.save_state", new_callable=AsyncMock)
    @patch("second_brain.save_brain", new_callable=AsyncMock)
    @patch("second_brain.load_brain", new_callable=AsyncMock)
    @patch("state.load_state", new_callable=AsyncMock)
    @patch("agent_setup.create_agent")
    @patch("cli.load_persona")
    @patch("builtins.input")
    def test_repl_help_command(
        self, mock_input, mock_lp, mock_ca, mock_ls, mock_lb, mock_sb, mock_ss,
        capsys,
    ):
        """/help shows instructions."""
        mock_input.side_effect = ["/help", "/exit"]
        mock_lp.return_value = _persona()
        mock_ls.return_value = AgentState()
        mock_lb.return_value = BrainState()
        mock_ca.return_value = _mock_agent()

        cmd_code(_args(task=None), _config())

        output = capsys.readouterr().out
        assert "/exit" in output

    @patch("state.save_state", new_callable=AsyncMock)
    @patch("second_brain.save_brain", new_callable=AsyncMock)
    @patch("second_brain.load_brain", new_callable=AsyncMock)
    @patch("state.load_state", new_callable=AsyncMock)
    @patch("agent_setup.create_agent")
    @patch("cli.load_persona")
    @patch("builtins.input")
    def test_repl_brain_command(
        self, mock_input, mock_lp, mock_ca, mock_ls, mock_lb, mock_sb, mock_ss,
        capsys,
    ):
        """/brain shows brain summary."""
        mock_input.side_effect = ["/brain", "/exit"]
        mock_lp.return_value = _persona()
        mock_ls.return_value = AgentState()
        mock_lb.return_value = BrainState()
        mock_ca.return_value = _mock_agent()

        cmd_code(_args(task=None), _config())

        output = capsys.readouterr().out
        assert "SECOND BRAIN" in output

    @patch("state.save_state", new_callable=AsyncMock)
    @patch("second_brain.save_brain", new_callable=AsyncMock)
    @patch("second_brain.load_brain", new_callable=AsyncMock)
    @patch("state.load_state", new_callable=AsyncMock)
    @patch("agent_setup.create_agent")
    @patch("cli.load_persona")
    @patch("builtins.input")
    def test_repl_keyboard_interrupt(
        self, mock_input, mock_lp, mock_ca, mock_ls, mock_lb, mock_sb, mock_ss,
        capsys,
    ):
        """Ctrl+C gracefully exits."""
        mock_input.side_effect = KeyboardInterrupt()
        mock_lp.return_value = _persona()
        mock_ls.return_value = AgentState()
        mock_lb.return_value = BrainState()
        mock_ca.return_value = _mock_agent()

        cmd_code(_args(task=None), _config())

        output = capsys.readouterr().out
        assert "Goodbye" in output or "bye" in output.lower()


# ══════════════════════════════════════════════════════════════════════
# H. Agent with tools — verify tools are passed through
# ══════════════════════════════════════════════════════════════════════

class TestCodingAgentTools:

    @patch("state.save_state", new_callable=AsyncMock)
    @patch("second_brain.save_brain", new_callable=AsyncMock)
    @patch("second_brain.load_brain", new_callable=AsyncMock)
    @patch("state.load_state", new_callable=AsyncMock)
    @patch("agent_setup.create_agent")
    @patch("cli.load_persona")
    def test_persona_tools_passed_to_agent(
        self, mock_lp, mock_ca, mock_ls, mock_lb, mock_sb, mock_ss,
    ):
        """Persona's tools are forwarded to create_agent."""
        def my_tool():
            """Custom tool."""
            pass

        mock_lp.return_value = _persona(tools=[my_tool])
        mock_ls.return_value = AgentState()
        mock_lb.return_value = BrainState()

        agent = _mock_agent(
            "Done [TASK_COMPLETE]",
            json.dumps({"topic": "T", "content": "C", "tags": [], "category": "resources"}),
        )
        mock_ca.return_value = agent

        cmd_code(_args(task="task"), _config())

        # Verify create_agent was called with the persona's tools
        call_kwargs = mock_ca.call_args
        tools_arg = call_kwargs.kwargs.get("tools") or call_kwargs[1].get("tools", [])
        # The tools list should contain our function
        assert my_tool in tools_arg

    @patch("state.save_state", new_callable=AsyncMock)
    @patch("second_brain.save_brain", new_callable=AsyncMock)
    @patch("second_brain.load_brain", new_callable=AsyncMock)
    @patch("state.load_state", new_callable=AsyncMock)
    @patch("agent_setup.create_agent")
    @patch("cli.load_persona")
    def test_persona_mcp_servers_passed_to_agent(
        self, mock_lp, mock_ca, mock_ls, mock_lb, mock_sb, mock_ss,
    ):
        """Persona's MCP servers are forwarded to create_agent."""
        servers = {"docker": {"type": "stdio", "command": "docker", "args": ["mcp"]}}
        mock_lp.return_value = _persona(mcp_servers=servers)
        mock_ls.return_value = AgentState()
        mock_lb.return_value = BrainState()

        agent = _mock_agent(
            "Done [TASK_COMPLETE]",
            json.dumps({"topic": "T", "content": "C", "tags": [], "category": "resources"}),
        )
        mock_ca.return_value = agent

        cmd_code(_args(task="task"), _config())

        call_kwargs = mock_ca.call_args
        mcp_arg = call_kwargs.kwargs.get("mcp_servers") or call_kwargs[1].get("mcp_servers")
        assert mcp_arg == servers


# ══════════════════════════════════════════════════════════════════════
# I. Output truncation
# ══════════════════════════════════════════════════════════════════════

class TestOutputHandling:

    @patch("state.save_state", new_callable=AsyncMock)
    @patch("second_brain.save_brain", new_callable=AsyncMock)
    @patch("second_brain.load_brain", new_callable=AsyncMock)
    @patch("state.load_state", new_callable=AsyncMock)
    @patch("agent_setup.create_agent")
    @patch("cli.load_persona")
    def test_long_output_truncated(
        self, mock_lp, mock_ca, mock_ls, mock_lb, mock_sb, mock_ss, capsys,
    ):
        """Agent output > 2000 chars is truncated in display."""
        mock_lp.return_value = _persona()
        mock_ls.return_value = AgentState()
        mock_lb.return_value = BrainState()

        long_text = "x" * 5000 + " [TASK_COMPLETE]"
        agent = _mock_agent(
            long_text,
            json.dumps({"topic": "T", "content": "C", "tags": [], "category": "resources"}),
        )
        mock_ca.return_value = agent

        cmd_code(_args(task="task"), _config())

        output = capsys.readouterr().out
        assert "truncated" in output.lower()
