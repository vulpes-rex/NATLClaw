"""Semantic search index for brain notes.

Uses sentence-transformers + FAISS to provide vector similarity search
over brain notes. Gracefully degrades to no-op when dependencies are
not installed (``pip install -e ".[semantic]"``).
"""

from __future__ import annotations

import logging
import time
from typing import Any

try:
    from sentence_transformers import SentenceTransformer
    import faiss
    import numpy as np

    _HAS_VECTOR_DEPS = True
except ImportError:
    _HAS_VECTOR_DEPS = False

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "all-MiniLM-L6-v2"
_COMPACT_THRESHOLD = 0.20  # rebuild when >20% of entries are removed


def _note_text(note: dict) -> str:
    """Combine summary and content into the text we embed."""
    summary = note.get("summary", "") or ""
    content = note.get("content", "") or ""
    return f"{summary}\n{content}".strip()


class BrainIndex:
    """In-memory FAISS index over brain note embeddings."""

    def __init__(self, model_name: str = _DEFAULT_MODEL) -> None:
        self._model_name = model_name
        self._model: Any = None
        self._index: Any = None
        self._note_ids: list[str] = []
        self._id_to_pos: dict[str, int] = {}
        self._removed: set[str] = set()
        self._dim: int = 0

    @property
    def available(self) -> bool:
        return _HAS_VECTOR_DEPS

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        self._model = SentenceTransformer(self._model_name)
        self._dim = self._model.get_sentence_embedding_dimension()

    def _embed(self, texts: list[str]) -> Any:
        self._ensure_model()
        return self._model.encode(
            texts, normalize_embeddings=True, show_progress_bar=False,
        ).astype(np.float32)

    def rebuild(self, notes: dict[str, dict]) -> int:
        """Batch-embed all non-archived notes and build the FAISS index.

        Returns the number of notes indexed.
        """
        if not _HAS_VECTOR_DEPS:
            return 0

        t0 = time.monotonic()

        # Filter out archived notes
        active: list[tuple[str, dict]] = [
            (nid, n) for nid, n in notes.items()
            if n.get("status", "active") != "archive"
        ]

        if not active:
            self._index = faiss.IndexFlatIP(self._dim or 384)
            self._note_ids = []
            self._id_to_pos = {}
            self._removed = set()
            return 0

        ids = [nid for nid, _ in active]
        texts = [_note_text(n) for _, n in active]

        embeddings = self._embed(texts)
        self._dim = embeddings.shape[1]

        self._index = faiss.IndexFlatIP(self._dim)
        self._index.add(embeddings)
        self._note_ids = ids
        self._id_to_pos = {nid: i for i, nid in enumerate(ids)}
        self._removed = set()

        elapsed = time.monotonic() - t0
        logger.info(
            "Brain index built: %d notes in %.1fs (dim=%d)",
            len(ids), elapsed, self._dim,
        )
        return len(ids)

    def add_note(self, note_id: str, note: dict) -> None:
        """Add a single note to the live index."""
        if not _HAS_VECTOR_DEPS or self._index is None:
            return
        if note.get("status", "active") == "archive":
            return

        text = _note_text(note)
        vec = self._embed([text])
        self._index.add(vec)
        pos = len(self._note_ids)
        self._note_ids.append(note_id)
        self._id_to_pos[note_id] = pos
        # If it was previously removed, un-remove it
        self._removed.discard(note_id)

    def remove_note(self, note_id: str) -> None:
        """Soft-remove a note from search results."""
        if note_id in self._id_to_pos:
            self._removed.add(note_id)
            # Compact if too many removals
            if (
                self._note_ids
                and len(self._removed) > len(self._note_ids) * _COMPACT_THRESHOLD
            ):
                self._compact()

    def _compact(self) -> None:
        """Rebuild index from surviving entries."""
        if not _HAS_VECTOR_DEPS or self._index is None:
            return

        surviving_ids = [
            nid for nid in self._note_ids if nid not in self._removed
        ]
        if not surviving_ids:
            self._index = faiss.IndexFlatIP(self._dim)
            self._note_ids = []
            self._id_to_pos = {}
            self._removed = set()
            return

        # Reconstruct vectors for surviving entries
        positions = [self._id_to_pos[nid] for nid in surviving_ids]
        vecs = np.vstack([
            self._index.reconstruct(pos) for pos in positions
        ])

        self._index = faiss.IndexFlatIP(self._dim)
        self._index.add(vecs)
        self._note_ids = surviving_ids
        self._id_to_pos = {nid: i for i, nid in enumerate(surviving_ids)}
        self._removed = set()
        logger.info("Brain index compacted to %d notes", len(surviving_ids))

    def query(self, text: str, k: int = 20) -> list[tuple[str, float]]:
        """Search for notes similar to *text*.

        Returns a list of ``(note_id, cosine_similarity)`` tuples,
        sorted by descending similarity.
        """
        if not _HAS_VECTOR_DEPS or self._index is None or self._index.ntotal == 0:
            return []

        vec = self._embed([text])
        # Request extra results to compensate for removed entries
        k_eff = min(k + len(self._removed), self._index.ntotal)
        scores, indices = self._index.search(vec, k_eff)

        results: list[tuple[str, float]] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self._note_ids):
                continue
            nid = self._note_ids[idx]
            if nid in self._removed:
                continue
            results.append((nid, float(score)))
            if len(results) >= k:
                break

        return results


# ---------------------------------------------------------------------------
# Module-level singleton API
# ---------------------------------------------------------------------------

_brain_index: BrainIndex | None = None


def get_brain_index() -> BrainIndex | None:
    """Return the singleton BrainIndex, or None if deps unavailable."""
    global _brain_index
    if not _HAS_VECTOR_DEPS:
        return None
    if _brain_index is None:
        _brain_index = BrainIndex()
    return _brain_index


def rebuild_index(notes: dict[str, dict]) -> int:
    """Rebuild the global index. Returns count indexed, or 0 if unavailable."""
    idx = get_brain_index()
    if idx is None:
        return 0
    return idx.rebuild(notes)


def index_note(note_id: str, note: dict) -> None:
    """Add a single note to the live index."""
    idx = get_brain_index()
    if idx is not None and idx._index is not None:
        idx.add_note(note_id, note)


def semantic_search(query: str, k: int = 20) -> list[tuple[str, float]]:
    """Return (note_id, score) pairs, or empty list if unavailable."""
    idx = get_brain_index()
    if idx is None or idx._index is None:
        return []
    return idx.query(query, k=k)
