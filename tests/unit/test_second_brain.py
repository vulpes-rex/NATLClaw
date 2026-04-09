"""Test suite for second_brain.py - Brain operations and error handling."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import pytest
import sys
from unittest.mock import MagicMock, patch

# Mock external dependencies
with patch.dict('sys.modules', {
    'agent_framework_github_copilot': MagicMock(),
    'agent_framework': MagicMock(),
    'agent_framework.foundry': MagicMock(),
    'agent_framework.openai': MagicMock(),
    'agent_framework.ollama': MagicMock(),
    'azure.identity': MagicMock(),
}):
    from second_brain import (
        BrainState,
        Note,
        add_note,
        apply_relevance_feedback,
        assign_note_to_topic,
        build_brain_stats,
        build_brain_stats_from_store,
        connect_notes,
        describe_note,
        describe_note_from_store,
        get_notes_by_category,
        get_recent_notes,
        get_topic_map,
        get_topic_map_from_store,
        build_brain_summary,
        load_brain,
        record_contradiction,
        save_brain,
        find_duplicate,
        decay_stale_notes,
        search_notes,
        search_notes_from_store,
        trace_topic,
        trace_topic_from_store,
        _brain_db_path,
        _brain_path,
    )
from state import AgentState

# Set up logging to avoid warnings during tests
logging.basicConfig(level=logging.DEBUG)


def _run(coro):
    """Helper to run an async coroutine synchronously in tests."""
    return asyncio.run(coro)


@pytest.fixture
def temp_state_file():
    """Create a temporary state file path for brain operations."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        f.write("{}")  # Empty state
        state_file = f.name
    yield state_file
    # Cleanup
    if os.path.exists(state_file):
        os.remove(state_file)
    brain_file = _brain_path(state_file)
    if os.path.exists(brain_file):
        os.remove(brain_file)
    brain_db = _brain_db_path(state_file)
    if os.path.exists(brain_db):
        os.remove(brain_db)


@pytest.fixture
def mock_brain():
    """Create a default BrainState."""
    return BrainState(
        notes={},
        connections=[],
        review_log=[],
        capture_count=0,
        last_review=None
    )


def test_brain_state_dataclass():
    """Test that BrainState is properly defined as a dataclass."""
    brain = BrainState()
    assert hasattr(brain, 'notes')
    assert hasattr(brain, 'connections')
    assert hasattr(brain, 'review_log')
    assert hasattr(brain, 'capture_count')
    assert hasattr(brain, 'last_review')


def test_note_dataclass():
    """Test that Note is properly defined as a dataclass."""
    note = Note(
        id="n0001",
        content="Test content",
        summary="Test summary",
        source="agent",
        note_type="pattern",
        status="active",
        confidence=85,
        evidence=["commit abc123"],
        tags=["test", "example"],
        category="resources",
        connections=["n0002"],
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
        last_accessed_at="2024-01-01T00:00:00Z",
        last_confirmed_at="2024-01-01T00:00:00Z",
        recall_count=3,
        positive_feedback=2,
        negative_feedback=1,
        contradiction_count=1,
        contradicted_by=["n0003"],
        feedback_log=[{"timestamp": "2024-01-02T00:00:00Z", "relevant": True, "reason": "validated"}],
        contradiction_log=[{"timestamp": "2024-01-03T00:00:00Z", "by_note_id": "n0003", "reason": "replaced"}],
    )
    assert note.id == "n0001"
    assert note.content == "Test content"
    assert note.tags == ["test", "example"]
    assert note.category == "resources"
    assert note.note_type == "pattern"
    assert note.confidence == 85
    assert note.recall_count == 3
    assert note.positive_feedback == 2


def test_add_note_success():
    """Test adding a note to the brain."""
    brain = BrainState()
    note_id = add_note(
        brain,
        content="Test note content",
        summary="Test summary",
        tags=["test"],
        category="resources"
    )
    assert note_id is not None
    assert note_id.startswith("n")
    assert len(brain.notes) == 1
    note_data = brain.notes[note_id]
    assert note_data["content"] == "Test note content"
    assert note_data["summary"] == "Test summary"
    assert note_data["tags"] == ["test"]
    assert note_data["category"] == "resources"
    assert note_data["created_at"] is not None
    assert note_data["updated_at"] is not None


def test_add_note_with_metadata_fields():
    """Notes can store type, status, confidence, and evidence."""
    brain = BrainState()
    note_id = add_note(
        brain,
        content="User prefers named exports",
        summary="Named export preference",
        note_type="preference",
        status="confirmed",
        confidence=92,
        evidence=["src/foo.ts", "src/bar.ts"],
        tags=["preference", "imports"],
    )

    note = brain.notes[note_id]
    assert note["note_type"] == "preference"
    assert note["status"] == "confirmed"
    assert note["confidence"] == 92
    assert note["evidence"] == ["src/foo.ts", "src/bar.ts"]
    assert note["last_accessed_at"]


def test_add_note_fallback_on_error():
    """Test that add_note falls back to minimal note on error."""
    brain = BrainState()
    # First attempt should succeed
    note_id = add_note(brain, content="Test content")
    assert note_id != "n0000"

    # Simulate serialization failure in both normal and fallback paths.
    def _broken_asdict(_value):
        raise Exception("Dataclass error")

    with patch.dict(add_note.__globals__, {"asdict": _broken_asdict}):
        note_id = add_note(brain, content="This should fail")
        assert note_id == "n0000"


def test_add_note_auto_increment():
    """Test that note IDs auto-increment."""
    brain = BrainState()
    note_id1 = add_note(brain, content="First note")
    note_id2 = add_note(brain, content="Second note")
    note_id3 = add_note(brain, content="Third note")

    assert note_id1 == "n0001"
    assert note_id2 == "n0002"
    assert note_id3 == "n0003"
    assert len(brain.notes) == 3


def test_connect_notes_success():
    """Test connecting two notes."""
    brain = BrainState()
    note_id1 = add_note(brain, content="Note 1", summary="First note")
    note_id2 = add_note(brain, content="Note 2", summary="Second note")

    connect_notes(brain, note_id1, note_id2, reason="Related topics")

    assert len(brain.connections) == 1
    connection = brain.connections[0]
    assert connection["from"] == note_id1
    assert connection["to"] == note_id2
    assert connection["reason"] == "Related topics"
    assert connection["created_at"] is not None

    assert note_id2 in brain.notes[note_id1]["connections"]
    assert note_id1 in brain.notes[note_id2]["connections"]


def test_connect_notes_invalid_ids():
    """Test connecting notes with invalid IDs."""
    brain = BrainState()
    note_id1 = add_note(brain, content="Note 1")

    connect_notes(brain, note_id1, "nonexistent", reason="Test")

    assert len(brain.connections) == 0


def test_connect_notes_self_connection():
    """Test that self-connections are created (current behavior)."""
    brain = BrainState()
    note_id = add_note(brain, content="Note 1")

    connect_notes(brain, note_id, note_id, reason="Self-reference")

    # Current implementation allows self-connections
    assert len(brain.connections) == 1


def test_get_notes_by_category():
    """Test filtering notes by category."""
    brain = BrainState()
    add_note(brain, content="Note 1", category="resources")
    add_note(brain, content="Note 2", category="projects")
    add_note(brain, content="Note 3", category="resources")
    add_note(brain, content="Note 4", category="areas")

    resources = get_notes_by_category(brain, "resources")
    projects = get_notes_by_category(brain, "projects")
    areas = get_notes_by_category(brain, "areas")
    archive = get_notes_by_category(brain, "archive")

    assert len(resources) == 2
    assert len(projects) == 1
    assert len(areas) == 1
    assert len(archive) == 0


def test_get_recent_notes():
    """Test getting recent notes."""
    brain = BrainState()
    add_note(brain, content="Note 1")
    add_note(brain, content="Note 2")
    add_note(brain, content="Note 3")
    add_note(brain, content="Note 4")
    add_note(brain, content="Note 5")
    add_note(brain, content="Note 6")

    recent = get_recent_notes(brain, count=3)
    assert len(recent) == 3
    # get_recent_notes returns most recent first (descending)
    recent_ids = [n["id"] for n in recent]
    assert recent_ids == ["n0006", "n0005", "n0004"]


def test_get_recent_notes_empty():
    """Test getting recent notes from empty brain."""
    brain = BrainState()
    recent = get_recent_notes(brain)
    assert len(recent) == 0


def test_build_brain_summary():
    """Test building a brain summary."""
    brain = BrainState()
    add_note(brain, content="Note 1", category="resources", tags=["tag1"])
    add_note(brain, content="Note 2", category="projects", tags=["tag2", "tag3"])
    add_note(brain, content="Note 3", category="resources")
    add_note(brain, content="Note 4", category="areas")

    connect_notes(brain, "n0001", "n0002", reason="Related")

    summary = build_brain_summary(brain)
    assert "Total notes: 4" in summary
    assert "Total connections: 1" in summary
    assert "resources=2" in summary
    assert "Recent knowledge:" in summary


def test_build_brain_summary_empty():
    """Test building brain summary for empty brain."""
    brain = BrainState()
    summary = build_brain_summary(brain)
    assert "Total notes: 0" in summary
    assert "Total connections: 0" in summary
    assert "Last review: never" in summary


def test_build_brain_stats_includes_types_topics_and_orphans():
    """Stats surface note types, top topics, and orphan counts."""
    brain = BrainState()
    n1 = add_note(brain, content="React hooks pattern", note_type="pattern", tags=["react", "hooks"])
    n2 = add_note(brain, content="User prefers named exports", note_type="preference", tags=["imports"])
    assign_note_to_topic(brain, n1, "React")
    assign_note_to_topic(brain, n1, "Hooks")
    assign_note_to_topic(brain, n2, "Style")
    connect_notes(brain, n1, n2, reason="frontend conventions")

    stats = build_brain_stats(brain)

    assert stats["notes"] == 2
    assert stats["connections"] == 1
    assert stats["note_types"]["pattern"] == 1
    assert stats["note_types"]["preference"] == 1
    assert stats["orphans"] == 0
    assert stats["top_topics"]
    assert any(topic["name"] == "React" for topic in stats["top_topics"])


def test_describe_note_returns_topics_pages_and_connections():
    """describe_note returns rich note context for inspection."""
    brain = BrainState()
    n1 = add_note(
        brain,
        content="Use feature flags for risky deploys",
        summary="Feature flag deploy guidance",
        note_type="decision",
        confidence=88,
        evidence=["docs/release.md"],
        tags=["deploy"],
    )
    n2 = add_note(brain, content="Rollback path must stay warm", summary="Rollback guidance")
    assign_note_to_topic(brain, n1, "Deployments")
    connect_notes(brain, n1, n2, reason="release safety")
    brain.pages["deployments"] = {
        "id": "deployments",
        "title": "Deployments",
        "content": "Deployment knowledge",
        "sources": [n1],
        "tags": ["deploy"],
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
    }

    desc = describe_note(brain, n1)

    assert desc is not None
    assert desc["note_type"] == "decision"
    assert desc["confidence"] == 88
    assert desc["topics"] == ["Deployments"]
    assert desc["source_pages"] == ["Deployments"]
    assert desc["connected_notes"][0]["id"] == n2


def test_trace_topic_returns_topics_and_reachable_notes():
    """trace_topic should traverse related topics and list reachable notes."""
    brain = BrainState()
    n1 = add_note(brain, content="React hooks reduce boilerplate", summary="Hooks")
    n2 = add_note(brain, content="Context can replace prop drilling", summary="Context")
    assign_note_to_topic(brain, n1, "React")
    assign_note_to_topic(brain, n2, "State")
    brain.topics["t0001"]["related_topics"] = ["t0002"]
    brain.topics["t0002"]["related_topics"] = ["t0001"]

    trace = trace_topic(brain, "React", depth=1, limit=10)

    assert trace is not None
    assert trace["topic"] == "React"
    assert len(trace["topics"]) == 2
    assert trace["total_notes"] == 2
    assert {note["id"] for note in trace["notes"]} == {n1, n2}


def test_search_notes_prefers_confident_connected_recent_note():
    """Ranking should favor stronger, fresher, connected notes over weak archived ones."""
    brain = BrainState()
    weak = add_note(
        brain,
        content="Deploy strategy for rollouts",
        summary="Old deploy note",
        status="archive",
        confidence=10,
    )
    strong = add_note(
        brain,
        content="Deploy strategy for rollouts",
        summary="Current deploy decision",
        note_type="decision",
        confidence=95,
        status="active",
    )
    helper = add_note(brain, content="Feature flags protect rollouts", summary="Feature flags")
    connect_notes(brain, strong, helper, reason="release safety")
    brain.notes[weak]["updated_at"] = "2020-01-01T00:00:00+00:00"

    results = search_notes(brain, "deploy strategy", max_results=2)

    assert results[0]["id"] == strong


def test_search_notes_records_access_signals():
    """Explicit retrieval should persist access counters in-memory."""
    brain = BrainState()
    note_id = add_note(brain, content="Deploy strategy prefers feature flags", summary="Deploy strategy")
    brain.notes[note_id]["last_accessed_at"] = ""

    results = search_notes(brain, "deploy strategy", max_results=5, record_access=True)

    assert results[0]["id"] == note_id
    assert brain.notes[note_id]["recall_count"] == 1
    assert brain.notes[note_id]["last_accessed_at"]


def test_search_notes_demotes_weak_and_contradicted_memories():
    """Negative feedback and contradiction signals should demote weaker notes."""
    brain = BrainState()
    outdated = add_note(
        brain,
        content="Deploy strategy uses manual approval gates",
        summary="Old deploy strategy",
        confidence=90,
    )
    current = add_note(
        brain,
        content="Deploy strategy uses manual approval gates",
        summary="Current deploy strategy",
        note_type="decision",
        confidence=72,
    )

    apply_relevance_feedback(brain, current, relevant=True, reason="Seen in current rollout docs")
    apply_relevance_feedback(brain, outdated, relevant=False, reason="Old playbook")
    record_contradiction(brain, outdated, current, reason="Automation replaced the manual gate")

    results = search_notes(brain, "deploy strategy", max_results=2)

    assert results[0]["id"] == current
    assert brain.notes[outdated]["status"] in {"tentative", "superseded"}


def test_store_backed_queries_match_saved_brain():
    """Store-backed search/stats/detail/trace helpers should work off the SQLite brain store."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "state.json")
        brain = BrainState()
        n1 = add_note(
            brain,
            content="Deploy strategy with feature flags",
            summary="Deploy strategy",
            note_type="decision",
            confidence=91,
            tags=["deploy"],
        )
        n2 = add_note(brain, content="Feature flags reduce rollout risk", summary="Flags")
        assign_note_to_topic(brain, n1, "Deployments")
        assign_note_to_topic(brain, n2, "Deployments")
        connect_notes(brain, n1, n2, reason="release safety")

        _run(save_brain(brain, state_file))
        os.remove(_brain_path(state_file))

        stats = build_brain_stats_from_store(state_file)
        results = search_notes_from_store(state_file, "deploy strategy", max_results=5, record_access=True)
        desc = describe_note_from_store(state_file, n1, record_access=True)
        topics = get_topic_map_from_store(state_file)
        trace = trace_topic_from_store(state_file, "Deployments", depth=1, limit=10, record_access=True)
        loaded = _run(load_brain(state_file))

        assert stats["notes"] == 2
        assert results[0]["id"] == n1
        assert desc is not None and desc["id"] == n1
        assert any(topic["name"] == "Deployments" for topic in topics)
        assert trace is not None and trace["total_notes"] == 2
        assert loaded.notes[n1]["recall_count"] >= 3
        assert loaded.notes[n1]["last_accessed_at"]
        assert loaded.notes[n2]["recall_count"] >= 1


def test_brain_path_derivation():
    """Test that brain path is correctly derived from state file."""
    # Use os.path.join for platform-independent comparison
    assert _brain_path("data/agent_state.json") == os.path.join("data", "brain.json")
    assert _brain_path("config/state.json") == os.path.join("config", "brain.json")
    assert _brain_db_path("data/agent_state.json") == os.path.join("data", "brain.db")
    assert _brain_db_path("config/state.json") == os.path.join("config", "brain.db")


def test_load_brain_file_not_found():
    """Test loading brain when file doesn't exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "state.json")
        brain = _run(load_brain(state_file))
        assert isinstance(brain, BrainState)
        assert brain.notes == {}
        assert brain.connections == []


def test_load_brain_success():
    """Test loading brain from valid JSON file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "state.json")
        brain_file = _brain_path(state_file)
        brain_db = _brain_db_path(state_file)

        test_brain = BrainState(
            notes={"n0001": {"id": "n0001", "content": "Test", "summary": "", "source": "agent", "tags": [], "category": "resources", "created_at": "2024-01-01T00:00:00Z", "updated_at": "2024-01-01T00:00:00Z"}},
            connections=[],
            review_log=[],
            capture_count=0,
            last_review=None
        )

        os.makedirs(os.path.dirname(brain_file), exist_ok=True)
        with open(brain_file, "w", encoding="utf-8") as f:
            json.dump(asdict(test_brain), f, indent=2)

        loaded_brain = _run(load_brain(state_file))
        assert loaded_brain.notes == test_brain.notes
        assert loaded_brain.connections == test_brain.connections
        assert os.path.exists(brain_db)


def test_load_brain_invalid_json():
    """Test loading brain with invalid JSON returns empty BrainState."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "state.json")
        brain_file = _brain_path(state_file)

        os.makedirs(os.path.dirname(brain_file), exist_ok=True)
        with open(brain_file, "w", encoding="utf-8") as f:
            f.write("{ invalid json }")

        brain = _run(load_brain(state_file))
        assert isinstance(brain, BrainState)
        assert brain.notes == {}


def test_save_brain_writes_valid_file():
    """Test that save_brain writes a valid JSON file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "state.json")
        brain_file = _brain_path(state_file)
        brain_db = _brain_db_path(state_file)

        brain = BrainState(
            notes={"n0001": {"id": "n0001", "content": "Test", "summary": "", "source": "agent", "tags": [], "category": "resources", "created_at": "2024-01-01T00:00:00Z", "updated_at": "2024-01-01T00:00:00Z"}},
            connections=[],
            review_log=[],
            capture_count=0,
            last_review=None
        )

        _run(save_brain(brain, state_file))

        with open(brain_file, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        assert "n0001" in loaded["notes"]
        assert loaded["notes"]["n0001"]["content"] == "Test"
        assert os.path.exists(brain_db)


def test_load_brain_from_sqlite_when_snapshot_missing():
    """SQLite is the primary store; load still works if brain.json is removed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "state.json")
        brain_file = _brain_path(state_file)

        brain = BrainState()
        add_note(brain, content="Persisted only in sqlite", summary="SQLite brain")
        _run(save_brain(brain, state_file))

        os.remove(brain_file)

        loaded_brain = _run(load_brain(state_file))
        assert len(loaded_brain.notes) == 1
        assert loaded_brain.notes["n0001"]["content"] == "Persisted only in sqlite"


def test_save_brain_max_review_limit():
    """Test that review log is limited to max_reviews."""
    brain = BrainState()
    for i in range(100):
        brain.review_log.append({
            "timestamp": f"2024-01-{(i % 28) + 1:02d}T00:00:00Z",
            "summary": f"Review {i}"
        })

    assert len(brain.review_log) == 100

    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "state.json")
        _run(save_brain(brain, state_file))
        loaded_brain = _run(load_brain(state_file))
        assert len(loaded_brain.review_log) == 50


def test_load_brain_handles_read_errors():
    """Test that load_brain handles read errors."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "state.json")
        brain_file = _brain_path(state_file)

        os.makedirs(os.path.dirname(brain_file), exist_ok=True)
        with open(brain_file, "w", encoding="utf-8") as f:
            f.write("valid json but permission denied test")
        os.chmod(brain_file, 0o000)

        try:
            brain = _run(load_brain(state_file))
            assert isinstance(brain, BrainState)
            assert brain.notes == {}
        finally:
            os.chmod(brain_file, 0o644)
            os.remove(brain_file)


def test_add_note_with_unicode():
    """Test adding notes with unicode characters."""
    brain = BrainState()
    note_id = add_note(brain, content="Test note with unicode: \u4f60\u597d \u2713", summary="Unicode summary")
    assert note_id is not None
    note = brain.notes[note_id]
    assert note["content"] == "Test note with unicode: \u4f60\u597d \u2713"


def test_connect_notes_with_unicode_reason():
    """Test connecting notes with unicode reason."""
    brain = BrainState()
    note_id1 = add_note(brain, content="Note 1")
    note_id2 = add_note(brain, content="Note 2")
    connect_notes(brain, note_id1, note_id2, reason="Unicode test: \u4f60\u597d \u2713")
    connection = brain.connections[0]
    assert connection["reason"] == "Unicode test: \u4f60\u597d \u2713"


def test_build_brain_summary_with_unicode():
    """Test building brain summary with unicode content."""
    brain = BrainState()
    add_note(brain, content="Unicode note: \u4f60\u597d \u2713", summary="Unicode summary")
    summary = build_brain_summary(brain)
    assert "Unicode summary" in summary


def test_get_recent_notes_sorted():
    """Test that recent notes are sorted by creation date descending."""
    brain = BrainState()
    notes_data = [
        {"id": "n0001", "content": "Oldest", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "n0002", "content": "Middle", "created_at": "2024-01-02T00:00:00Z"},
        {"id": "n0003", "content": "Newest", "created_at": "2024-01-03T00:00:00Z"},
    ]

    for note_data in notes_data:
        brain.notes[note_data["id"]] = note_data

    recent = get_recent_notes(brain, count=2)
    assert len(recent) == 2
    assert recent[0]["id"] == "n0003"
    assert recent[1]["id"] == "n0002"


def test_save_brain_with_large_content():
    """Test saving brain with large content."""
    brain = BrainState()
    large_content = "a" * 1000000
    add_note(brain, content=large_content)

    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "state.json")
        _run(save_brain(brain, state_file))
        loaded_brain = _run(load_brain(state_file))
        assert "n0001" in loaded_brain.notes


def test_brain_state_copy_on_write():
    """Test that modifying returned notes affects brain state (dict reference)."""
    brain = BrainState()
    add_note(brain, content="Test note")
    note_id = list(brain.notes.keys())[0]

    note = brain.notes[note_id]
    note["content"] = "Modified content"

    assert brain.notes[note_id]["content"] == "Modified content"


# --- New tests for dedup and decay ---

def test_find_duplicate_exact_match():
    """Test that find_duplicate detects exact duplicates."""
    brain = BrainState()
    add_note(brain, content="The quick brown fox jumps over the lazy dog")
    dup_id = find_duplicate(brain, "The quick brown fox jumps over the lazy dog")
    assert dup_id == "n0001"


def test_find_duplicate_near_match():
    """Test that find_duplicate detects near-duplicates above threshold."""
    brain = BrainState()
    # With threshold=0.50, Jaccard 9/11≈0.818 is well above threshold
    add_note(brain, content="The quick brown fox jumps over the lazy dog and rests")
    dup_id = find_duplicate(brain, "The quick brown fox jumps over the lazy dog and sleeps")
    assert dup_id == "n0001"  # 0.818 > 0.50 threshold

    brain2 = BrainState()
    add_note(brain2, content="The quick brown fox jumps over the lazy dog on the green hill near the river")
    dup_id2 = find_duplicate(brain2, "The quick brown fox jumps over the lazy dog on the green hill by the river")
    # intersection=12, union=14, sim=12/14≈0.857
    assert dup_id2 == "n0001"


def test_find_duplicate_no_match():
    """Test that find_duplicate returns None for dissimilar content."""
    brain = BrainState()
    add_note(brain, content="Python programming language features")
    dup_id = find_duplicate(brain, "Cooking recipes for Italian pasta dishes")
    assert dup_id is None


def test_decay_stale_notes():
    """Test that old orphan notes are archived."""
    brain = BrainState()
    # Add a note with an old timestamp
    brain.notes["n0001"] = {
        "id": "n0001",
        "content": "Old note",
        "category": "resources",
        "created_at": "2020-01-01T00:00:00Z",
        "connections": [],
    }
    # Set capture_count so add_note creates n0002 (not overwriting n0001)
    brain.capture_count = 1
    # Add a recent note
    add_note(brain, content="Recent note")

    archived = decay_stale_notes(brain, max_age_days=30)
    assert archived == 1
    assert brain.notes["n0001"]["category"] == "archive"
    assert brain.notes["n0002"]["category"] == "resources"


def test_decay_stale_notes_preserves_connected():
    """Test that old but connected notes are NOT archived."""
    brain = BrainState()
    brain.notes["n0001"] = {
        "id": "n0001",
        "content": "Old connected note",
        "category": "resources",
        "created_at": "2020-01-01T00:00:00Z",
        "connections": ["n0002"],
    }
    brain.notes["n0002"] = {
        "id": "n0002",
        "content": "Other old note",
        "category": "resources",
        "created_at": "2020-01-01T00:00:00Z",
        "connections": ["n0001"],
    }
    brain.connections.append({"from": "n0001", "to": "n0002", "reason": "related"})

    archived = decay_stale_notes(brain, max_age_days=30)
    assert archived == 0
    assert brain.notes["n0001"]["category"] == "resources"
    assert brain.notes["n0002"]["category"] == "resources"
