"""External knowledge ingestion pipeline.

Reads files (text, markdown, JSON), chunks them, optionally summarises
each chunk via an LLM, and stores everything as brain notes.

Usage
-----
::

    from ingest import ingest_file, ingest_text

    # With an LLM agent for summarisation
    ids = await ingest_file(brain, "docs/architecture.md", agent=agent)

    # Without an agent (uses first-line heuristic for summary)
    ids = await ingest_text(brain, long_text, source="clipboard")
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from second_brain import BrainState, add_note

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────
# Chunking
# ──────────────────────────────────────────────────────────────────────

_DEFAULT_MAX_TOKENS = 500  # approximate word count per chunk
_DEFAULT_OVERLAP = 50      # words of overlap between chunks


def _chunk_text(
    text: str,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    overlap: int = _DEFAULT_OVERLAP,
) -> list[str]:
    """Split *text* into overlapping word-level chunks.

    Each chunk contains at most *max_tokens* whitespace-delimited words.
    Consecutive chunks share *overlap* words to preserve context.
    """
    words = text.split()
    if not words:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = start + max_tokens
        chunk = " ".join(words[start:end])
        chunks.append(chunk)
        if end >= len(words):
            break
        start = end - overlap
    return chunks


def _first_line_summary(text: str, max_len: int = 120) -> str:
    """Extract a one-line summary from the first non-empty line."""
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:max_len]
    return text[:max_len]


def _detect_tags(text: str) -> list[str]:
    """Auto-detect a few useful tags from the content."""
    tags: list[str] = []
    lower = text.lower()
    # File extension tags
    for ext in (".py", ".ts", ".tsx", ".js", ".md", ".json", ".yaml"):
        if ext in lower:
            tags.append(ext.lstrip("."))
    # Domain keywords (commercial lines insurance focus)
    for kw in ("insurance", "claims", "underwriting", "policy", "premium",
               "react", "python", "typescript", "docker", "api"):
        if kw in lower:
            tags.append(kw)
    return list(dict.fromkeys(tags))[:8]  # dedupe, cap at 8


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────

async def ingest_file(
    brain: BrainState,
    path: str,
    *,
    agent=None,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    overlap: int = _DEFAULT_OVERLAP,
    category: str = "resources",
) -> list[str]:
    """Read a file, chunk it, and store each chunk as a brain note.

    Parameters
    ----------
    brain : BrainState
        The brain to ingest into.
    path : str
        Path to the file to read.
    agent : optional
        An async agent with ``.run(prompt)`` → response with ``.text``.
        If provided, each chunk is summarised by the LLM; otherwise a
        heuristic first-line summary is used.
    max_tokens, overlap : int
        Chunking parameters (word counts).
    category : str
        PARA category for ingested notes (default ``"resources"``).

    Returns
    -------
    list[str]
        IDs of the notes created.
    """
    if not os.path.isfile(path):
        logger.error("[ingest] File not found: %s", path)
        return []

    content = Path(path).read_text(encoding="utf-8", errors="replace")
    source = f"file:{os.path.basename(path)}"
    return await ingest_text(
        brain,
        content,
        source=source,
        agent=agent,
        max_tokens=max_tokens,
        overlap=overlap,
        category=category,
    )


async def ingest_text(
    brain: BrainState,
    text: str,
    *,
    source: str = "ingested",
    agent=None,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    overlap: int = _DEFAULT_OVERLAP,
    category: str = "resources",
) -> list[str]:
    """Chunk a text blob and store each chunk as a brain note.

    Parameters
    ----------
    brain : BrainState
        Target brain.
    text : str
        Content to ingest.
    source : str
        Provenance label (e.g. ``"file:readme.md"``, ``"url:https://..."``).
    agent : optional
        LLM agent for summarisation.
    max_tokens, overlap, category : various
        See :func:`ingest_file`.

    Returns
    -------
    list[str]
        IDs of the notes created.
    """
    chunks = _chunk_text(text, max_tokens=max_tokens, overlap=overlap)
    if not chunks:
        logger.warning("[ingest] No content to ingest from source=%s", source)
        return []

    note_ids: list[str] = []
    for i, chunk in enumerate(chunks):
        chunk_source = f"{source}#chunk{i}"
        tags = _detect_tags(chunk)
        tags.insert(0, "ingested")

        # Summarise
        if agent is not None:
            try:
                resp = await agent.run(
                    f"Summarise this in one sentence:\n{chunk[:800]}"
                )
                summary = resp.text if hasattr(resp, "text") else str(resp)
                summary = summary.strip()[:200]
            except Exception as e:
                logger.warning("[ingest] LLM summary failed for chunk %d: %s", i, e)
                summary = _first_line_summary(chunk)
        else:
            summary = _first_line_summary(chunk)

        nid = add_note(
            brain,
            content=chunk,
            summary=summary,
            source=chunk_source,
            tags=tags,
            category=category,
        )
        note_ids.append(nid)

    logger.info("[ingest] Ingested %d chunks from %s", len(note_ids), source)
    return note_ids
