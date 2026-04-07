from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class Note:
    """An atomic note in the second brain."""

    id: str
    content: str
    summary: str = ""
    source: str = "agent"
    tags: list[str] = field(default_factory=list)
    category: str = "resources"  # projects | areas | resources | archive
    connections: list[str] = field(default_factory=list)  # IDs of related notes
    created_at: str = ""
    updated_at: str = ""


@dataclass
class BrainState:
    """Persistent state for the second brain knowledge store."""

    notes: dict[str, dict] = field(default_factory=dict)  # id -> Note as dict
    connections: list[dict] = field(default_factory=list)  # [{from, to, reason}]
    review_log: list[dict] = field(default_factory=list)  # [{timestamp, summary}]
    capture_count: int = 0
    last_review: str | None = None


def _brain_path(state_file: str) -> str:
    """Derive brain state path from the main state file path."""
    parent = os.path.dirname(state_file) or "data"
    return os.path.join(parent, "brain.json")


def load_brain(state_file: str) -> BrainState:
    """Load brain state from disk."""
    path = _brain_path(state_file)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return BrainState(**{
            k: v for k, v in data.items() if k in BrainState.__dataclass_fields__
        })
    return BrainState()


def save_brain(brain: BrainState, state_file: str, max_reviews: int = 50) -> None:
    """Save brain state atomically."""
    path = _brain_path(state_file)
    if len(brain.review_log) > max_reviews:
        brain.review_log = brain.review_log[-max_reviews:]

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    dir_name = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(asdict(brain), f, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        os.unlink(tmp_path)
        raise


def add_note(
    brain: BrainState,
    content: str,
    *,
    summary: str = "",
    source: str = "agent",
    tags: list[str] | None = None,
    category: str = "resources",
) -> str:
    """Add an atomic note to the brain. Returns the note ID."""
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


def connect_notes(
    brain: BrainState, from_id: str, to_id: str, reason: str = ""
) -> None:
    """Create a bidirectional connection between two notes."""
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


def get_notes_by_category(brain: BrainState, category: str) -> list[dict]:
    """Return notes filtered by PARA category."""
    return [n for n in brain.notes.values() if n.get("category") == category]


def get_recent_notes(brain: BrainState, count: int = 10) -> list[dict]:
    """Return the most recently added notes."""
    all_notes = sorted(
        brain.notes.values(), key=lambda n: n.get("created_at", ""), reverse=True
    )
    return all_notes[:count]


def build_brain_summary(brain: BrainState, max_notes: int = 10) -> str:
    """Build a text summary of the brain's contents for prompt injection."""
    lines = ["== SECOND BRAIN =="]
    lines.append(f"Total notes: {len(brain.notes)}")
    lines.append(f"Total connections: {len(brain.connections)}")
    lines.append(f"Last review: {brain.last_review or 'never'}")

    # Category breakdown
    categories: dict[str, int] = {}
    for n in brain.notes.values():
        cat = n.get("category", "resources")
        categories[cat] = categories.get(cat, 0) + 1
    if categories:
        lines.append(f"Categories: {', '.join(f'{k}={v}' for k, v in categories.items())}")

    # Recent notes
    recent = get_recent_notes(brain, max_notes)
    if recent:
        lines.append("\nRecent knowledge:")
        for note in recent:
            summary = note.get("summary") or note.get("content", "")[:80]
            tags = ", ".join(note.get("tags", []))
            tag_str = f" [{tags}]" if tags else ""
            lines.append(f"  - ({note['id']}) {summary}{tag_str}")

    return "\n".join(lines)
