from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import cli


def test_cmd_brain_dream_dry_run(monkeypatch, capsys):
    async def _load_brain(_state_file):
        return object()

    calls: dict[str, int] = {"save": 0}

    def _run_dream_cycle(_brain, **kwargs):
        assert kwargs["apply"] is False
        return {
            "timestamp": "2026-04-14T12:00:00+00:00",
            "phases": {
                "gather": {"unconsolidated": 2},
                "consolidate": {"exact_duplicates_archived": 1},
                "prune": {"stale_archived": 0, "lint_issues": 3},
            },
            "before": {"notes": 10, "orphans": 4},
            "after": {"notes": 9, "orphans": 3},
        }

    async def _save_brain(_brain, _state_file):
        calls["save"] += 1

    monkeypatch.setattr("second_brain.load_brain", _load_brain)
    monkeypatch.setattr("second_brain.run_dream_cycle", _run_dream_cycle)
    monkeypatch.setattr("second_brain.save_brain", _save_brain)

    cli.cmd_brain_dream(
        argparse.Namespace(apply=False, heartbeat=0, max_age_days=30, json=False, compact=False),
        config=SimpleNamespace(state_file="data/state.json"),
    )
    out = capsys.readouterr().out
    assert "Dream cycle (DRY-RUN)" in out
    assert "consolidate.exact_duplicates_archived=1" in out
    assert calls["save"] == 0


def test_cmd_brain_dream_apply_saves(monkeypatch, capsys):
    async def _load_brain(_state_file):
        return object()

    calls: dict[str, int] = {"save": 0}

    def _run_dream_cycle(_brain, **kwargs):
        assert kwargs["apply"] is True
        assert kwargs["heartbeat_number"] == 77
        return {
            "timestamp": "2026-04-14T12:00:00+00:00",
            "phases": {
                "gather": {"unconsolidated": 0},
                "consolidate": {"exact_duplicates_archived": 0},
                "prune": {"stale_archived": 0, "lint_issues": 0},
            },
            "before": {"notes": 1, "orphans": 0},
            "after": {"notes": 1, "orphans": 0},
        }

    async def _save_brain(_brain, _state_file):
        calls["save"] += 1

    monkeypatch.setattr("second_brain.load_brain", _load_brain)
    monkeypatch.setattr("second_brain.run_dream_cycle", _run_dream_cycle)
    monkeypatch.setattr("second_brain.save_brain", _save_brain)

    cli.cmd_brain_dream(
        argparse.Namespace(apply=True, heartbeat=77, max_age_days=30, json=False, compact=False),
        config=SimpleNamespace(state_file="data/state.json"),
    )
    out = capsys.readouterr().out
    assert "Dream cycle (APPLY)" in out
    assert calls["save"] == 1


def test_cmd_brain_dream_json_output(monkeypatch, capsys):
    async def _load_brain(_state_file):
        return object()

    def _run_dream_cycle(_brain, **kwargs):
        return {
            "applied": kwargs["apply"],
            "timestamp": "2026-04-14T12:00:00+00:00",
            "phases": {
                "gather": {"unconsolidated": 1},
                "consolidate": {"exact_duplicates_archived": 0},
                "prune": {"stale_archived": 0, "lint_issues": 0},
            },
            "before": {"notes": 3, "orphans": 1},
            "after": {"notes": 3, "orphans": 1},
        }

    async def _save_brain(_brain, _state_file):
        return None

    monkeypatch.setattr("second_brain.load_brain", _load_brain)
    monkeypatch.setattr("second_brain.run_dream_cycle", _run_dream_cycle)
    monkeypatch.setattr("second_brain.save_brain", _save_brain)

    cli.cmd_brain_dream(
        argparse.Namespace(apply=False, heartbeat=0, max_age_days=30, json=True, compact=False),
        config=SimpleNamespace(state_file="data/state.json"),
    )
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["applied"] is False
    assert payload["phases"]["gather"]["unconsolidated"] == 1


def test_cmd_brain_dream_json_compact_output(monkeypatch, capsys):
    async def _load_brain(_state_file):
        return object()

    def _run_dream_cycle(_brain, **kwargs):
        return {
            "applied": kwargs["apply"],
            "timestamp": "2026-04-14T12:00:00+00:00",
            "phases": {
                "gather": {"unconsolidated": 1},
                "consolidate": {"exact_duplicates_archived": 0},
                "prune": {"stale_archived": 0, "lint_issues": 0},
            },
            "before": {"notes": 3, "orphans": 1},
            "after": {"notes": 3, "orphans": 1},
        }

    async def _save_brain(_brain, _state_file):
        return None

    monkeypatch.setattr("second_brain.load_brain", _load_brain)
    monkeypatch.setattr("second_brain.run_dream_cycle", _run_dream_cycle)
    monkeypatch.setattr("second_brain.save_brain", _save_brain)

    cli.cmd_brain_dream(
        argparse.Namespace(apply=False, heartbeat=0, max_age_days=30, json=True, compact=True),
        config=SimpleNamespace(state_file="data/state.json"),
    )
    out = capsys.readouterr().out
    assert "\n" not in out.strip()
    payload = json.loads(out)
    assert payload["phases"]["prune"]["lint_issues"] == 0


def test_cmd_brain_dream_policy_text(monkeypatch, capsys):
    def _load_persona(_name):
        return SimpleNamespace(
            name="workspace_observer",
            dream_enabled=True,
            dream_idle_streak_min=4,
            dream_max_age_days=21,
        )

    monkeypatch.setattr("cli.load_persona", _load_persona)

    cli.cmd_brain_dream(
        argparse.Namespace(
            apply=False,
            heartbeat=0,
            max_age_days=30,
            json=False,
            compact=False,
            policy=True,
        ),
        config=SimpleNamespace(state_file="data/state.json", persona="workspace_observer"),
    )
    out = capsys.readouterr().out
    assert "Dream policy (workspace_observer):" in out
    assert "enabled=True" in out
    assert "idle_streak_min=4" in out
    assert "max_age_days=21" in out


def test_cmd_brain_dream_policy_json_compact(monkeypatch, capsys):
    def _load_persona(_name):
        return SimpleNamespace(
            name="default",
            dream_enabled=False,
            dream_idle_streak_min=6,
            dream_max_age_days=40,
        )

    monkeypatch.setattr("cli.load_persona", _load_persona)

    cli.cmd_brain_dream(
        argparse.Namespace(
            apply=False,
            heartbeat=0,
            max_age_days=30,
            json=True,
            compact=True,
            policy=True,
        ),
        config=SimpleNamespace(state_file="data/state.json", persona="default"),
    )
    out = capsys.readouterr().out
    assert "\n" not in out.strip()
    payload = json.loads(out)
    assert payload["dream"]["enabled"] is False
    assert payload["dream"]["idle_streak_min"] == 6
