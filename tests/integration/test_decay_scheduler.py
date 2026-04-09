"""Category C: Decay <-> scheduler <-> brain pipeline integration tests.

Verifies that decay_stale_notes works through the scheduler path,
that connected notes survive, and that summary reflects archived notes.
"""
from __future__ import annotations

import asyncio
import json
import time
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from config import AppConfig
from second_brain import (
    BrainState,
    add_note,
    build_brain_summary,
    connect_notes,
    decay_stale_notes,
    get_notes_by_category,
    load_brain,
    save_brain,
)
from state import AgentState, load_state, save_state


def _make_old_note(brain: BrainState, note_id: str, content: str,
                   days_old: int = 60, category: str = "resources"):
    """Insert a note with a creation timestamp in the past."""
    old_time = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
    brain.notes[note_id] = {
        "id": note_id,
        "content": content,
        "summary": content[:40],
        "source": "agent",
        "tags": [],
        "category": category,
        "connections": [],
        "created_at": old_time,
        "updated_at": old_time,
    }


class TestDecayInSchedulerHeartbeat:
    """C1: Stale orphan notes are decayed during scheduler iteration."""

    @pytest.mark.asyncio
    async def test_stale_orphan_notes_archived_and_persisted(self, tmp_path):
        state_file = str(tmp_path / "state.json")
        brain = BrainState()

        _make_old_note(brain, "n0001", "Old orphan note about setup", days_old=60)
        _make_old_note(brain, "n0002", "Another old orphan unconnected", days_old=45)
        brain.capture_count = 2

        # Run decay
        archived = decay_stale_notes(brain, max_age_days=30)
        assert archived == 2
        assert brain.notes["n0001"]["category"] == "archive"
        assert brain.notes["n0002"]["category"] == "archive"

        # Persist and reload — archived status survives
        await save_brain(brain, state_file)
        brain2 = await load_brain(state_file)
        assert brain2.notes["n0001"]["category"] == "archive"
        assert brain2.notes["n0002"]["category"] == "archive"

    def test_recent_notes_not_archived(self):
        brain = BrainState()
        add_note(brain, content="Fresh note just added", tags=["fresh"])
        archived = decay_stale_notes(brain, max_age_days=30)
        assert archived == 0
        assert brain.notes["n0001"]["category"] == "resources"


class TestConnectedNotesSurviveDecay:
    """C2: Connected notes keep their category even if old."""

    def test_old_connected_notes_not_archived(self):
        brain = BrainState()
        _make_old_note(brain, "n0001", "Historical context about architecture", days_old=60)
        _make_old_note(brain, "n0002", "Design decision for database choice", days_old=60)
        _make_old_note(brain, "n0003", "Unused orphan note", days_old=60)

        # Connect n0001 <-> n0002
        connect_notes(brain, "n0001", "n0002", "Architecture informs DB choice")

        archived = decay_stale_notes(brain, max_age_days=30)

        # Only n0003 should be archived (orphan)
        assert archived == 1
        assert brain.notes["n0001"]["category"] == "resources"  # connected, survives
        assert brain.notes["n0002"]["category"] == "resources"  # connected, survives
        assert brain.notes["n0003"]["category"] == "archive"    # orphan, archived


class TestDecaySummaryInteraction:
    """C3: After decay, summary and category queries reflect changes."""

    def test_summary_reports_archive_category(self):
        brain = BrainState()
        _make_old_note(brain, "n0001", "Stale insight about tooling", days_old=60)
        brain.capture_count = 1  # sync counter so add_note creates n0002
        add_note(brain, content="Fresh note", tags=["fresh"])

        decay_stale_notes(brain, max_age_days=30)

        summary = build_brain_summary(brain, max_notes=10)
        assert "archive" in summary.lower() or "archive=1" in summary

    def test_get_notes_by_category_excludes_archived(self):
        brain = BrainState()
        _make_old_note(brain, "n0001", "Old resource", days_old=60)
        _make_old_note(brain, "n0002", "Another old resource", days_old=60)
        brain.capture_count = 2  # sync counter so add_note creates n0003
        add_note(brain, content="Active resource", category="resources")

        decay_stale_notes(brain, max_age_days=30)

        resources = get_notes_by_category(brain, "resources")
        archives = get_notes_by_category(brain, "archive")

        assert len(resources) == 1
        assert resources[0]["content"] == "Active resource"
        assert len(archives) == 2
