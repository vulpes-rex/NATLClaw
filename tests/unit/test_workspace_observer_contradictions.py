from __future__ import annotations

import json

from capture_policy import CapturePolicy
from second_brain import (
    BrainState,
    observer_evidence_overlap_sufficient,
    reconcile_evidence_contradictions,
    workspace_observer_contents_diverge,
)
from workflow import _store_capture

# Production uses a burst merge window; this test needs two distinct notes with
# overlapping evidence so reconciliation can run between them.
_CAPTURE_TWO_NOTE = CapturePolicy(
    reject_if_missing_evidence=True,
    evidence_burst_merge_window_minutes=0,
    after_capture="personas.workspace_observer.capture:after_note",
)


def test_observer_evidence_overlap_gating():
    a = {f"file:{i}" for i in range(8)}
    b = {f"file:{i}" for i in range(8, 16)}
    b.add("file:0")  # one incidental shared token with a
    assert not observer_evidence_overlap_sufficient(a, b)
    shared = {"file:x", "file:y"}
    assert observer_evidence_overlap_sufficient(
        shared | {"file:za"}, shared | {"file:zb"},
    )


def test_workspace_observer_divergence_heuristic():
    a = (
        "The user is exclusively configuring Kubernetes Helm charts for multi-region "
        "deployment pipelines and service mesh ingress rules across staging clusters."
    )
    b = (
        "Activity centers on pytest fixture design and asyncio mock patterns for the "
        "unit test suite in the scheduling subsystem with no infrastructure changes."
    )
    assert workspace_observer_contents_diverge(a, b) is True
    sim = (
        "The user is refactoring the scheduler module to support event-driven wakeups "
        "when file changes arrive from the repository watcher process."
    )
    sim2 = (
        "Refactoring scheduler code so heartbeats wake promptly on file change events "
        "from the repository watcher."
    )
    assert workspace_observer_contents_diverge(sim, sim2) is False


def test_reconcile_marks_contradiction_on_overlapping_evidence():
    brain = BrainState()
    first = json.dumps(
        {
            "topic": "Focus A",
            "content": (
                "The user is exclusively configuring Kubernetes Helm charts for "
                "multi-region deployment pipelines and service mesh ingress rules."
            ),
            "tags": ["observer"],
            "category": "resources",
            "evidence": ["file:scheduler.py", "commit:aaa1111"],
            "confidence": 70,
        }
    )
    id1 = _store_capture(
        brain,
        first,
        persona_name="workspace_observer",
        capture_policy=_CAPTURE_TWO_NOTE,
        step="analyse",
    )
    assert id1 is not None
    assert brain.notes[id1].get("contradiction_count", 0) == 0

    second = json.dumps(
        {
            "topic": "Focus B",
            "content": (
                "Activity centers on pytest fixture design and asyncio mock patterns "
                "for the unit test suite in the scheduling subsystem with no "
                "infrastructure or Helm involvement whatsoever in this repository."
            ),
            "tags": ["observer"],
            "category": "resources",
            "evidence": ["file:scheduler.py", "commit:bbb2222"],
            "confidence": 72,
        }
    )
    id2 = _store_capture(
        brain,
        second,
        persona_name="workspace_observer",
        capture_policy=_CAPTURE_TWO_NOTE,
        step="analyse",
    )
    assert id2 is not None
    n1, n2 = brain.notes[id1], brain.notes[id2]
    c1 = n1.get("contradiction_count", 0)
    c2 = n2.get("contradiction_count", 0)
    assert max(c1, c2) >= 1
    assert {n1.get("status"), n2.get("status")} & {"superseded", "tentative"}


def test_reconcile_idempotent_no_double_count():
    brain = BrainState()
    first = json.dumps(
        {
            "topic": "Focus A",
            "content": (
                "The user is exclusively configuring Kubernetes Helm charts for "
                "multi-region deployment pipelines and service mesh ingress rules."
            ),
            "tags": ["observer"],
            "category": "resources",
            "evidence": ["file:scheduler.py", "commit:aaa1111"],
            "confidence": 70,
        }
    )
    id1 = _store_capture(
        brain,
        first,
        persona_name="workspace_observer",
        capture_policy=_CAPTURE_TWO_NOTE,
        step="analyse",
    )
    second = json.dumps(
        {
            "topic": "Focus B",
            "content": (
                "Activity centers on pytest fixture design and asyncio mock patterns "
                "for the unit test suite in the scheduling subsystem with no "
                "infrastructure or Helm involvement whatsoever in this repository."
            ),
            "tags": ["observer"],
            "category": "resources",
            "evidence": ["file:scheduler.py", "commit:bbb2222"],
            "confidence": 72,
        }
    )
    id2 = _store_capture(
        brain,
        second,
        persona_name="workspace_observer",
        capture_policy=_CAPTURE_TWO_NOTE,
        step="analyse",
    )
    c1, c2 = (
        brain.notes[id1].get("contradiction_count", 0),
        brain.notes[id2].get("contradiction_count", 0),
    )
    demoted = id1 if c1 >= c2 else id2
    c_before = brain.notes[demoted].get("contradiction_count", 0)
    reconcile_evidence_contradictions(brain, id2)
    reconcile_evidence_contradictions(brain, id1)
    assert brain.notes[demoted].get("contradiction_count", 0) == c_before


def test_reconcile_skips_incidental_single_file_in_large_evidence_lists():
    """One shared path among many should not trigger contradiction even if stories diverge."""
    brain = BrainState()
    ev_a = [f"file:src/{i}.py" for i in range(10)]
    ev_b = ["file:src/0.py"] + [f"file:other/{i}.ts" for i in range(10)]
    first = json.dumps(
        {
            "topic": "A",
            "content": (
                "The user is exclusively configuring Kubernetes Helm charts for "
                "multi-region deployment pipelines and service mesh ingress rules."
            ),
            "tags": ["observer"],
            "category": "resources",
            "evidence": ev_a,
            "confidence": 70,
        }
    )
    _store_capture(
        brain,
        first,
        persona_name="workspace_observer",
        capture_policy=_CAPTURE_TWO_NOTE,
        step="analyse",
    )
    second = json.dumps(
        {
            "topic": "B",
            "content": (
                "Activity centers on pytest fixture design and asyncio mock patterns "
                "for the unit test suite in the scheduling subsystem with no "
                "infrastructure or Helm involvement whatsoever in this repository."
            ),
            "tags": ["observer"],
            "category": "resources",
            "evidence": ev_b,
            "confidence": 72,
        }
    )
    _store_capture(
        brain,
        second,
        persona_name="workspace_observer",
        capture_policy=_CAPTURE_TWO_NOTE,
        step="analyse",
    )
    assert all(
        brain.notes[nid].get("contradiction_count", 0) == 0
        for nid in brain.notes
    )
