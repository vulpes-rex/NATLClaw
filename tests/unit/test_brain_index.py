"""Tests for brain_index.py — semantic search over brain notes.

All tests mock sentence-transformers and faiss so the test suite
does not require ML dependencies to be installed.
"""

from __future__ import annotations

import hashlib
import sys
import types
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers: fake embedding model + in-process FAISS
# ---------------------------------------------------------------------------

_DIM = 384


def _deterministic_vec(text: str) -> list[float]:
    """Return a deterministic unit vector derived from *text*."""
    seed = int(hashlib.md5(text.encode()).hexdigest()[:8], 16)
    rng = __import__("random").Random(seed)
    vec = [rng.gauss(0, 1) for _ in range(_DIM)]
    norm = sum(v ** 2 for v in vec) ** 0.5
    return [v / norm for v in vec]


def _make_numpy():
    """Return real numpy (needed for FAISS array ops)."""
    import numpy as np
    return np


# We need *real* numpy + faiss for the index math to work correctly.
# If they're not installed, skip these tests entirely.
np = pytest.importorskip("numpy", reason="numpy required for brain_index tests")

try:
    import faiss as _faiss
except ImportError:
    _faiss = None


needs_faiss = pytest.mark.skipif(_faiss is None, reason="faiss-cpu not installed")


class FakeSentenceTransformer:
    """Deterministic embedder that produces consistent vectors per text."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name

    def encode(self, texts, normalize_embeddings=True, show_progress_bar=False):
        vecs = [_deterministic_vec(t) for t in texts]
        arr = np.array(vecs, dtype="float32")
        if normalize_embeddings:
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            norms[norms == 0] = 1
            arr = arr / norms
        return arr

    def get_sentence_embedding_dimension(self):
        return _DIM


def _make_sample_notes(n: int = 5) -> dict[str, dict]:
    """Create N sample notes with diverse content."""
    topics = [
        ("Deployment pipeline", "The CI/CD deployment pipeline pushes containers to staging then production"),
        ("Authentication flow", "OAuth2 token exchange with PKCE for browser-based single-page apps"),
        ("Database schema", "PostgreSQL schema with users, sessions, and audit_logs tables"),
        ("Error handling", "Global exception handler catches unhandled errors and logs stack traces"),
        ("Test coverage", "Unit tests cover 85% of the codebase with integration tests for API endpoints"),
    ]
    notes = {}
    for i in range(min(n, len(topics))):
        nid = f"n{i+1:04d}"
        title, content = topics[i]
        notes[nid] = {
            "id": nid,
            "content": content,
            "summary": title,
            "status": "active",
            "tags": [title.split()[0].lower()],
            "note_type": "general",
            "confidence": 80,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-04-01T00:00:00+00:00",
        }
    return notes


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the module-level singleton between tests."""
    import brain_index
    brain_index._brain_index = None
    yield
    brain_index._brain_index = None


@pytest.fixture()
def _patch_model():
    """Patch SentenceTransformer to use our deterministic fake."""
    with patch("brain_index.SentenceTransformer", FakeSentenceTransformer):
        yield


# ---------------------------------------------------------------------------
# Tests: graceful degradation
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    def test_available_false_when_deps_missing(self):
        import brain_index
        original = brain_index._HAS_VECTOR_DEPS
        try:
            brain_index._HAS_VECTOR_DEPS = False
            assert brain_index.get_brain_index() is None
            assert brain_index.semantic_search("test") == []
            assert brain_index.rebuild_index({"n0001": {"content": "x"}}) == 0
            # index_note should be a no-op
            brain_index.index_note("n0001", {"content": "x"})
        finally:
            brain_index._HAS_VECTOR_DEPS = original

    def test_semantic_search_empty_when_no_index(self):
        import brain_index
        # Singleton exists but index was never built
        idx = brain_index.get_brain_index()
        if idx is not None:
            assert idx._index is None
        assert brain_index.semantic_search("test") == []


# ---------------------------------------------------------------------------
# Tests: BrainIndex core (require faiss + numpy)
# ---------------------------------------------------------------------------


@needs_faiss
class TestBrainIndexCore:
    def test_rebuild_indexes_notes(self, _patch_model):
        import brain_index
        notes = _make_sample_notes(5)
        count = brain_index.rebuild_index(notes)
        assert count == 5
        idx = brain_index.get_brain_index()
        assert idx._index.ntotal == 5
        assert len(idx._note_ids) == 5

    def test_rebuild_excludes_archived(self, _patch_model):
        import brain_index
        notes = _make_sample_notes(3)
        notes["n0002"]["status"] = "archive"
        count = brain_index.rebuild_index(notes)
        assert count == 2
        idx = brain_index.get_brain_index()
        assert "n0002" not in idx._note_ids

    def test_rebuild_empty_brain(self, _patch_model):
        import brain_index
        count = brain_index.rebuild_index({})
        assert count == 0
        assert brain_index.semantic_search("anything") == []

    def test_query_returns_results(self, _patch_model):
        import brain_index
        notes = _make_sample_notes(5)
        brain_index.rebuild_index(notes)
        results = brain_index.semantic_search("deployment pipeline CI/CD", k=3)
        assert len(results) > 0
        assert len(results) <= 3
        # Each result is (note_id, score)
        for nid, score in results:
            assert nid.startswith("n")
            assert isinstance(score, float)

    def test_add_note_incremental(self, _patch_model):
        import brain_index
        notes = _make_sample_notes(3)
        brain_index.rebuild_index(notes)
        idx = brain_index.get_brain_index()
        assert idx._index.ntotal == 3

        brain_index.index_note("n0099", {
            "content": "New note about caching",
            "summary": "Redis caching layer",
            "status": "active",
        })
        assert idx._index.ntotal == 4
        assert "n0099" in idx._note_ids

    def test_remove_note_excluded(self, _patch_model):
        import brain_index
        notes = _make_sample_notes(5)
        brain_index.rebuild_index(notes)
        idx = brain_index.get_brain_index()

        idx.remove_note("n0001")
        results = brain_index.semantic_search("deployment", k=10)
        result_ids = [nid for nid, _ in results]
        assert "n0001" not in result_ids

    def test_remove_triggers_compact(self, _patch_model):
        import brain_index
        notes = _make_sample_notes(5)
        brain_index.rebuild_index(notes)
        idx = brain_index.get_brain_index()

        # Remove 2 of 5 (40% > 20% threshold) — should trigger compact
        idx.remove_note("n0001")
        idx.remove_note("n0002")
        # After compact, removed set should be cleared
        assert len(idx._removed) == 0
        assert idx._index.ntotal == 3
        assert len(idx._note_ids) == 3

    def test_text_preparation(self):
        from brain_index import _note_text
        note = {"summary": "My Summary", "content": "Full content here"}
        assert _note_text(note) == "My Summary\nFull content here"

    def test_text_preparation_missing_fields(self):
        from brain_index import _note_text
        assert _note_text({}) == ""
        assert _note_text({"summary": "Only summary"}) == "Only summary"


# ---------------------------------------------------------------------------
# Tests: hybrid search integration
# ---------------------------------------------------------------------------


class TestHybridSearch:
    def test_rank_notes_hybrid_semantic_only_match(self):
        """A note with zero lexical overlap but high semantic score is included."""
        from second_brain import _rank_notes_hybrid

        notes = [
            {
                "id": "n0001",
                "content": "shipping containers to production servers",
                "summary": "production release",
                "tags": ["ops"],
                "note_type": "general",
                "status": "active",
                "confidence": 80,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-04-01T00:00:00+00:00",
            },
        ]
        # Query "deployment" has no substring match in the note
        results = _rank_notes_hybrid(
            notes, [], "deployment",
            semantic_scores={"n0001": 0.85},
            max_results=5,
        )
        assert len(results) == 1
        assert results[0]["id"] == "n0001"

    def test_rank_notes_hybrid_no_semantic_no_lexical(self):
        """A note with zero semantic and zero lexical score is excluded."""
        from second_brain import _rank_notes_hybrid

        notes = [
            {
                "id": "n0001",
                "content": "unrelated note about cooking",
                "summary": "recipe",
                "tags": [],
                "note_type": "general",
                "status": "active",
                "confidence": 50,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-04-01T00:00:00+00:00",
            },
        ]
        results = _rank_notes_hybrid(
            notes, [], "deployment",
            semantic_scores={},  # no semantic hit for this note
            max_results=5,
        )
        assert len(results) == 0

    def test_hybrid_lexical_plus_semantic_boost(self):
        """A note matching both lexically and semantically ranks higher."""
        from second_brain import _rank_notes_hybrid

        notes = [
            {
                "id": "n0001",
                "content": "deployment pipeline for production",
                "summary": "deployment",
                "tags": ["ci"],
                "note_type": "general",
                "status": "active",
                "confidence": 80,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-04-01T00:00:00+00:00",
            },
            {
                "id": "n0002",
                "content": "shipping to production servers",
                "summary": "release process",
                "tags": ["ops"],
                "note_type": "general",
                "status": "active",
                "confidence": 80,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-04-01T00:00:00+00:00",
            },
        ]
        results = _rank_notes_hybrid(
            notes, [], "deployment",
            semantic_scores={"n0001": 0.90, "n0002": 0.85},
            max_results=5,
        )
        assert len(results) == 2
        # n0001 should rank first (has both lexical "deployment" + semantic)
        assert results[0]["id"] == "n0001"


# ---------------------------------------------------------------------------
# Tests: API endpoints
# ---------------------------------------------------------------------------


class TestAPI:
    @pytest.fixture()
    def client(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi not installed")
        from api_server import create_app
        from config import AppConfig
        cfg = AppConfig()
        cfg.state_file = "test_state.json"
        app = create_app(cfg)
        return TestClient(app, raise_server_exceptions=False)

    def test_search_passes_semantic_flag(self, client):
        with patch("api_server.search_notes_from_store", return_value=[]) as mock_search:
            resp = client.post("/api/brain/search", json={"query": "test", "semantic": False})
            assert resp.status_code == 200
            data = resp.json()
            assert data["semantic"] is False
            mock_search.assert_called_once()
            _, kwargs = mock_search.call_args
            assert kwargs.get("semantic") is False

    def test_brain_stats_includes_index(self, client):
        with patch("api_server.build_brain_stats_from_store", return_value={"notes": 10}):
            resp = client.get("/api/brain/stats")
            assert resp.status_code == 200
            data = resp.json()
            assert "semantic_index" in data
            assert "available" in data["semantic_index"]
            assert "indexed_notes" in data["semantic_index"]

    def test_reindex_endpoint(self, client):
        with patch("brain_index.get_brain_index") as mock_get, \
             patch("brain_index.rebuild_index", return_value=42):
            mock_get.return_value = MagicMock(_index=True)
            with patch("api_server.load_brain") as mock_lb:
                mock_lb.return_value = MagicMock(notes={"n0001": {}})
                resp = client.post("/api/brain/reindex")
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "ok"
                assert data["indexed"] == 42

    def test_reindex_unavailable(self, client):
        with patch("brain_index.get_brain_index", return_value=None):
            resp = client.post("/api/brain/reindex")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "unavailable"
