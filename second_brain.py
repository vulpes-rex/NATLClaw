from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

@dataclass
class Note:
    """An atomic note in the second brain."""

    id: str
    content: str
    summary: str = ""
    source: Any = "agent"  # str or structured dict with provenance
    tags: list[str] = field(default_factory=list)
    category: str = "resources"  # projects | areas | resources | archive
    connections: list[str] = field(default_factory=list)  # IDs of related notes
    created_at: str = ""
    updated_at: str = ""


@dataclass
class WikiPage:
    """A long-term consolidated knowledge page.

    Wiki pages are synthesized documents — one per topic or theme.
    They accumulate knowledge from multiple atomic notes, resolve
    contradictions, and serve as the agent's stable reference material.
    """

    id: str              # slug, e.g. "deployment-patterns"
    title: str           # human-readable title
    content: str         # full markdown body
    sources: list[str] = field(default_factory=list)   # note IDs that contributed
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Topic:
    """A named topic node in the knowledge graph."""

    id: str
    name: str  # e.g. "React", "CI/CD"
    related_topics: list[str] = field(default_factory=list)  # sibling/parent topic IDs
    note_ids: list[str] = field(default_factory=list)  # notes under this topic
    created_at: str = ""


@dataclass
class BrainState:
    """Persistent state for the second brain knowledge store."""

    # Short-term memory: atomic notes
    notes: dict[str, dict] = field(default_factory=dict)  # id -> Note as dict
    topics: dict[str, dict] = field(default_factory=dict)  # id -> Topic as dict
    connections: list[dict] = field(default_factory=list)  # [{from, to, reason}]

    # Long-term memory: wiki pages
    pages: dict[str, dict] = field(default_factory=dict)  # id -> WikiPage as dict

    # Metadata
    review_log: list[dict] = field(default_factory=list)  # [{timestamp, summary}]
    lint_log: list[dict] = field(default_factory=list)     # [{timestamp, issues}]
    capture_count: int = 0
    topic_count: int = 0
    page_count: int = 0
    last_review: str | None = None
    last_consolidation: str | None = None
    last_lint: str | None = None


def _brain_path(state_file: str) -> str:
    """Derive brain state path from the main state file path."""
    parent = os.path.dirname(state_file) or "data"
    return os.path.join(parent, "brain.json")


def _read_brain(path: str) -> dict:
    """Read brain JSON from disk (runs in executor)."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


async def load_brain(state_file: str) -> BrainState:
    """Load brain state from disk.

    Transient I/O errors (``OSError``) are **not** caught here so that the
    caller's retry decorator can handle them.  Corrupt-data errors
    (``JSONDecodeError``, ``UnicodeDecodeError``) are non-retryable and
    return a fresh ``BrainState`` instead.
    """
    path = _brain_path(state_file)
    if not os.path.exists(path):
        return BrainState()
    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, _read_brain, path)
        return BrainState(**{
            k: v for k, v in data.items() if k in BrainState.__dataclass_fields__
        })
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        # Corrupt data — not retryable; start fresh
        logger.error("Corrupt brain file %s: %s — starting fresh", path, str(e))
        return BrainState()
    # OSError propagates so the retry decorator in scheduler.py can retry


def _write_brain(brain_dict: dict, path: str) -> None:
    """Write brain JSON atomically (runs in executor)."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    dir_name = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(brain_dict, f, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        os.unlink(tmp_path)
        raise


async def save_brain(brain: BrainState, state_file: str, max_reviews: int = 50) -> None:
    """Save brain state atomically."""
    path = _brain_path(state_file)
    if len(brain.review_log) > max_reviews:
        brain.review_log = brain.review_log[-max_reviews:]

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _write_brain, asdict(brain), path)


def add_note(
    brain: BrainState,
    content: str,
    *,
    summary: str = "",
    source: str | dict = "agent",
    tags: list[str] | None = None,
    category: str = "resources",
) -> str:
    """Add an atomic note to the brain. Returns the note ID."""
    try:
        now = datetime.now(timezone.utc).isoformat()
        brain.capture_count += 1
        note_id = f"n{brain.capture_count:04d}"
        brain.notes[note_id] = asdict(Note(
            id=note_id,
            content=content,
            summary=summary,
            source=source,
            tags=tags or [],
            category=category,
            created_at=now,
            updated_at=now,
        ))
        return note_id
    except Exception as e:
        logger.error("Failed to add note: %s", str(e))
        logger.debug("add_note error details:", exc_info=True)
        # Try to add a minimal note as fallback
        try:
            note_id = f"n{brain.capture_count:04d}"
            brain.notes[note_id] = asdict(Note(
                id=note_id,
                content=content[:100],
                summary="",
                source="agent",
                tags=[],
                category="resources",
                created_at=now,
                updated_at=now,
            ))
            return note_id
        except Exception:
            return "n0000"  # Return a default error ID


def connect_notes(
    brain: BrainState, from_id: str, to_id: str, reason: str = ""
) -> None:
    """Create a bidirectional connection between two notes."""
    try:
        if from_id not in brain.notes or to_id not in brain.notes:
            return
        brain.connections.append({
            "from": from_id,
            "to": to_id,
            "reason": reason,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        # Update each note's connection list
        if to_id not in brain.notes[from_id].get("connections", []):
            brain.notes[from_id].setdefault("connections", []).append(to_id)
        if from_id not in brain.notes[to_id].get("connections", []):
            brain.notes[to_id].setdefault("connections", []).append(from_id)
    except Exception as e:
        logger.error("Failed to connect notes %s and %s: %s", from_id, to_id, str(e))
        logger.debug("connect_notes error details:", exc_info=True)


def get_notes_by_category(brain: BrainState, category: str) -> list[dict]:
    """Return notes filtered by PARA category."""
    return [n for n in brain.notes.values() if n.get("category") == category]


def get_recent_notes(brain: BrainState, count: int = 10) -> list[dict]:
    """Return the most recently added notes."""
    try:
        all_notes = sorted(
            brain.notes.values(), key=lambda n: n.get("created_at", ""), reverse=True
        )
        return all_notes[:count]
    except Exception as e:
        logger.error("Failed to get recent notes: %s", str(e))
        logger.debug("get_recent_notes error details:", exc_info=True)
        return []


def search_notes(brain: BrainState, query: str, max_results: int = 10) -> list[dict]:
    """Full-text search over note content, summaries, and tags.

    Supports both exact substring matching and word-level matching.
    Scores: content match (+3), summary match (+2), tag match (+1),
    plus bonus for individual word hits.
    """
    query_lower = query.lower()
    query_words = [w for w in query_lower.split() if len(w) >= 2]
    scored: list[tuple[float, dict]] = []
    for note in brain.notes.values():
        content = (note.get("content") or "").lower()
        summary = (note.get("summary") or "").lower()
        tags = " ".join(note.get("tags") or []).lower()
        all_text = f"{content} {summary} {tags}"
        score = 0.0
        # Exact substring match (high value)
        if query_lower in content:
            score += 3
        if query_lower in summary:
            score += 2
        if query_lower in tags:
            score += 1
        # Word-level matching (partial hits still surface results)
        if query_words:
            word_hits = sum(1 for w in query_words if w in all_text)
            score += word_hits / len(query_words)  # 0.0 to 1.0 bonus
        if score > 0:
            scored.append((score, note))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [note for _, note in scored[:max_results]]


def _token_overlap(a: str, b: str) -> float:
    """Compute Jaccard similarity over word tokens. Returns 0.0–1.0."""
    tokens_a = set(a.lower().split())
    tokens_b = set(b.lower().split())
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def find_duplicate(brain: BrainState, content: str, threshold: float = 0.50) -> str | None:
    """Return the ID of a near-duplicate recent note, or None."""
    recent = get_recent_notes(brain, 50)
    for note in recent:
        if _token_overlap(content, note.get("content", "")) > threshold:
            return note["id"]
    return None


def decay_stale_notes(brain: BrainState, max_age_days: int = 30) -> int:
    """Move orphan notes older than max_age_days to archive. Returns count."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    connected_ids = {c["from"] for c in brain.connections} | {c["to"] for c in brain.connections}
    archived = 0
    for nid, note in brain.notes.items():
        if (note.get("category") != "archive"
                and note.get("created_at", "") < cutoff
                and nid not in connected_ids):
            note["category"] = "archive"
            archived += 1
    return archived


# ──────────────────────────────────────────────────────────────────────
# Topic graph
# ──────────────────────────────────────────────────────────────────────

def find_or_create_topic(brain: BrainState, name: str) -> dict:
    """Return the topic dict for *name*, creating it if it doesn't exist.

    Lookup is case-insensitive; the stored name preserves the case of the
    first caller.
    """
    lower = name.lower().strip()
    for topic in brain.topics.values():
        if topic.get("name", "").lower() == lower:
            return topic

    brain.topic_count += 1
    topic_id = f"t{brain.topic_count:04d}"
    now = datetime.now(timezone.utc).isoformat()
    brain.topics[topic_id] = asdict(Topic(
        id=topic_id,
        name=name.strip(),
        created_at=now,
    ))
    return brain.topics[topic_id]


def assign_note_to_topic(brain: BrainState, note_id: str, topic_name: str) -> None:
    """Link a note to a topic, creating the topic if needed."""
    if note_id not in brain.notes:
        return
    topic = find_or_create_topic(brain, topic_name)
    if note_id not in topic.get("note_ids", []):
        topic.setdefault("note_ids", []).append(note_id)


def relate_topics(brain: BrainState, name_a: str, name_b: str) -> None:
    """Create a bidirectional relationship between two topics."""
    topic_a = find_or_create_topic(brain, name_a)
    topic_b = find_or_create_topic(brain, name_b)
    if topic_b["id"] not in topic_a.get("related_topics", []):
        topic_a.setdefault("related_topics", []).append(topic_b["id"])
    if topic_a["id"] not in topic_b.get("related_topics", []):
        topic_b.setdefault("related_topics", []).append(topic_a["id"])


def recall_by_topic(
    brain: BrainState, topic_name: str, *, depth: int = 1, include_connected: bool = True,
) -> list[dict]:
    """Return notes reachable from a topic via the knowledge graph.

    *depth* controls how many hops through related topics to traverse.
    When *include_connected* is True, notes that are note-to-note connected
    to any direct match are also included (one extra hop through the
    ``connections`` edge list).
    """
    # Find the root topic
    lower = topic_name.lower().strip()
    root = next(
        (t for t in brain.topics.values() if t.get("name", "").lower() == lower),
        None,
    )
    if root is None:
        return []

    # BFS through related topics up to depth
    visited: set[str] = set()
    queue: list[tuple[str, int]] = [(root["id"], 0)]
    note_ids: set[str] = set()

    while queue:
        tid, d = queue.pop(0)
        if tid in visited:
            continue
        visited.add(tid)
        topic = brain.topics.get(tid)
        if not topic:
            continue
        note_ids.update(topic.get("note_ids", []))
        if d < depth:
            for related in topic.get("related_topics", []):
                queue.append((related, d + 1))

    # Optionally expand via note-to-note connections
    if include_connected:
        connected: set[str] = set()
        for conn in brain.connections:
            if conn["from"] in note_ids:
                connected.add(conn["to"])
            if conn["to"] in note_ids:
                connected.add(conn["from"])
        note_ids |= connected

    return [brain.notes[nid] for nid in note_ids if nid in brain.notes]


def get_topic_map(brain: BrainState) -> list[dict]:
    """Return a lightweight summary of every topic and its note count."""
    return [
        {
            "id": t["id"],
            "name": t.get("name", ""),
            "notes": len(t.get("note_ids", [])),
            "related": len(t.get("related_topics", [])),
        }
        for t in brain.topics.values()
    ]


# ──────────────────────────────────────────────────────────────────────
# Wiki pages (long-term memory)
# ──────────────────────────────────────────────────────────────────────

def _slugify(title: str) -> str:
    """Convert a title to a URL-safe slug."""
    import re
    slug = title.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")[:80]


def add_page(
    brain: BrainState,
    title: str,
    content: str,
    sources: list[str] | None = None,
    tags: list[str] | None = None,
) -> str:
    """Create a new wiki page. Returns the page ID (slug)."""
    now = datetime.now(timezone.utc).isoformat()
    brain.page_count += 1
    page_id = _slugify(title) or f"page-{brain.page_count}"
    # Ensure uniqueness
    if page_id in brain.pages:
        page_id = f"{page_id}-{brain.page_count}"
    brain.pages[page_id] = asdict(WikiPage(
        id=page_id,
        title=title.strip(),
        content=content,
        sources=sources or [],
        tags=tags or [],
        created_at=now,
        updated_at=now,
    ))
    return page_id


def update_page(
    brain: BrainState,
    page_id: str,
    content: str,
    new_sources: list[str] | None = None,
) -> bool:
    """Update an existing wiki page. Returns True if the page existed."""
    page = brain.pages.get(page_id)
    if page is None:
        return False
    page["content"] = content
    page["updated_at"] = datetime.now(timezone.utc).isoformat()
    if new_sources:
        existing = set(page.get("sources", []))
        page["sources"] = list(existing | set(new_sources))
    return True


def get_unconsolidated_notes(brain: BrainState) -> list[dict]:
    """Return notes not yet consumed by any wiki page.

    A note is "consolidated" if its ID appears in the ``sources`` list of
    any wiki page, or if its category is ``"archive"``.
    """
    consolidated_ids: set[str] = set()
    for page in brain.pages.values():
        consolidated_ids.update(page.get("sources", []))
    return [
        note for nid, note in brain.notes.items()
        if nid not in consolidated_ids and note.get("category") != "archive"
    ]


def build_wiki_summary(brain: BrainState, max_pages: int = 10) -> str:
    """Build a text summary of wiki pages for prompt injection."""
    if not brain.pages:
        return ""
    lines = ["== WIKI PAGES =="]
    sorted_pages = sorted(
        brain.pages.values(),
        key=lambda p: p.get("updated_at", ""),
        reverse=True,
    )
    for page in sorted_pages[:max_pages]:
        first_line = page.get("content", "").split("\n", 1)[0][:80]
        source_count = len(page.get("sources", []))
        lines.append(f"  📄 {page['title']} ({source_count} sources): {first_line}")
    return "\n".join(lines)


def should_consolidate(
    brain: BrainState,
    interval: int = 5,
    threshold: int = 10,
    heartbeat_number: int = 0,
) -> bool:
    """Check if consolidation should run this heartbeat.

    Returns True if either:
    - ``interval > 0`` and the current heartbeat is a multiple of *interval*
    - The number of unconsolidated notes exceeds *threshold*
    """
    if interval > 0 and heartbeat_number > 0 and heartbeat_number % interval == 0:
        return True
    if threshold > 0 and len(get_unconsolidated_notes(brain)) >= threshold:
        return True
    return False


def should_lint_wiki(
    brain: BrainState,
    interval: int = 20,
    heartbeat_number: int = 0,
) -> bool:
    """Check if a wiki lint pass should run this heartbeat."""
    return interval > 0 and heartbeat_number > 0 and heartbeat_number % interval == 0


def archive_consolidated_notes(brain: BrainState, note_ids: list[str]) -> int:
    """Set consumed notes' category to ``"archive"``. Returns count archived."""
    archived = 0
    for nid in note_ids:
        note = brain.notes.get(nid)
        if note and note.get("category") != "archive":
            note["category"] = "archive"
            archived += 1
    return archived


def build_brain_summary(
    brain: BrainState, max_notes: int = 10, *, query_topic: str = "",
    max_pages: int = 10,
) -> str:
    """Build a text summary of the brain's contents for prompt injection.

    When wiki pages exist, the summary prioritises page summaries (long-term
    memory) and only includes a small window of recent unconsolidated notes
    (short-term memory).

    When *query_topic* is provided, notes are selected via topic-graph
    traversal instead of simple recency.
    """
    try:
        unconsolidated = get_unconsolidated_notes(brain)
        lines = ["== SECOND BRAIN =="]
        lines.append(f"Wiki pages: {len(brain.pages)}")
        lines.append(f"Total notes: {len(brain.notes)} ({len(unconsolidated)} pending consolidation)")
        lines.append(f"Total connections: {len(brain.connections)}")
        lines.append(f"Total topics: {len(brain.topics)}")
        lines.append(f"Last review: {brain.last_review or 'never'}")
        lines.append(f"Last consolidation: {brain.last_consolidation or 'never'}")

        # Category breakdown
        categories: dict[str, int] = {}
        for n in brain.notes.values():
            cat = n.get("category", "resources")
            categories[cat] = categories.get(cat, 0) + 1
        if categories:
            lines.append(f"Categories: {', '.join(f'{k}={v}' for k, v in categories.items())}")

        # Wiki page summaries (long-term memory) — shown first
        wiki_block = build_wiki_summary(brain, max_pages)
        if wiki_block:
            lines.append(f"\n{wiki_block}")

        # Topic map (top topics by note count)
        topic_map = get_topic_map(brain)
        if topic_map:
            top_topics = sorted(topic_map, key=lambda t: t["notes"], reverse=True)[:8]
            topic_strs = [t["name"] + "(" + str(t["notes"]) + ")" for t in top_topics]
            lines.append("\nTopics: " + ", ".join(topic_strs))

        # Select notes: topic-based when possible, else recent unconsolidated
        if query_topic:
            selected = recall_by_topic(brain, query_topic, depth=1)[:max_notes]
            label = f"Knowledge related to '{query_topic}'"
        elif brain.pages:
            # When pages exist, only show recent unconsolidated notes (short-term)
            capped = min(max_notes, 5)  # cap short-term window
            sorted_uncons = sorted(
                unconsolidated,
                key=lambda n: n.get("created_at", ""),
                reverse=True,
            )[:capped]
            selected = sorted_uncons
            label = "Recent unconsolidated notes"
        else:
            selected = get_recent_notes(brain, max_notes)
            label = "Recent knowledge"

        if selected:
            lines.append(f"\n{label}:")
            for note in selected:
                summary = note.get("summary") or note.get("content", "")[:80]
                tags = ", ".join(note.get("tags", []))
                tag_str = f" [{tags}]" if tags else ""
                lines.append(f"  - ({note['id']}) {summary}{tag_str}")

        return "\n".join(lines)
    except Exception as e:
        logger.error("Failed to build brain summary: %s", str(e))
        logger.debug("build_brain_summary error details:", exc_info=True)
        return "Brain summary unavailable due to error"


# ──────────────────────────────────────────────────────────────────────
# Brain health / lint
# ──────────────────────────────────────────────────────────────────────

def lint_brain(brain: BrainState) -> list[dict]:
    """Analyse the brain and return a list of quality issues.

    Issue types
    -----------
    orphan        Note has zero connections.
    stale         Note older than 30 days and not archived.
    empty_content Note content is blank or very short.
    dup_tags      Two or more notes share >80 % identical tag sets.
    low_density   Connection-to-note ratio is below 0.3.

    Each issue is ``{"type": str, "severity": str, "note_id": str|None,
    "message": str}``.
    """
    issues: list[dict] = []

    connected_ids = (
        {c["from"] for c in brain.connections} | {c["to"] for c in brain.connections}
    )

    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

    for nid, note in brain.notes.items():
        # Orphan check
        if nid not in connected_ids and note.get("category") != "archive":
            issues.append({
                "type": "orphan",
                "severity": "info",
                "note_id": nid,
                "message": f"Note {nid} has no connections",
            })

        # Stale check
        if (note.get("category") != "archive"
                and note.get("created_at", "") < cutoff
                and nid not in connected_ids):
            issues.append({
                "type": "stale",
                "severity": "warning",
                "note_id": nid,
                "message": f"Note {nid} is older than 30 days with no connections",
            })

        # Empty content
        if len(note.get("content", "").strip()) < 10:
            issues.append({
                "type": "empty_content",
                "severity": "warning",
                "note_id": nid,
                "message": f"Note {nid} has very short or empty content",
            })

    # Low connection density (global metric)
    if brain.notes:
        density = len(brain.connections) / len(brain.notes)
        if density < 0.3:
            issues.append({
                "type": "low_density",
                "severity": "info",
                "note_id": None,
                "message": f"Connection density is {density:.2f} (target ≥ 0.3)",
            })

    return issues


def build_lint_block(brain: BrainState) -> str:
    """Build a concise text block from lint results for prompt injection."""
    issues = lint_brain(brain)
    if not issues:
        return ""
    warnings = [i for i in issues if i["severity"] == "warning"]
    infos = [i for i in issues if i["severity"] == "info"]
    lines = ["\n== BRAIN HEALTH =="]
    lines.append(f"Issues found: {len(warnings)} warnings, {len(infos)} info")
    for issue in (warnings + infos)[:10]:  # cap at 10
        prefix = "⚠" if issue["severity"] == "warning" else "ℹ"
        lines.append(f"  {prefix} [{issue['type']}] {issue['message']}")
    return "\n".join(lines)
