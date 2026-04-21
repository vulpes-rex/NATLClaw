from __future__ import annotations

import json

from capture_policy import CapturePolicy
from second_brain import BrainState
from workflow import _store_capture

_WS_CAPTURE = CapturePolicy(
    reject_if_missing_evidence=True,
    evidence_burst_merge_window_minutes=20,
)


def test_workspace_observer_batches_burst_notes_by_evidence_overlap():
    brain = BrainState()
    raw_first = json.dumps(
        {
            "topic": "Initial touch",
            "content": "Touched scheduling and watcher files.",
            "tags": ["observer"],
            "category": "resources",
            "evidence": ["scheduler.py"],
            "confidence": 82,
        }
    )
    first_id = _store_capture(
        brain,
        raw_first,
        persona_name="workspace_observer",
        capture_policy=_WS_CAPTURE,
    )
    assert first_id is not None

    raw_second = json.dumps(
        {
            "topic": "Follow-up touch",
            "content": "Same area still active with additional watcher edits.",
            "tags": ["observer", "batch"],
            "category": "resources",
            "evidence": ["scheduler.py", "event_watcher.py"],
            "confidence": 84,
        }
    )
    second_id = _store_capture(
        brain,
        raw_second,
        persona_name="workspace_observer",
        capture_policy=_WS_CAPTURE,
    )
    assert second_id == first_id
    note = brain.notes[first_id]
    assert "event_watcher.py" in note["evidence"]
    assert "batch" in note["tags"]


def test_workspace_observer_creates_new_note_when_evidence_does_not_overlap():
    brain = BrainState()
    first = _store_capture(
        brain,
        json.dumps(
            {
                "topic": "Area one",
                "content": "Working in CLI watch status.",
                "tags": ["observer"],
                "category": "resources",
                "evidence": ["cli.py"],
            }
        ),
        persona_name="workspace_observer",
        capture_policy=_WS_CAPTURE,
    )
    second = _store_capture(
        brain,
        json.dumps(
            {
                "topic": "Area two",
                "content": "Now working in decision logic.",
                "tags": ["observer"],
                "category": "resources",
                "evidence": ["decision_engine.py"],
            }
        ),
        persona_name="workspace_observer",
        capture_policy=_WS_CAPTURE,
    )
    assert first is not None and second is not None
    assert first != second
