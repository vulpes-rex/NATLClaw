from __future__ import annotations

import asyncio
import json

from second_brain import BrainState, load_brain, save_brain
from workflow import _store_capture


def test_accepts_workspace_observer_note_with_evidence():
    brain = BrainState()
    raw = json.dumps(
        {
            "topic": "Current implementation focus",
            "content": "The user is updating queue-drain logic in observer tooling.",
            "tags": ["observer"],
            "category": "resources",
            "evidence": ["cli.py", "commit:abc1234"],
        }
    )
    note_id = _store_capture(
        brain,
        raw,
        persona_name="workspace_observer",
        heartbeat_number=1,
        step="analyse",
    )
    assert note_id is not None
    note = brain.notes[note_id]
    assert note["status"] == "active"
    assert note["evidence"] == ["cli.py", "commit:abc1234"]
    assert note["confidence"] == 70


def test_rejects_workspace_observer_but_flags_other_persona_without_evidence():
    observer_brain = BrainState()
    missing_evidence_raw = json.dumps(
        {
            "topic": "Unbacked claim",
            "content": "A claim with no citations.",
            "tags": ["observer"],
            "category": "resources",
        }
    )
    observer_note_id = _store_capture(
        observer_brain,
        missing_evidence_raw,
        persona_name="workspace_observer",
    )
    assert observer_note_id is None
    assert observer_brain.notes == {}

    general_brain = BrainState()
    general_note_id = _store_capture(
        general_brain,
        missing_evidence_raw,
        persona_name="researcher",
    )
    assert general_note_id is not None
    general_note = general_brain.notes[general_note_id]
    assert general_note["status"] == "invalid"
    assert "low_quality" in general_note["tags"]
    assert "missing_evidence" in general_note["tags"]
    assert general_note["confidence"] == 20


def test_evidence_persists_through_save_and_load(tmp_path):
    state_file = str(tmp_path / "state.json")
    brain = BrainState()
    raw = json.dumps(
        {
            "topic": "Evidence persistence",
            "content": "Evidence should survive persistence.",
            "tags": ["observer"],
            "category": "resources",
            "evidence": ["workflow.py", "commit:def4567"],
            "confidence": 88,
        }
    )
    note_id = _store_capture(
        brain,
        raw,
        persona_name="workspace_observer",
    )
    assert note_id is not None

    asyncio.run(save_brain(brain, state_file))
    loaded = asyncio.run(load_brain(state_file))
    note = loaded.notes[note_id]
    assert note["evidence"] == ["workflow.py", "commit:def4567"]
    assert note["confidence"] == 88
