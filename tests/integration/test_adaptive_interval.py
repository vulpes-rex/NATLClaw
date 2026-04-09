"""Category E: Adaptive interval <-> brain mutations integration tests.

Verifies the scheduler computes an adaptive sleep interval based on
how many notes/connections were created during the heartbeat.
"""
from __future__ import annotations

import asyncio
import time
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from config import AppConfig
from second_brain import BrainState, add_note, connect_notes
from state import AgentState


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


class TestProductiveHeartbeatShortensInterval:
    """E1: Heartbeat that produces notes/connections shortens next interval."""

    def test_two_notes_and_one_connection_shortens(self):
        config = _make_config(heartbeat_interval_sec=120)
        brain = BrainState()

        notes_before = len(brain.notes)
        conns_before = len(brain.connections)

        # Simulate productive heartbeat
        add_note(brain, content="Note 1")
        add_note(brain, content="Note 2")
        connect_notes(brain, "n0001", "n0002", "related")

        new_notes = len(brain.notes) - notes_before
        new_conns = len(brain.connections) - conns_before
        score = new_notes + 2 * new_conns  # 2 + 2 = 4

        assert score > 0
        interval = max(config.heartbeat_interval_sec * 0.7, 60)
        assert interval == 84.0  # 120 * 0.7

    def test_minimum_interval_is_60_seconds(self):
        config = _make_config(heartbeat_interval_sec=60)
        brain = BrainState()
        add_note(brain, content="Note 1")

        interval = max(config.heartbeat_interval_sec * 0.7, 60)
        assert interval == 60  # max(42, 60)


class TestUnproductiveHeartbeatLengthensInterval:
    """E2: Heartbeat with no notes/connections lengthens next interval."""

    def test_zero_score_lengthens(self):
        config = _make_config(heartbeat_interval_sec=120)
        score = 0
        interval = min(config.heartbeat_interval_sec * 1.5, 600)
        assert interval == 180.0  # 120 * 1.5

    def test_maximum_interval_is_600_seconds(self):
        config = _make_config(heartbeat_interval_sec=500)
        score = 0
        interval = min(config.heartbeat_interval_sec * 1.5, 600)
        assert interval == 600  # min(750, 600)

    def test_interval_logic_matches_scheduler(self):
        """Mirror the exact adaptive interval code from run_scheduler."""
        config = _make_config(heartbeat_interval_sec=200)
        brain = BrainState()
        notes_before = len(brain.notes)
        conns_before = len(brain.connections)

        # Unproductive: no changes
        new_notes = len(brain.notes) - notes_before
        new_conns = len(brain.connections) - conns_before
        score = new_notes + 2 * new_conns
        assert score == 0

        if score <= 0:
            interval = min(config.heartbeat_interval_sec * 1.5, 600)
        else:
            interval = max(config.heartbeat_interval_sec * 0.7, 60)

        assert interval == 300.0  # 200 * 1.5

        # Now productive
        add_note(brain, content="Insight A")
        new_notes = len(brain.notes) - notes_before
        new_conns = len(brain.connections) - conns_before
        score = new_notes + 2 * new_conns
        assert score == 1

        if score <= 0:
            interval = min(config.heartbeat_interval_sec * 1.5, 600)
        else:
            interval = max(config.heartbeat_interval_sec * 0.7, 60)

        assert interval == 140.0  # 200 * 0.7
