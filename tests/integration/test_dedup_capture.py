"""Category B: Dedup <-> workflow capture pipeline integration tests.

Verifies that find_duplicate / _token_overlap interact correctly with
_store_capture in the workflow, including across save/load boundaries.
"""
from __future__ import annotations

import json
import pytest
from datetime import datetime, timezone

from second_brain import BrainState, add_note, find_duplicate, load_brain, save_brain
from workflow import _store_capture


class TestDedupMergesInStoreCapture:
    """B1: Duplicate capture merges rather than creating a new note."""

    def test_near_duplicate_capture_merges_into_existing(self):
        brain = BrainState()
        # First capture — create note n0001
        first_json = json.dumps({
            "topic": "Python async patterns",
            "content": "Python asyncio uses coroutines and event loops for concurrent I/O-bound programming tasks",
            "tags": ["python", "async"],
            "category": "resources",
        })
        note_id_1 = _store_capture(brain, first_json)
        assert note_id_1 == "n0001"
        assert len(brain.notes) == 1

        # Second capture — near-duplicate content
        second_json = json.dumps({
            "topic": "Python async patterns updated",
            "content": "Python asyncio uses coroutines and event loops for concurrent I/O-bound programming tasks effectively",
            "tags": ["python", "async", "concurrency"],
            "category": "resources",
        })
        note_id_2 = _store_capture(brain, second_json)

        # Should merge into existing note, not create n0002
        assert note_id_2 == "n0001"
        assert len(brain.notes) == 1
        # Tags should be the union
        assert "concurrency" in brain.notes["n0001"]["tags"]

    def test_merged_note_updates_content_and_timestamp(self):
        brain = BrainState()
        first_json = json.dumps({
            "topic": "Infrastructure monitoring",
            "content": "Prometheus and Grafana provide comprehensive infrastructure monitoring alerting and dashboard visualization capabilities",
            "tags": ["monitoring"],
            "category": "resources",
        })
        _store_capture(brain, first_json)
        original_updated = brain.notes["n0001"]["updated_at"]

        updated_json = json.dumps({
            "topic": "Monitoring tools",
            "content": "Prometheus and Grafana provide comprehensive infrastructure monitoring alerting and dashboard visualization capabilities together",
            "tags": ["monitoring", "devops"],
            "category": "resources",
        })
        _store_capture(brain, updated_json)

        assert brain.notes["n0001"]["updated_at"] >= original_updated
        assert "together" in brain.notes["n0001"]["content"]


class TestDedupAcrossSaveLoadBoundary:
    """B2: Dedup works after brain is persisted and reloaded."""

    @pytest.mark.asyncio
    async def test_dedup_survives_save_load_roundtrip(self, tmp_path):
        state_file = str(tmp_path / "state.json")
        brain = BrainState()

        first_json = json.dumps({
            "topic": "Kubernetes scaling",
            "content": "Kubernetes horizontal pod autoscaler adjusts the number of pods in a deployment based on observed CPU utilization metrics",
            "tags": ["k8s", "scaling"],
            "category": "resources",
        })
        _store_capture(brain, first_json)
        assert len(brain.notes) == 1

        # Persist and reload
        await save_brain(brain, state_file)
        brain2 = await load_brain(state_file)

        # Now capture near-duplicate into reloaded brain
        dup_json = json.dumps({
            "topic": "K8s autoscaling",
            "content": "Kubernetes horizontal pod autoscaler adjusts the number of pods in a deployment based on observed CPU utilization metrics automatically",
            "tags": ["k8s", "scaling", "autoscaler"],
            "category": "resources",
        })
        note_id = _store_capture(brain2, dup_json)

        assert note_id == "n0001"
        assert len(brain2.notes) == 1
        assert "autoscaler" in brain2.notes["n0001"]["tags"]


class TestDedupDivergentContentCreatesSeparateNotes:
    """B3: Sufficiently different notes are stored as separate entries."""

    def test_divergent_notes_get_separate_ids(self):
        brain = BrainState()

        first_json = json.dumps({
            "topic": "Python type hints",
            "content": "Python type hints improve code readability and enable static analysis with tools like mypy and pyright",
            "tags": ["python", "types"],
            "category": "resources",
        })
        note_id_1 = _store_capture(brain, first_json)

        second_json = json.dumps({
            "topic": "Docker networking",
            "content": "Docker bridge networks provide isolated communication between containers using virtual network interfaces",
            "tags": ["docker", "networking"],
            "category": "resources",
        })
        note_id_2 = _store_capture(brain, second_json)

        assert note_id_1 == "n0001"
        assert note_id_2 == "n0002"
        assert len(brain.notes) == 2
        assert brain.notes["n0001"]["tags"] == ["python", "types"]
        assert brain.notes["n0002"]["tags"] == ["docker", "networking"]

    def test_no_false_positive_dedup_with_short_content(self):
        """Short notes with a few overlapping common words must not be merged."""
        brain = BrainState()
        _store_capture(brain, json.dumps({
            "topic": "A",
            "content": "The system uses Python for backend development and data processing",
            "tags": ["a"],
            "category": "resources",
        }))
        _store_capture(brain, json.dumps({
            "topic": "B",
            "content": "The team reviewed deployment pipeline configuration and CI/CD workflow integration",
            "tags": ["b"],
            "category": "resources",
        }))
        assert len(brain.notes) == 2
