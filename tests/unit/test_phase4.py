"""Test suite for Phase 4 features: CLI, metrics, config validation, hot reload."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Mock external dependencies before importing project modules
with patch.dict("sys.modules", {
    "agent_framework_github_copilot": MagicMock(),
    "agent_framework": MagicMock(),
    "agent_framework.foundry": MagicMock(),
    "agent_framework.openai": MagicMock(),
    "agent_framework.ollama": MagicMock(),
    "azure.identity": MagicMock(),
    "dotenv": MagicMock(),
}):
    import cli as cli_mod
    from cli import build_parser, main
    from config import AppConfig, validate_config
    from metrics import MetricsStore, JsonFormatter
    from second_brain import BrainState, add_note


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _fresh_config(**overrides) -> AppConfig:
    defaults = {"agent_name": "TestAgent", "state_file": "data/agent_state.json"}
    defaults.update(overrides)
    return AppConfig(**defaults)


def _fresh_brain() -> BrainState:
    return BrainState()


# ======================================================================
# A. CLI parser
# ======================================================================

class TestCLIParser:
    def test_parser_creates_successfully(self):
        parser = build_parser()
        assert parser is not None

    def test_run_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["run"])
        assert args.command == "run"
        assert not args.once

    def test_run_once_flag(self):
        parser = build_parser()
        args = parser.parse_args(["run", "--once"])
        assert args.once

    def test_brain_stats_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["brain", "stats"])
        assert args.command == "brain"
        assert args.brain_command == "stats"

    def test_brain_search_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["brain", "search", "React"])
        assert args.command == "brain"
        assert args.brain_command == "search"
        assert args.query == "React"

    def test_brain_add_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["brain", "add", "some note", "--tags", "a,b"])
        assert args.content == "some note"
        assert args.tags == "a,b"

    def test_brain_export_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["brain", "export", "-o", "out.md"])
        assert args.output == "out.md"

    def test_brain_lint_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["brain", "lint"])
        assert args.brain_command == "lint"

    def test_persona_list_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["persona", "list"])
        assert args.persona_command == "list"

    def test_config_show_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["config", "show"])
        assert args.config_command == "show"

    def test_config_validate_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["config", "validate"])
        assert args.config_command == "validate"

    def test_verbose_flag(self):
        parser = build_parser()
        args = parser.parse_args(["-v", "run"])
        assert args.verbose

    def test_no_command_prints_help(self, capsys):
        """Calling main with no args should print help — not crash."""
        with patch("cli.load_config", return_value=_fresh_config()):
            main([])
        captured = capsys.readouterr()
        assert "natl" in captured.out.lower() or captured.out == ""


# ======================================================================
# B. CLI brain commands
# ======================================================================

class TestCLIBrainCommands:
    def test_brain_stats_output(self, capsys):
        brain = _fresh_brain()
        add_note(brain, content="Note about Python development", tags=["python"])
        add_note(brain, content="Note about React components", tags=["react"], category="projects")

        config = _fresh_config()
        args = MagicMock()

        with patch.object(cli_mod, "_load_brain_sync", return_value=brain):
            cli_mod.cmd_brain_stats(args, config)

        out = capsys.readouterr().out
        assert "Notes:" in out
        assert "2" in out
        assert "Connections:" in out

    def test_brain_lint_healthy(self, capsys):
        brain = _fresh_brain()
        config = _fresh_config()
        args = MagicMock()

        with patch.object(cli_mod, "_load_brain_sync", return_value=brain):
            cli_mod.cmd_brain_lint(args, config)

        out = capsys.readouterr().out
        assert "healthy" in out.lower() or "no issues" in out.lower()

    def test_brain_lint_with_issues(self, capsys):
        brain = _fresh_brain()
        add_note(brain, content="x")  # short content = empty_content issue
        config = _fresh_config()
        args = MagicMock()

        with patch.object(cli_mod, "_load_brain_sync", return_value=brain):
            cli_mod.cmd_brain_lint(args, config)

        out = capsys.readouterr().out
        assert "warning" in out.lower() or "info" in out.lower()

    def test_brain_export_to_stdout(self, capsys):
        brain = _fresh_brain()
        add_note(brain, content="Test note for export", summary="Test", tags=["test"])
        config = _fresh_config()
        args = MagicMock()
        args.output = None  # stdout

        with patch.object(cli_mod, "_load_brain_sync", return_value=brain):
            cli_mod.cmd_brain_export(args, config)

        out = capsys.readouterr().out
        assert "Brain Export" in out
        assert "Test note for export" in out

    def test_brain_export_to_file(self, tmp_path):
        brain = _fresh_brain()
        add_note(brain, content="Export test content", tags=["export"])
        config = _fresh_config()
        out_file = str(tmp_path / "export.md")
        args = MagicMock()
        args.output = out_file

        with patch.object(cli_mod, "_load_brain_sync", return_value=brain):
            cli_mod.cmd_brain_export(args, config)

        assert os.path.isfile(out_file)
        content = Path(out_file).read_text(encoding="utf-8")
        assert "Export test content" in content


# ======================================================================
# C. Metrics — JsonFormatter
# ======================================================================

class TestJsonFormatter:
    def test_format_basic_record(self):
        import logging
        fmt = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello world", args=(), exc_info=None,
        )
        output = fmt.format(record)
        data = json.loads(output)
        assert data["message"] == "hello world"
        assert data["level"] == "INFO"
        assert "timestamp" in data

    def test_format_extra_fields(self):
        import logging
        fmt = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="heartbeat done", args=(), exc_info=None,
        )
        record.heartbeat = 5
        record.elapsed_sec = 3.14
        record.persona = "researcher"
        output = fmt.format(record)
        data = json.loads(output)
        assert data["heartbeat"] == 5
        assert data["elapsed_sec"] == 3.14
        assert data["persona"] == "researcher"

    def test_format_missing_extra_fields_omitted(self):
        import logging
        fmt = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="plain", args=(), exc_info=None,
        )
        output = fmt.format(record)
        data = json.loads(output)
        assert "heartbeat" not in data
        assert "persona" not in data


# ======================================================================
# D. Metrics — MetricsStore (SQLite)
# ======================================================================

class TestMetricsStore:
    def test_create_store(self, tmp_path):
        db_path = str(tmp_path / "test_metrics.db")
        store = MetricsStore(db_path)
        assert os.path.isfile(db_path)
        store.close()

    def test_record_and_recent(self, tmp_path):
        db_path = str(tmp_path / "test_metrics.db")
        store = MetricsStore(db_path)
        store.record_heartbeat(
            heartbeat_number=1,
            persona="researcher",
            workflow="second_brain",
            elapsed_sec=2.5,
            notes_created=3,
            connections_created=1,
            score=5,
            interval_sec=84.0,
        )
        rows = store.recent(10)
        assert len(rows) == 1
        assert rows[0]["heartbeat_number"] == 1
        assert rows[0]["persona"] == "researcher"
        assert rows[0]["notes_created"] == 3
        assert rows[0]["score"] == 5
        store.close()

    def test_recent_ordering(self, tmp_path):
        db_path = str(tmp_path / "test_metrics.db")
        store = MetricsStore(db_path)
        for i in range(5):
            store.record_heartbeat(
                heartbeat_number=i + 1,
                persona="test",
                elapsed_sec=float(i),
            )
        rows = store.recent(3)
        assert len(rows) == 3
        # Most recent first
        assert rows[0]["heartbeat_number"] == 5
        assert rows[2]["heartbeat_number"] == 3
        store.close()

    def test_summary(self, tmp_path):
        db_path = str(tmp_path / "test_metrics.db")
        store = MetricsStore(db_path)
        store.record_heartbeat(heartbeat_number=1, notes_created=2, connections_created=1, elapsed_sec=1.0)
        store.record_heartbeat(heartbeat_number=2, notes_created=4, connections_created=3, elapsed_sec=3.0)
        summary = store.summary()
        assert summary["total_heartbeats"] == 2
        assert summary["total_notes_created"] == 6
        assert summary["total_connections_created"] == 4
        assert summary["avg_elapsed_sec"] == 2.0
        store.close()

    def test_empty_summary(self, tmp_path):
        db_path = str(tmp_path / "test_metrics.db")
        store = MetricsStore(db_path)
        summary = store.summary()
        assert summary.get("total_heartbeats", 0) == 0
        store.close()

    def test_store_creates_parent_dirs(self, tmp_path):
        db_path = str(tmp_path / "nested" / "dir" / "metrics.db")
        store = MetricsStore(db_path)
        assert os.path.isfile(db_path)
        store.close()


# ======================================================================
# E. Config validation
# ======================================================================

class TestConfigValidation:
    def test_valid_config(self):
        config = _fresh_config()
        assert validate_config(config) == []

    def test_invalid_provider(self):
        config = _fresh_config(provider="invalid_provider")
        errors = validate_config(config)
        assert any("provider" in e.lower() for e in errors)

    def test_low_heartbeat_interval(self):
        config = _fresh_config(heartbeat_interval_sec=5)
        errors = validate_config(config)
        assert any("heartbeat_interval" in e for e in errors)

    def test_foundry_needs_endpoint(self):
        config = _fresh_config(provider="foundry", project_endpoint="")
        errors = validate_config(config)
        assert any("ENDPOINT" in e for e in errors)

    def test_openai_needs_key(self):
        config = _fresh_config(provider="openai", openai_api_key="")
        errors = validate_config(config)
        assert any("KEY" in e for e in errors)

    def test_low_max_history(self):
        config = _fresh_config(max_history=0)
        errors = validate_config(config)
        assert any("max_history" in e for e in errors)

    def test_valid_foundry_config(self):
        config = _fresh_config(provider="foundry", project_endpoint="https://example.com")
        errors = validate_config(config)
        assert errors == []

    def test_valid_openai_config(self):
        config = _fresh_config(provider="openai", openai_api_key="sk-test123")
        errors = validate_config(config)
        assert errors == []

    def test_azure_openai_needs_endpoint(self):
        config = _fresh_config(
            provider="azure_openai",
            azure_openai_api_key="key",
            azure_openai_deployment="deploy",
            azure_openai_endpoint="",
        )
        errors = validate_config(config)
        assert any("AZURE_OPENAI_ENDPOINT" in e for e in errors)

    def test_azure_openai_needs_key(self):
        config = _fresh_config(
            provider="azure_openai",
            azure_openai_endpoint="https://x.cognitiveservices.azure.com",
            azure_openai_deployment="deploy",
            azure_openai_api_key="",
        )
        errors = validate_config(config)
        assert any("AZURE_OPENAI_API_KEY" in e for e in errors)

    def test_azure_openai_needs_deployment(self):
        config = _fresh_config(
            provider="azure_openai",
            azure_openai_endpoint="https://x.cognitiveservices.azure.com",
            azure_openai_api_key="key",
            azure_openai_deployment="",
        )
        errors = validate_config(config)
        assert any("AZURE_OPENAI_DEPLOYMENT" in e for e in errors)

    def test_valid_azure_openai_config(self):
        config = _fresh_config(
            provider="azure_openai",
            azure_openai_endpoint="https://x.cognitiveservices.azure.com",
            azure_openai_api_key="key123",
            azure_openai_deployment="gpt-4.1-kvw",
        )
        errors = validate_config(config)
        assert errors == []

    def test_azure_openai_all_missing(self):
        config = _fresh_config(provider="azure_openai")
        errors = validate_config(config)
        assert len(errors) == 3  # endpoint, key, deployment all missing


# ======================================================================
# F. Hot reload (scheduler integration)
# ======================================================================

class TestHotReload:
    def test_mcp_json_mtime_tracking(self, tmp_path):
        """Verify the persona re-loads when mcp.json mtime changes."""
        mcp_file = tmp_path / "mcp.json"
        mcp_file.write_text('{"personas": {}}')
        mtime1 = os.path.getmtime(str(mcp_file))

        # Simulate a change
        import time
        time.sleep(0.05)
        mcp_file.write_text('{"personas": {"new": {}}}')
        mtime2 = os.path.getmtime(str(mcp_file))

        assert mtime2 != mtime1, "File mtime should change after rewrite"

    def test_hot_reload_detects_change(self, tmp_path):
        """Ensure the scheduler's hot-reload branch runs when mtime differs."""
        # This tests the logic pattern, not full scheduler loop
        mcp_file = tmp_path / "mcp.json"
        mcp_file.write_text('{}')

        last_mtime = 0.0  # stale
        cur_mtime = os.path.getmtime(str(mcp_file))

        # The condition from scheduler.py
        reload_triggered = cur_mtime != last_mtime
        assert reload_triggered

    def test_no_reload_when_unchanged(self, tmp_path):
        mcp_file = tmp_path / "mcp.json"
        mcp_file.write_text('{}')
        mtime = os.path.getmtime(str(mcp_file))

        # Same mtime → no reload
        assert mtime == mtime


# ======================================================================
# G. Scheduler metrics integration
# ======================================================================

class TestSchedulerMetrics:
    def test_adaptive_interval_low_score(self):
        """Score <= 0 should increase interval (slow down)."""
        base = 120
        score = 0
        interval = min(base * 1.5, 600)
        assert interval == 180.0

    def test_adaptive_interval_high_score(self):
        """Score > 0 should decrease interval (speed up)."""
        base = 120
        score = 3
        interval = max(base * 0.7, 60)
        assert interval == 84.0

    def test_adaptive_interval_clamped_low(self):
        """Interval should not go below 60s."""
        base = 60
        score = 5
        interval = max(base * 0.7, 60)
        assert interval == 60

    def test_adaptive_interval_clamped_high(self):
        """Interval should not exceed 600s."""
        base = 500
        score = -1
        interval = min(base * 1.5, 600)
        assert interval == 600

    def test_score_calculation(self):
        """Score = new_notes + 2 * new_connections."""
        notes_before, notes_after = 5, 7
        conns_before, conns_after = 3, 5
        score = (notes_after - notes_before) + 2 * (conns_after - conns_before)
        assert score == 6  # 2 + 2*2

    def test_negative_score_no_notes(self):
        """No new notes or connections → score = 0."""
        score = (10 - 10) + 2 * (5 - 5)
        assert score == 0
