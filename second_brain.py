from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import copy

logger = logging.getLogger(__name__)


def _normalize_confidence(confidence: Any) -> int | None:
    """Coerce confidence into the inclusive range 0-100."""
    try:
        if confidence in (None, ""):
            return None
        value = int(confidence)
    except (TypeError, ValueError):
        return None
    return max(0, min(100, value))


def _normalize_evidence(evidence: Any) -> list[str]:
    """Return a deduplicated list of non-empty evidence strings."""
    if evidence is None:
        return []
    if isinstance(evidence, list):
        candidates = evidence
    else:
        candidates = [evidence]

    cleaned: list[str] = []
    for item in candidates:
        text = str(item).strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _normalize_counter(value: Any) -> int:
    """Coerce numeric counters to non-negative integers."""
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _normalize_string_list(values: Any) -> list[str]:
    """Return a deduplicated list of non-empty strings."""
    if values is None:
        return []
    if isinstance(values, list):
        candidates = values
    else:
        candidates = [values]

    cleaned: list[str] = []
    for item in candidates:
        text = str(item).strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _normalize_event_log(entries: Any, *, limit: int = 12) -> list[dict]:
    """Keep only well-formed event log entries."""
    if not isinstance(entries, list):
        return []
    normalized = [entry for entry in entries if isinstance(entry, dict)]
    return normalized[-limit:]


def _normalize_note_dict(note: dict, note_id: str | None = None) -> dict:
    """Ensure note metadata fields exist and have consistent types."""
    if note_id:
        note["id"] = note_id
    note["note_type"] = (note.get("note_type") or "general").strip() or "general"
    note["status"] = (note.get("status") or "active").strip() or "active"
    note["confidence"] = _normalize_confidence(note.get("confidence"))
    note["evidence"] = _normalize_evidence(note.get("evidence"))
    note["tags"] = _normalize_string_list(note.get("tags"))
    note["connections"] = _normalize_string_list(note.get("connections"))
    note["last_accessed_at"] = str(note.get("last_accessed_at") or "")
    note["last_confirmed_at"] = str(note.get("last_confirmed_at") or "")
    note["recall_count"] = _normalize_counter(note.get("recall_count"))
    note["positive_feedback"] = _normalize_counter(note.get("positive_feedback"))
    note["negative_feedback"] = _normalize_counter(note.get("negative_feedback"))
    note["contradiction_count"] = _normalize_counter(note.get("contradiction_count"))
    note["contradicted_by"] = _normalize_string_list(note.get("contradicted_by"))
    note["feedback_log"] = _normalize_event_log(note.get("feedback_log"))
    note["contradiction_log"] = _normalize_event_log(note.get("contradiction_log"))
    return note


def _append_limited_log(note: dict, key: str, entry: dict, *, limit: int = 12) -> None:
    """Append an event entry while keeping note logs bounded."""
    log_entries = _normalize_event_log(note.get(key), limit=limit)
    log_entries.append(entry)
    note[key] = log_entries[-limit:]


def _note_label(note: dict, limit: int = 80) -> str:
    """Return a short human-readable label for a note."""
    return (note.get("summary") or note.get("content", "")[:limit]).strip()


@dataclass
class Note:
    """An atomic note in the second brain."""

    id: str
    content: str
    summary: str = ""
    source: Any = "agent"  # str or structured dict with provenance
    note_type: str = "general"
    status: str = "active"
    confidence: int | None = None
    evidence: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    category: str = "resources"  # projects | areas | resources | archive
    connections: list[str] = field(default_factory=list)  # IDs of related notes
    created_at: str = ""
    updated_at: str = ""
    last_accessed_at: str = ""
    last_confirmed_at: str = ""
    recall_count: int = 0
    positive_feedback: int = 0
    negative_feedback: int = 0
    contradiction_count: int = 0
    contradicted_by: list[str] = field(default_factory=list)
    feedback_log: list[dict] = field(default_factory=list)
    contradiction_log: list[dict] = field(default_factory=list)


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
    last_dream: str | None = None
    last_dream_heartbeat: int = 0
    dream_log: list[dict] = field(default_factory=list)


def _brain_path(state_file: str) -> str:
    """Derive brain state path from the main state file path."""
    parent = os.path.dirname(state_file) or "data"
    return os.path.join(parent, "brain.json")


def _brain_db_path(state_file: str) -> str:
    """Derive the SQLite brain store path from the main state file path."""
    parent = os.path.dirname(state_file) or "data"
    return os.path.join(parent, "brain.db")


_CREATE_META_TABLE = """\
CREATE TABLE IF NOT EXISTS brain_meta (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);
"""

_CREATE_NOTES_TABLE = """\
CREATE TABLE IF NOT EXISTS brain_notes (
    id              TEXT PRIMARY KEY,
    summary         TEXT NOT NULL DEFAULT '',
    category        TEXT NOT NULL DEFAULT 'resources',
    note_type       TEXT NOT NULL DEFAULT 'general',
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TEXT NOT NULL DEFAULT '',
    updated_at      TEXT NOT NULL DEFAULT '',
    confidence      INTEGER,
    recall_count    INTEGER NOT NULL DEFAULT 0,
    last_accessed_at TEXT NOT NULL DEFAULT '',
    content         TEXT NOT NULL DEFAULT '',
    raw_json        TEXT NOT NULL
);
"""

_CREATE_TOPICS_TABLE = """\
CREATE TABLE IF NOT EXISTS brain_topics (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    raw_json   TEXT NOT NULL
);
"""

_CREATE_PAGES_TABLE = """\
CREATE TABLE IF NOT EXISTS brain_pages (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    raw_json   TEXT NOT NULL
);
"""

_CREATE_CONNECTIONS_TABLE = """\
CREATE TABLE IF NOT EXISTS brain_connections (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id    TEXT NOT NULL DEFAULT '',
    to_id      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    raw_json   TEXT NOT NULL
);
"""

_CREATE_REVIEW_LOG_TABLE = """\
CREATE TABLE IF NOT EXISTS brain_review_log (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp  TEXT NOT NULL DEFAULT '',
    summary    TEXT NOT NULL DEFAULT '',
    raw_json   TEXT NOT NULL
);
"""

_CREATE_LINT_LOG_TABLE = """\
CREATE TABLE IF NOT EXISTS brain_lint_log (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp  TEXT NOT NULL DEFAULT '',
    raw_json   TEXT NOT NULL
);
"""

_CREATE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_brain_notes_updated ON brain_notes (updated_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_brain_notes_category ON brain_notes (category);",
    "CREATE INDEX IF NOT EXISTS idx_brain_topics_name ON brain_topics (name COLLATE NOCASE);",
    "CREATE INDEX IF NOT EXISTS idx_brain_pages_updated ON brain_pages (updated_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_brain_connections_from_to ON brain_connections (from_id, to_id);",
)

# Indexes on v2 columns — created after migration
_CREATE_V2_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_brain_notes_status ON brain_notes (status);",
    "CREATE INDEX IF NOT EXISTS idx_brain_notes_created ON brain_notes (created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_brain_notes_confidence ON brain_notes (confidence DESC);",
    "CREATE INDEX IF NOT EXISTS idx_brain_notes_recall ON brain_notes (recall_count DESC);",
)

# Current schema version — bump when schema changes
_SCHEMA_VERSION = 2


def _get_schema_version(conn: sqlite3.Connection) -> int:
    """Read the schema version from brain_meta, defaulting to 1."""
    try:
        row = conn.execute(
            "SELECT value_json FROM brain_meta WHERE key = 'schema_version'"
        ).fetchone()
        if row:
            return int(json.loads(row[0]))
    except (sqlite3.OperationalError, ValueError, TypeError):
        pass
    return 1


def _set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    """Write the schema version to brain_meta."""
    conn.execute(
        "INSERT OR REPLACE INTO brain_meta (key, value_json) VALUES ('schema_version', ?)",
        (json.dumps(version),),
    )


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Add queryable columns and FTS5 table (v1 → v2).

    Safe to run on a fresh DB (columns already exist via CREATE TABLE).
    """
    # Add queryable columns to brain_notes if missing (legacy DBs only)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(brain_notes)").fetchall()}
    new_cols = [
        ("confidence", "INTEGER"),
        ("recall_count", "INTEGER NOT NULL DEFAULT 0"),
        ("last_accessed_at", "TEXT NOT NULL DEFAULT ''"),
        ("content", "TEXT NOT NULL DEFAULT ''"),
    ]
    for col, col_type in new_cols:
        if col not in existing:
            conn.execute(f"ALTER TABLE brain_notes ADD COLUMN {col} {col_type}")

    # Backfill new columns from raw_json
    for note_id, raw_json in conn.execute("SELECT id, raw_json FROM brain_notes").fetchall():
        try:
            note = json.loads(raw_json)
        except (json.JSONDecodeError, TypeError):
            continue
        conn.execute(
            "UPDATE brain_notes SET confidence = ?, recall_count = ?, "
            "last_accessed_at = ?, content = ? WHERE id = ?",
            (
                _normalize_confidence(note.get("confidence")),
                _normalize_counter(note.get("recall_count")),
                str(note.get("last_accessed_at") or ""),
                str(note.get("content") or ""),
                note_id,
            ),
        )

    # Create FTS5 virtual table for full-text search on notes
    try:
        conn.execute("DROP TABLE IF EXISTS brain_notes_fts")
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS brain_notes_fts "
            "USING fts5(id UNINDEXED, content, summary, content=brain_notes, content_rowid=rowid)"
        )
        # Populate FTS from existing data
        conn.execute(
            "INSERT INTO brain_notes_fts(brain_notes_fts) VALUES('rebuild')"
        )
    except sqlite3.OperationalError:
        # FTS5 not available in this SQLite build — skip gracefully
        logger.info("FTS5 not available; full-text search will use fallback")

    _set_schema_version(conn, 2)
    conn.commit()


def _ensure_brain_db(db_path: str) -> sqlite3.Connection:
    """Open or create the SQLite brain store and ensure schema exists."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(_CREATE_META_TABLE)
    conn.execute(_CREATE_NOTES_TABLE)
    conn.execute(_CREATE_TOPICS_TABLE)
    conn.execute(_CREATE_PAGES_TABLE)
    conn.execute(_CREATE_CONNECTIONS_TABLE)
    conn.execute(_CREATE_REVIEW_LOG_TABLE)
    conn.execute(_CREATE_LINT_LOG_TABLE)
    for stmt in _CREATE_INDEXES:
        conn.execute(stmt)
    conn.commit()

    # Run migrations
    version = _get_schema_version(conn)
    if version < 2:
        _migrate_v1_to_v2(conn)

    # Create v2 indexes (safe after migration has added the columns)
    for stmt in _CREATE_V2_INDEXES:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column might not exist in degraded state
    conn.commit()

    return conn


def _infer_counter(value: Any, keys: list[str] | tuple[str, ...], prefix: str) -> int:
    """Keep counters in sync with the highest observed ID for a collection."""
    max_seen = 0
    for key in keys:
        if isinstance(key, str) and key.startswith(prefix):
            suffix = key[len(prefix):]
            if suffix.isdigit():
                max_seen = max(max_seen, int(suffix))
    try:
        current = int(value or 0)
    except (TypeError, ValueError):
        current = 0
    return max(current, max_seen)


def _brain_state_from_data(data: dict) -> BrainState:
    """Build a BrainState from persisted top-level data."""
    filtered = {k: v for k, v in data.items() if k in BrainState.__dataclass_fields__}
    brain = BrainState(**filtered)
    brain.capture_count = _infer_counter(brain.capture_count, tuple(brain.notes.keys()), "n")
    brain.topic_count = _infer_counter(brain.topic_count, tuple(brain.topics.keys()), "t")
    brain.page_count = max(int(brain.page_count or 0), len(brain.pages))
    return brain


def _read_brain(path: str) -> dict:
    """Read brain JSON from disk (runs in executor)."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _read_brain_sqlite(db_path: str) -> dict:
    """Read the brain from SQLite into the legacy top-level dict shape."""
    conn = _ensure_brain_db(db_path)
    try:
        meta = {
            key: json.loads(value_json)
            for key, value_json in conn.execute(
                "SELECT key, value_json FROM brain_meta"
            ).fetchall()
        }

        notes = {}
        for note_id, raw_json in conn.execute(
            "SELECT id, raw_json FROM brain_notes ORDER BY id"
        ).fetchall():
            note = json.loads(raw_json)
            if isinstance(note, dict):
                note.setdefault("id", note_id)
                notes[note_id] = note

        topics = {}
        for topic_id, raw_json in conn.execute(
            "SELECT id, raw_json FROM brain_topics ORDER BY id"
        ).fetchall():
            topic = json.loads(raw_json)
            if isinstance(topic, dict):
                topic.setdefault("id", topic_id)
                topics[topic_id] = topic

        pages = {}
        for page_id, raw_json in conn.execute(
            "SELECT id, raw_json FROM brain_pages ORDER BY id"
        ).fetchall():
            page = json.loads(raw_json)
            if isinstance(page, dict):
                page.setdefault("id", page_id)
                pages[page_id] = page

        connections = [
            json.loads(raw_json)
            for (raw_json,) in conn.execute(
                "SELECT raw_json FROM brain_connections ORDER BY seq"
            ).fetchall()
        ]
        review_log = [
            json.loads(raw_json)
            for (raw_json,) in conn.execute(
                "SELECT raw_json FROM brain_review_log ORDER BY seq"
            ).fetchall()
        ]
        lint_log = [
            json.loads(raw_json)
            for (raw_json,) in conn.execute(
                "SELECT raw_json FROM brain_lint_log ORDER BY seq"
            ).fetchall()
        ]
    finally:
        conn.close()

    return {
        "notes": notes,
        "topics": topics,
        "connections": connections,
        "pages": pages,
        "review_log": review_log,
        "lint_log": lint_log,
        "capture_count": meta.get("capture_count", 0),
        "topic_count": meta.get("topic_count", 0),
        "page_count": meta.get("page_count", 0),
        "last_review": meta.get("last_review"),
        "last_consolidation": meta.get("last_consolidation"),
        "last_lint": meta.get("last_lint"),
        "last_dream": meta.get("last_dream"),
        "last_dream_heartbeat": _normalize_counter(meta.get("last_dream_heartbeat")),
        "dream_log": _normalize_event_log(meta.get("dream_log"), limit=50),
    }


async def load_brain(state_file: str) -> BrainState:
    """Load brain state from disk.

    Transient I/O errors (``OSError``) are **not** caught here so that the
    caller's retry decorator can handle them.  Corrupt-data errors
    (``JSONDecodeError``, ``UnicodeDecodeError``) are non-retryable and
    return a fresh ``BrainState`` instead.
    """
    path = _brain_path(state_file)
    db_path = _brain_db_path(state_file)
    if not os.path.exists(path) and not os.path.exists(db_path):
        return BrainState()
    try:
        loop = asyncio.get_event_loop()
        if os.path.exists(db_path):
            try:
                data = await loop.run_in_executor(None, _read_brain_sqlite, db_path)
            except sqlite3.OperationalError as e:
                raise OSError(str(e)) from e
            except (sqlite3.DatabaseError, ValueError, UnicodeDecodeError) as e:
                logger.error("Corrupt brain database %s: %s", db_path, str(e))
                if not os.path.exists(path):
                    return BrainState()
                data = await loop.run_in_executor(None, _read_brain, path)
                try:
                    await loop.run_in_executor(None, _write_brain_sqlite, data, db_path)
                except sqlite3.OperationalError as db_write_err:
                    raise OSError(str(db_write_err)) from db_write_err

            # SQLite exists but is empty — backfill from JSON if it has data
            if not data.get("notes") and os.path.exists(path):
                json_data = await loop.run_in_executor(None, _read_brain, path)
                if json_data.get("notes"):
                    logger.info(
                        "SQLite brain empty but %s has %d note(s) — backfilling",
                        path, len(json_data["notes"]),
                    )
                    data = json_data
                    try:
                        await loop.run_in_executor(None, _write_brain_sqlite, data, db_path)
                    except sqlite3.OperationalError as e:
                        raise OSError(str(e)) from e
        else:
            data = await loop.run_in_executor(None, _read_brain, path)
            try:
                await loop.run_in_executor(None, _write_brain_sqlite, data, db_path)
            except sqlite3.OperationalError as e:
                raise OSError(str(e)) from e
            logger.info("Migrated %s to SQLite brain store", path)

        return _brain_state_from_data(data)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
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


def _write_brain_sqlite(brain_dict: dict, db_path: str) -> None:
    """Persist the brain dict to SQLite as the primary store."""
    try:
        conn = _ensure_brain_db(db_path)
    except sqlite3.DatabaseError:
        if os.path.exists(db_path):
            os.remove(db_path)
        conn = _ensure_brain_db(db_path)

    try:
        with conn:
            conn.execute("DELETE FROM brain_meta")
            conn.execute("DELETE FROM brain_notes")
            conn.execute("DELETE FROM brain_topics")
            conn.execute("DELETE FROM brain_pages")
            conn.execute("DELETE FROM brain_connections")
            conn.execute("DELETE FROM brain_review_log")
            conn.execute("DELETE FROM brain_lint_log")

            conn.executemany(
                "INSERT INTO brain_meta (key, value_json) VALUES (?, ?)",
                [
                    ("schema_version", json.dumps(_SCHEMA_VERSION)),
                    ("capture_count", json.dumps(brain_dict.get("capture_count", 0))),
                    ("topic_count", json.dumps(brain_dict.get("topic_count", 0))),
                    ("page_count", json.dumps(brain_dict.get("page_count", 0))),
                    ("last_review", json.dumps(brain_dict.get("last_review"))),
                    ("last_consolidation", json.dumps(brain_dict.get("last_consolidation"))),
                    ("last_lint", json.dumps(brain_dict.get("last_lint"))),
                    ("last_dream", json.dumps(brain_dict.get("last_dream"))),
                    ("last_dream_heartbeat", json.dumps(_normalize_counter(brain_dict.get("last_dream_heartbeat")))),
                    ("dream_log", json.dumps(_normalize_event_log(brain_dict.get("dream_log"), limit=50))),
                ],
            )

            conn.executemany(
                "INSERT INTO brain_notes "
                "(id, summary, category, note_type, status, created_at, updated_at, "
                "confidence, recall_count, last_accessed_at, content, raw_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        note_id,
                        note.get("summary", ""),
                        note.get("category", "resources"),
                        note.get("note_type", "general"),
                        note.get("status", "active"),
                        note.get("created_at", ""),
                        note.get("updated_at", ""),
                        _normalize_confidence(note.get("confidence")),
                        _normalize_counter(note.get("recall_count")),
                        str(note.get("last_accessed_at") or ""),
                        str(note.get("content") or ""),
                        json.dumps(note),
                    )
                    for note_id, note in brain_dict.get("notes", {}).items()
                ],
            )

            conn.executemany(
                "INSERT INTO brain_topics (id, name, created_at, raw_json) VALUES (?, ?, ?, ?)",
                [
                    (
                        topic_id,
                        topic.get("name", ""),
                        topic.get("created_at", ""),
                        json.dumps(topic),
                    )
                    for topic_id, topic in brain_dict.get("topics", {}).items()
                ],
            )

            conn.executemany(
                "INSERT INTO brain_pages (id, title, updated_at, raw_json) VALUES (?, ?, ?, ?)",
                [
                    (
                        page_id,
                        page.get("title", ""),
                        page.get("updated_at", ""),
                        json.dumps(page),
                    )
                    for page_id, page in brain_dict.get("pages", {}).items()
                ],
            )

            conn.executemany(
                "INSERT INTO brain_connections (from_id, to_id, created_at, raw_json) VALUES (?, ?, ?, ?)",
                [
                    (
                        conn_row.get("from", ""),
                        conn_row.get("to", ""),
                        conn_row.get("created_at", ""),
                        json.dumps(conn_row),
                    )
                    for conn_row in brain_dict.get("connections", [])
                ],
            )

            conn.executemany(
                "INSERT INTO brain_review_log (timestamp, summary, raw_json) VALUES (?, ?, ?)",
                [
                    (
                        entry.get("timestamp", ""),
                        entry.get("summary", ""),
                        json.dumps(entry),
                    )
                    for entry in brain_dict.get("review_log", [])
                ],
            )

            conn.executemany(
                "INSERT INTO brain_lint_log (timestamp, raw_json) VALUES (?, ?)",
                [
                    (
                        entry.get("timestamp", ""),
                        json.dumps(entry),
                    )
                    for entry in brain_dict.get("lint_log", [])
                ],
            )
    finally:
        conn.close()


def _update_note_row(conn: sqlite3.Connection, note_id: str, note: dict) -> None:
    """Persist one note row in SQLite after an in-place mutation."""
    conn.execute(
        "UPDATE brain_notes "
        "SET summary = ?, category = ?, note_type = ?, status = ?, created_at = ?, updated_at = ?, "
        "confidence = ?, recall_count = ?, last_accessed_at = ?, content = ?, raw_json = ? "
        "WHERE id = ?",
        (
            note.get("summary", ""),
            note.get("category", "resources"),
            note.get("note_type", "general"),
            note.get("status", "active"),
            note.get("created_at", ""),
            note.get("updated_at", ""),
            _normalize_confidence(note.get("confidence")),
            _normalize_counter(note.get("recall_count")),
            str(note.get("last_accessed_at") or ""),
            str(note.get("content") or ""),
            json.dumps(note),
            note_id,
        ),
    )


def _apply_note_updates_to_snapshot(snapshot_path: str, updated_notes: dict[str, dict]) -> None:
    """Patch note metadata in the JSON compatibility snapshot."""
    if not updated_notes or not os.path.exists(snapshot_path):
        return
    try:
        data = _read_brain(snapshot_path)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        logger.warning("Failed to update brain snapshot after note mutation", exc_info=True)
        return

    notes = data.get("notes")
    if not isinstance(notes, dict):
        return

    changed = False
    for note_id, note in updated_notes.items():
        if note_id in notes:
            notes[note_id] = note
            changed = True

    if changed:
        _write_brain(data, snapshot_path)


def _mutate_notes_in_store(
    state_file: str,
    note_ids: list[str],
    mutator,
    *,
    sync_snapshot: bool = False,
) -> int:
    """Apply a note mutation directly to the persisted brain store."""
    ordered_ids = [note_id for note_id in dict.fromkeys(note_ids) if note_id]
    if not ordered_ids:
        return 0

    db_path = _brain_db_path(state_file)
    snapshot_path = _brain_path(state_file)

    if not os.path.exists(db_path):
        brain = _load_brain_for_queries(state_file)
        updated_notes: dict[str, dict] = {}
        for note_id in ordered_ids:
            note = brain.notes.get(note_id)
            if note is None:
                continue
            _normalize_note_dict(note, note_id)
            if mutator(note):
                updated_notes[note_id] = note
        if updated_notes:
            brain_dict = asdict(brain)
            _write_brain_sqlite(brain_dict, db_path)
            _write_brain(brain_dict, snapshot_path)
        return len(updated_notes)

    try:
        updated_notes: dict[str, dict] = {}
        conn = _ensure_brain_db(db_path)
        try:
            placeholders = ",".join("?" for _ in ordered_ids)
            rows = conn.execute(
                f"SELECT id, raw_json FROM brain_notes WHERE id IN ({placeholders})",
                tuple(ordered_ids),
            ).fetchall()
            with conn:
                for note_id, raw_json in rows:
                    note = json.loads(raw_json)
                    if not isinstance(note, dict):
                        continue
                    _normalize_note_dict(note, note_id)
                    if not mutator(note):
                        continue
                    _update_note_row(conn, note_id, note)
                    updated_notes[note_id] = note
        finally:
            conn.close()
    except (sqlite3.DatabaseError, ValueError, UnicodeDecodeError):
        logger.warning("Failed to mutate SQLite notes; falling back to full brain rewrite", exc_info=True)
        brain = _load_brain_for_queries(state_file)
        updated_notes = {}
        for note_id in ordered_ids:
            note = brain.notes.get(note_id)
            if note is None:
                continue
            _normalize_note_dict(note, note_id)
            if mutator(note):
                updated_notes[note_id] = note
        if updated_notes:
            brain_dict = asdict(brain)
            _write_brain_sqlite(brain_dict, db_path)
            if sync_snapshot:
                _write_brain(brain_dict, snapshot_path)
        return len(updated_notes)

    if updated_notes and sync_snapshot:
        _apply_note_updates_to_snapshot(snapshot_path, updated_notes)
    return len(updated_notes)


async def save_brain(brain: BrainState, state_file: str, max_reviews: int = 50) -> None:
    """Save brain state atomically.

    Uses incremental UPSERT for the SQLite store when available,
    falling back to full rewrite on error.  Always writes the JSON
    snapshot for backward compatibility.
    """
    path = _brain_path(state_file)
    db_path = _brain_db_path(state_file)
    if len(brain.review_log) > max_reviews:
        brain.review_log = brain.review_log[-max_reviews:]

    loop = asyncio.get_event_loop()
    brain_dict = asdict(brain)
    try:
        await loop.run_in_executor(None, _write_brain_incremental, brain_dict, db_path)
    except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
        logger.warning("Incremental save failed, falling back to full rewrite: %s", e)
        try:
            await loop.run_in_executor(None, _write_brain_sqlite, brain_dict, db_path)
        except sqlite3.OperationalError as e2:
            raise OSError(str(e2)) from e2
    await loop.run_in_executor(None, _write_brain, brain_dict, path)


def _write_brain_incremental(brain_dict: dict, db_path: str) -> None:
    """Persist the brain dict to SQLite using UPSERT for notes/topics/pages.

    Instead of DELETE all + re-INSERT, this:
    1. UPSERTs notes, topics, pages (only changed rows touch disk)
    2. Replaces connections and logs (these are append-only and small)
    3. UPSERTs meta counters
    """
    try:
        conn = _ensure_brain_db(db_path)
    except sqlite3.DatabaseError:
        try:
            if os.path.exists(db_path):
                os.remove(db_path)
        except OSError:
            pass
        conn = _ensure_brain_db(db_path)

    try:
        with conn:
            # Meta — UPSERT
            meta_rows = [
                ("schema_version", json.dumps(_SCHEMA_VERSION)),
                ("capture_count", json.dumps(brain_dict.get("capture_count", 0))),
                ("topic_count", json.dumps(brain_dict.get("topic_count", 0))),
                ("page_count", json.dumps(brain_dict.get("page_count", 0))),
                ("last_review", json.dumps(brain_dict.get("last_review"))),
                ("last_consolidation", json.dumps(brain_dict.get("last_consolidation"))),
                ("last_lint", json.dumps(brain_dict.get("last_lint"))),
                ("last_dream", json.dumps(brain_dict.get("last_dream"))),
                ("last_dream_heartbeat", json.dumps(_normalize_counter(brain_dict.get("last_dream_heartbeat")))),
                ("dream_log", json.dumps(_normalize_event_log(brain_dict.get("dream_log"), limit=50))),
            ]
            conn.executemany(
                "INSERT OR REPLACE INTO brain_meta (key, value_json) VALUES (?, ?)",
                meta_rows,
            )

            # Notes — UPSERT
            note_ids_in_brain = set(brain_dict.get("notes", {}).keys())
            conn.executemany(
                "INSERT OR REPLACE INTO brain_notes "
                "(id, summary, category, note_type, status, created_at, updated_at, "
                "confidence, recall_count, last_accessed_at, content, raw_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        note_id,
                        note.get("summary", ""),
                        note.get("category", "resources"),
                        note.get("note_type", "general"),
                        note.get("status", "active"),
                        note.get("created_at", ""),
                        note.get("updated_at", ""),
                        _normalize_confidence(note.get("confidence")),
                        _normalize_counter(note.get("recall_count")),
                        str(note.get("last_accessed_at") or ""),
                        str(note.get("content") or ""),
                        json.dumps(note),
                    )
                    for note_id, note in brain_dict.get("notes", {}).items()
                ],
            )
            # Remove notes that were deleted from the in-memory brain
            existing_ids = {
                row[0] for row in conn.execute("SELECT id FROM brain_notes").fetchall()
            }
            removed = existing_ids - note_ids_in_brain
            if removed:
                ph = ",".join("?" for _ in removed)
                conn.execute(f"DELETE FROM brain_notes WHERE id IN ({ph})", tuple(removed))

            # Topics — UPSERT
            topic_ids_in_brain = set(brain_dict.get("topics", {}).keys())
            conn.executemany(
                "INSERT OR REPLACE INTO brain_topics (id, name, created_at, raw_json) VALUES (?, ?, ?, ?)",
                [
                    (
                        topic_id,
                        topic.get("name", ""),
                        topic.get("created_at", ""),
                        json.dumps(topic),
                    )
                    for topic_id, topic in brain_dict.get("topics", {}).items()
                ],
            )
            existing_topic_ids = {
                row[0] for row in conn.execute("SELECT id FROM brain_topics").fetchall()
            }
            removed_topics = existing_topic_ids - topic_ids_in_brain
            if removed_topics:
                ph = ",".join("?" for _ in removed_topics)
                conn.execute(f"DELETE FROM brain_topics WHERE id IN ({ph})", tuple(removed_topics))

            # Pages — UPSERT
            page_ids_in_brain = set(brain_dict.get("pages", {}).keys())
            conn.executemany(
                "INSERT OR REPLACE INTO brain_pages (id, title, updated_at, raw_json) VALUES (?, ?, ?, ?)",
                [
                    (
                        page_id,
                        page.get("title", ""),
                        page.get("updated_at", ""),
                        json.dumps(page),
                    )
                    for page_id, page in brain_dict.get("pages", {}).items()
                ],
            )
            existing_page_ids = {
                row[0] for row in conn.execute("SELECT id FROM brain_pages").fetchall()
            }
            removed_pages = existing_page_ids - page_ids_in_brain
            if removed_pages:
                ph = ",".join("?" for _ in removed_pages)
                conn.execute(f"DELETE FROM brain_pages WHERE id IN ({ph})", tuple(removed_pages))

            # Connections — replace (append-only, small)
            conn.execute("DELETE FROM brain_connections")
            conn.executemany(
                "INSERT INTO brain_connections (from_id, to_id, created_at, raw_json) VALUES (?, ?, ?, ?)",
                [
                    (
                        conn_row.get("from", ""),
                        conn_row.get("to", ""),
                        conn_row.get("created_at", ""),
                        json.dumps(conn_row),
                    )
                    for conn_row in brain_dict.get("connections", [])
                ],
            )

            # Review/lint logs — replace (bounded, small)
            conn.execute("DELETE FROM brain_review_log")
            conn.executemany(
                "INSERT INTO brain_review_log (timestamp, summary, raw_json) VALUES (?, ?, ?)",
                [
                    (
                        entry.get("timestamp", ""),
                        entry.get("summary", ""),
                        json.dumps(entry),
                    )
                    for entry in brain_dict.get("review_log", [])
                ],
            )
            conn.execute("DELETE FROM brain_lint_log")
            conn.executemany(
                "INSERT INTO brain_lint_log (timestamp, raw_json) VALUES (?, ?)",
                [
                    (
                        entry.get("timestamp", ""),
                        json.dumps(entry),
                    )
                    for entry in brain_dict.get("lint_log", [])
                ],
            )

            # Rebuild FTS index if it exists
            if _has_fts(conn):
                try:
                    conn.execute(
                        "INSERT INTO brain_notes_fts(brain_notes_fts) VALUES('rebuild')"
                    )
                except sqlite3.OperationalError:
                    pass
    finally:
        conn.close()


def add_note(
    brain: BrainState,
    content: str,
    *,
    summary: str = "",
    source: str | dict = "agent",
    note_type: str = "general",
    status: str = "active",
    confidence: int | None = None,
    evidence: list[str] | None = None,
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
            note_type=(note_type or "general").strip() or "general",
            status=(status or "active").strip() or "active",
            confidence=_normalize_confidence(confidence),
            evidence=_normalize_evidence(evidence),
            tags=tags or [],
            category=category,
            created_at=now,
            updated_at=now,
            last_accessed_at=now,
        ))
        # Index for semantic search (no-op if deps not installed)
        try:
            from brain_index import index_note
            index_note(note_id, brain.notes[note_id])
        except Exception:
            pass
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


def record_note_access(
    brain: BrainState,
    note_ids: list[str],
    *,
    accessed_at: str | None = None,
) -> int:
    """Record that notes were surfaced to a user during retrieval."""
    stamp = accessed_at or datetime.now(timezone.utc).isoformat()
    updated = 0
    for note_id in dict.fromkeys(note_ids):
        note = brain.notes.get(note_id)
        if note is None:
            continue
        _normalize_note_dict(note, note_id)
        note["last_accessed_at"] = stamp
        note["recall_count"] = _normalize_counter(note.get("recall_count")) + 1
        updated += 1
    return updated


def record_note_access_in_store(
    state_file: str,
    note_ids: list[str],
    *,
    accessed_at: str | None = None,
) -> int:
    """Persist access signals without rewriting the full brain snapshot."""
    stamp = accessed_at or datetime.now(timezone.utc).isoformat()

    def _mutator(note: dict) -> bool:
        note["last_accessed_at"] = stamp
        note["recall_count"] = _normalize_counter(note.get("recall_count")) + 1
        return True

    return _mutate_notes_in_store(state_file, note_ids, _mutator, sync_snapshot=False)


def apply_relevance_feedback(
    brain: BrainState,
    note_id: str,
    *,
    relevant: bool,
    reason: str = "",
    timestamp: str | None = None,
) -> bool:
    """Persist an explicit reinforcement or demotion signal for a note."""
    note = brain.notes.get(note_id)
    if note is None:
        return False

    stamp = timestamp or datetime.now(timezone.utc).isoformat()
    _normalize_note_dict(note, note_id)
    if relevant:
        note["positive_feedback"] = _normalize_counter(note.get("positive_feedback")) + 1
        note["last_confirmed_at"] = stamp
        note["confidence"] = min(100, (_normalize_confidence(note.get("confidence")) or 55) + 5)
        if note.get("status") == "tentative" and note["positive_feedback"] >= note["negative_feedback"]:
            note["status"] = "active"
    else:
        note["negative_feedback"] = _normalize_counter(note.get("negative_feedback")) + 1
        confidence = _normalize_confidence(note.get("confidence"))
        if confidence is not None:
            note["confidence"] = max(0, confidence - 10)
        if note.get("status") != "superseded" and note["negative_feedback"] >= note["positive_feedback"]:
            note["status"] = "tentative"

    note["updated_at"] = stamp
    _append_limited_log(
        note,
        "feedback_log",
        {
            "timestamp": stamp,
            "relevant": bool(relevant),
            "reason": reason.strip(),
        },
    )
    return True


def _note_strength(note: dict) -> float:
    """Estimate which note should win when memories conflict."""
    _normalize_note_dict(note)
    strength = float(_normalize_confidence(note.get("confidence")) or 50)
    strength += _normalize_counter(note.get("positive_feedback")) * 8.0
    strength -= _normalize_counter(note.get("negative_feedback")) * 6.0
    strength -= _normalize_counter(note.get("contradiction_count")) * 7.0
    strength += min(_normalize_counter(note.get("recall_count")), 10) * 0.5

    confirmed_at = _parse_iso(note.get("last_confirmed_at", ""))
    if confirmed_at is not None and datetime.now(timezone.utc) - confirmed_at.astimezone(timezone.utc) <= timedelta(days=30):
        strength += 6.0
    return strength


def _has_connection(brain: BrainState, from_id: str, to_id: str, *, reason_prefix: str = "") -> bool:
    """Return True when a matching connection already exists."""
    expected = {from_id, to_id}
    prefix = reason_prefix.lower().strip()
    for conn in brain.connections:
        pair = {conn.get("from", ""), conn.get("to", "")}
        if pair != expected:
            continue
        if not prefix:
            return True
        if str(conn.get("reason", "")).lower().startswith(prefix):
            return True
    return False


def record_contradiction(
    brain: BrainState,
    note_id: str,
    contradicting_note_id: str,
    *,
    reason: str = "",
    supersede: bool | None = None,
    timestamp: str | None = None,
) -> bool:
    """Mark one note as contradicted by another stronger or newer note."""
    if note_id == contradicting_note_id:
        return False
    note = brain.notes.get(note_id)
    contradicter = brain.notes.get(contradicting_note_id)
    if note is None or contradicter is None:
        return False

    stamp = timestamp or datetime.now(timezone.utc).isoformat()
    _normalize_note_dict(note, note_id)
    _normalize_note_dict(contradicter, contradicting_note_id)

    note["contradiction_count"] = _normalize_counter(note.get("contradiction_count")) + 1
    if contradicting_note_id not in note["contradicted_by"]:
        note["contradicted_by"].append(contradicting_note_id)
    note["updated_at"] = stamp
    _append_limited_log(
        note,
        "contradiction_log",
        {
            "timestamp": stamp,
            "by_note_id": contradicting_note_id,
            "reason": reason.strip(),
        },
    )

    contradicter["last_confirmed_at"] = stamp
    if contradicter.get("status") == "tentative" and contradicter["positive_feedback"] >= contradicter["negative_feedback"]:
        contradicter["status"] = "active"

    if supersede is None:
        supersede = _note_strength(contradicter) >= _note_strength(note)
    note["status"] = "superseded" if supersede else "tentative"

    relation_reason = f"contradiction: {reason.strip()}" if reason.strip() else "contradiction"
    if not _has_connection(brain, note_id, contradicting_note_id, reason_prefix="contradiction"):
        connect_notes(brain, note_id, contradicting_note_id, reason=relation_reason)
    return True


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


def _query_words(query: str) -> list[str]:
    """Tokenize a query into searchable words."""
    import re

    return [word for word in re.findall(r"[a-z0-9_/-]+", query.lower()) if len(word) >= 2]


def _parse_iso(ts: str) -> datetime | None:
    """Parse an ISO timestamp, tolerating invalid values."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def _recency_bonus(note: dict) -> float:
    """Return a small bonus for recent notes."""
    ts = (
        note.get("last_confirmed_at")
        or note.get("updated_at")
        or note.get("created_at")
        or ""
    )
    dt = _parse_iso(ts)
    if dt is None:
        return 0.0
    age = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
    if age <= timedelta(days=1):
        return 0.60
    if age <= timedelta(days=7):
        return 0.35
    if age <= timedelta(days=30):
        return 0.15
    return 0.0


def _access_frequency_bonus(note: dict) -> float:
    """Score based on how frequently a note is accessed relative to its age.

    A note recalled 8 times in 10 days is far more valuable than one
    recalled 8 times over 6 months.  This replaces the flat recall_count
    multiplier with an access-rate signal and a last-access recency curve.
    """
    recall_count = _normalize_counter(note.get("recall_count"))
    if recall_count == 0:
        return 0.0

    now = datetime.now(timezone.utc)

    # --- access rate (recalls per week, capped) ---
    created = _parse_iso(note.get("created_at", ""))
    if created is not None:
        age_days = max((now - created.astimezone(timezone.utc)).total_seconds() / 86400, 1.0)
        recalls_per_week = (recall_count / age_days) * 7.0
        # Diminishing returns: sqrt curve capped at ~0.55
        rate_score = min(recalls_per_week ** 0.5 * 0.25, 0.55)
    else:
        # Fallback: use raw count with diminishing returns
        rate_score = min(recall_count ** 0.5 * 0.12, 0.40)

    # --- last-access recency (smooth decay, not step-function) ---
    accessed_at = _parse_iso(note.get("last_accessed_at", ""))
    if accessed_at is not None:
        days_since = max((now - accessed_at.astimezone(timezone.utc)).total_seconds() / 86400, 0.0)
        # Exponential decay: half-life ~5 days, max 0.30
        recency_score = 0.30 * (0.5 ** (days_since / 5.0))
    else:
        recency_score = 0.0

    return rate_score + recency_score


def _usage_adjustment(note: dict) -> float:
    """Convert real retrieval and feedback signals into ranking adjustments."""
    positive_feedback = _normalize_counter(note.get("positive_feedback"))
    negative_feedback = _normalize_counter(note.get("negative_feedback"))
    contradiction_count = _normalize_counter(note.get("contradiction_count"))

    score = _access_frequency_bonus(note)
    score += min(positive_feedback, 4) * 0.30
    score -= min(negative_feedback, 4) * 0.45
    score -= min(contradiction_count, 4) * 0.55

    return score


def _staleness_penalty(note: dict) -> float:
    """Penalize old notes that have not been reconfirmed or reused."""
    ts = (
        note.get("last_confirmed_at")
        or note.get("updated_at")
        or note.get("created_at")
        or ""
    )
    dt = _parse_iso(ts)
    if dt is None:
        return 0.0

    age = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
    if age <= timedelta(days=30):
        return 0.0

    penalty = 0.0
    if age > timedelta(days=180):
        penalty += 0.60
    elif age > timedelta(days=90):
        penalty += 0.35
    else:
        penalty += 0.15

    if not note.get("last_confirmed_at") and _normalize_counter(note.get("positive_feedback")) == 0:
        penalty += 0.20
    if _normalize_counter(note.get("recall_count")) == 0 and (_normalize_confidence(note.get("confidence")) or 0) <= 50:
        penalty += 0.10
    return penalty


def _connection_count_map(connections: list[dict]) -> dict[str, int]:
    """Return a map of note_id -> direct connection count."""
    counts: dict[str, int] = {}
    for conn in connections:
        from_id = conn.get("from", "")
        to_id = conn.get("to", "")
        if from_id:
            counts[from_id] = counts.get(from_id, 0) + 1
        if to_id:
            counts[to_id] = counts.get(to_id, 0) + 1
    return counts


def _score_note(
    note: dict,
    query_lower: str,
    query_words: list[str],
    *,
    connection_count: int = 0,
) -> float:
    """Rank a note for retrieval using lexical, structural, and freshness signals."""
    content = (note.get("content") or "").lower()
    summary = (note.get("summary") or "").lower()
    tags_text = " ".join(note.get("tags") or []).lower()
    note_type = (note.get("note_type") or "").lower()
    status = (note.get("status") or "active").lower()

    lexical = 0.0
    if query_lower and query_lower in content:
        lexical += 3.5
    if query_lower and query_lower in summary:
        lexical += 2.5
    if query_lower and query_lower in tags_text:
        lexical += 1.5
    if query_lower and query_lower in note_type:
        lexical += 1.25
    if query_lower and query_lower in status:
        lexical += 0.25

    if query_words:
        content_hits = sum(1 for word in query_words if word in content)
        summary_hits = sum(1 for word in query_words if word in summary)
        tag_hits = sum(1 for word in query_words if word in tags_text)
        type_hits = sum(1 for word in query_words if word in note_type)
        lexical += (content_hits * 0.60) / len(query_words)
        lexical += (summary_hits * 0.80) / len(query_words)
        lexical += (tag_hits * 0.50) / len(query_words)
        lexical += (type_hits * 0.40) / len(query_words)

    if lexical <= 0:
        return 0.0

    score = lexical
    confidence = _normalize_confidence(note.get("confidence")) or 0
    score += (confidence / 100.0) * 0.75
    score += min(connection_count, 5) * 0.15
    score += _recency_bonus(note)
    score += _usage_adjustment(note)
    score -= _staleness_penalty(note)

    if status == "archive":
        score -= 0.75
    elif status == "superseded":
        score -= 1.10
    elif status == "tentative":
        score -= 0.55

    if note_type in {"decision", "preference", "pattern", "architecture"}:
        score += 0.10

    return score


def _rank_notes(notes: list[dict], connections: list[dict], query: str, *, max_results: int = 10) -> list[tuple[float, dict]]:
    """Return ranked note matches as (score, note) tuples."""
    query_lower = query.lower().strip()
    query_words = _query_words(query_lower)
    connection_counts = _connection_count_map(connections)
    scored: list[tuple[float, str, str, dict]] = []

    for note in notes:
        score = _score_note(
            note,
            query_lower,
            query_words,
            connection_count=connection_counts.get(note.get("id", ""), 0),
        )
        if score <= 0:
            continue
        recency = note.get("last_confirmed_at") or note.get("updated_at") or note.get("created_at", "")
        scored.append((score, recency, note.get("id", ""), note))

    scored.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return [(score, note) for score, _, _, note in scored[:max_results]]


def _rank_notes_hybrid(
    notes: list[dict],
    connections: list[dict],
    query: str,
    *,
    semantic_scores: dict[str, float],
    semantic_weight: float = 2.0,
    max_results: int = 10,
) -> list[dict]:
    """Rank notes using combined lexical + semantic signals.

    Unlike ``_rank_notes``, a note does **not** need a positive lexical
    score to be included — a positive semantic score alone is sufficient.
    """
    query_lower = query.lower().strip()
    query_words = _query_words(query_lower)
    connection_counts = _connection_count_map(connections)
    scored: list[tuple[float, str, str, dict]] = []

    for note in notes:
        note_id = note.get("id", "")
        lexical = _score_note(
            note,
            query_lower,
            query_words,
            connection_count=connection_counts.get(note_id, 0),
        )
        sem = semantic_scores.get(note_id, 0.0) * semantic_weight
        total = max(lexical, 0.0) + sem
        if total <= 0:
            continue
        recency = note.get("last_confirmed_at") or note.get("updated_at") or note.get("created_at", "")
        scored.append((total, recency, note_id, note))

    scored.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return [note for _, _, _, note in scored[:max_results]]


def search_notes(
    brain: BrainState,
    query: str,
    max_results: int = 10,
    *,
    record_access: bool = False,
) -> list[dict]:
    """Search notes using lexical, confidence, recency, and graph signals."""
    results = [
        note for _, note in _rank_notes(
            list(brain.notes.values()),
            brain.connections,
            query,
            max_results=max_results,
        )
    ]
    if record_access and results:
        record_note_access(brain, [note.get("id", "") for note in results])
    return results


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


def _dream_normalized_content(note: dict) -> str:
    """Return canonicalized note content for deterministic dedup checks."""
    return " ".join(str(note.get("content", "")).strip().lower().split())


def _dream_archive_exact_duplicates(brain: BrainState, *, min_chars: int = 24) -> int:
    """Archive exact duplicate notes while keeping the newest copy."""
    note_items = sorted(
        brain.notes.items(),
        key=lambda item: (item[1].get("created_at", ""), item[0]),
        reverse=True,
    )
    canonical_to_primary: dict[str, str] = {}
    archived = 0
    now = datetime.now(timezone.utc).isoformat()
    for note_id, note in note_items:
        _normalize_note_dict(note, note_id)
        if note.get("category") == "archive":
            continue
        canonical = _dream_normalized_content(note)
        if len(canonical) < min_chars:
            continue

        primary_id = canonical_to_primary.get(canonical)
        if primary_id is None:
            canonical_to_primary[canonical] = note_id
            continue

        note["category"] = "archive"
        if note.get("status", "active") in {"active", "confirmed", "tentative"}:
            note["status"] = "superseded"
        note["updated_at"] = now
        marker = f"dream deduped: duplicate of {primary_id}"
        evidence = _normalize_evidence(note.get("evidence"))
        if marker not in evidence:
            evidence.append(marker)
        note["evidence"] = evidence
        archived += 1
    return archived


def run_dream_cycle(
    brain: BrainState,
    *,
    heartbeat_number: int | None = None,
    apply: bool = True,
    max_age_days: int = 30,
    trigger: str = "manual",
) -> dict[str, Any]:
    """Run a deterministic sleep/dream maintenance cycle.

    Phases:
    - orient: capture baseline brain stats
    - gather: count unconsolidated candidate notes
    - consolidate: exact duplicate compaction
    - prune: stale orphan decay + lint snapshot
    """
    target = brain if apply else copy.deepcopy(brain)
    before = build_brain_stats(target)
    gathered = len(get_unconsolidated_notes(target))
    deduped = _dream_archive_exact_duplicates(target)
    decayed = decay_stale_notes(target, max_age_days=max_age_days)
    issues = lint_brain(target)
    now = datetime.now(timezone.utc).isoformat()
    target.last_dream = now
    if heartbeat_number is not None:
        target.last_dream_heartbeat = _normalize_counter(heartbeat_number)
    changed = deduped > 0 or decayed > 0
    if apply:
        target.dream_log.append(
            {
                "timestamp": now,
                "trigger": trigger,
                "heartbeat": target.last_dream_heartbeat,
                "changed": changed,
                "deduped": deduped,
                "stale_archived": decayed,
                "lint_issues": len(issues),
            }
        )
        target.dream_log = _normalize_event_log(target.dream_log, limit=50)
        target.lint_log.append(
            {
                "timestamp": now,
                "source": "dream",
                "issues": issues[:25],
            }
        )
    after = build_brain_stats(target)
    return {
        "applied": bool(apply),
        "changed": changed,
        "timestamp": now,
        "heartbeat": target.last_dream_heartbeat,
        "phases": {
            "orient": {"before_notes": before["notes"], "before_orphans": before["orphans"]},
            "gather": {"unconsolidated": gathered},
            "consolidate": {"exact_duplicates_archived": deduped},
            "prune": {"stale_archived": decayed, "lint_issues": len(issues)},
        },
        "before": before,
        "after": after,
    }


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


def build_brain_stats(brain: BrainState, *, topic_limit: int = 8) -> dict[str, Any]:
    """Return aggregate brain metrics for visibility and diagnostics."""
    categories: dict[str, int] = {}
    note_types: dict[str, int] = {}
    statuses: dict[str, int] = {}
    connected_ids = {c["from"] for c in brain.connections} | {c["to"] for c in brain.connections}
    orphan_count = 0
    recall_total = 0
    positive_feedback_total = 0
    negative_feedback_total = 0
    contradiction_total = 0
    frequently_accessed = 0
    never_accessed = 0

    for nid, note in brain.notes.items():
        _normalize_note_dict(note, nid)
        cat = note.get("category", "resources")
        categories[cat] = categories.get(cat, 0) + 1

        note_type = note.get("note_type", "general") or "general"
        note_types[note_type] = note_types.get(note_type, 0) + 1

        status = note.get("status", "active") or "active"
        statuses[status] = statuses.get(status, 0) + 1

        rc = _normalize_counter(note.get("recall_count"))
        recall_total += rc
        positive_feedback_total += _normalize_counter(note.get("positive_feedback"))
        negative_feedback_total += _normalize_counter(note.get("negative_feedback"))
        contradiction_total += _normalize_counter(note.get("contradiction_count"))

        if rc == 0:
            never_accessed += 1
        elif _access_frequency_bonus(note) >= 0.25:
            frequently_accessed += 1

        if note.get("category") != "archive" and nid not in connected_ids:
            orphan_count += 1

    connection_density = (len(brain.connections) / len(brain.notes)) if brain.notes else 0.0
    top_topics = sorted(
        get_topic_map(brain),
        key=lambda topic: (topic["notes"], topic["related"], topic["name"].lower()),
        reverse=True,
    )[:topic_limit]

    return {
        "notes": len(brain.notes),
        "pages": len(brain.pages),
        "topics": len(brain.topics),
        "connections": len(brain.connections),
        "reviews": len(brain.review_log),
        "unconsolidated": len(get_unconsolidated_notes(brain)),
        "orphans": orphan_count,
        "connection_density": connection_density,
        "last_review": brain.last_review or "never",
        "last_consolidation": brain.last_consolidation or "never",
        "last_dream": brain.last_dream or "never",
        "last_dream_heartbeat": _normalize_counter(brain.last_dream_heartbeat),
        "dream_runs_recent": list(reversed(_normalize_event_log(brain.dream_log, limit=10))),
        "categories": categories,
        "note_types": note_types,
        "statuses": statuses,
        "recalls": recall_total,
        "frequently_accessed": frequently_accessed,
        "never_accessed": never_accessed,
        "positive_feedback": positive_feedback_total,
        "negative_feedback": negative_feedback_total,
        "contradictions": contradiction_total,
        "top_topics": top_topics,
    }


def get_note_topics(brain: BrainState, note_id: str) -> list[str]:
    """Return topic names associated with a note."""
    names = {
        topic.get("name", "")
        for topic in brain.topics.values()
        if note_id in topic.get("note_ids", [])
    }
    return sorted([name for name in names if name], key=str.lower)


def _connected_note_ids(brain: BrainState, note_id: str) -> set[str]:
    """Return all note IDs directly connected to *note_id*."""
    ids = set(brain.notes.get(note_id, {}).get("connections", []))
    for conn in brain.connections:
        if conn.get("from") == note_id:
            ids.add(conn.get("to", ""))
        if conn.get("to") == note_id:
            ids.add(conn.get("from", ""))
    ids.discard(note_id)
    ids.discard("")
    return ids


def describe_note(
    brain: BrainState,
    note_id: str,
    *,
    connected_limit: int = 5,
    record_access: bool = False,
) -> dict[str, Any] | None:
    """Return a rich description of a note for CLI inspection."""
    note = brain.notes.get(note_id)
    if note is None:
        return None
    _normalize_note_dict(note, note_id)
    if record_access:
        record_note_access(brain, [note_id])

    connected_ids = sorted(
        _connected_note_ids(brain, note_id),
        key=lambda nid: (
            brain.notes.get(nid, {}).get("updated_at")
            or brain.notes.get(nid, {}).get("created_at", ""),
            nid,
        ),
        reverse=True,
    )
    connected_notes = []
    for cid in connected_ids[:connected_limit]:
        other = brain.notes.get(cid)
        if other is None:
            continue
        connected_notes.append({
            "id": cid,
            "summary": _note_label(other),
            "category": other.get("category", "resources"),
            "note_type": other.get("note_type", "general"),
        })

    source_pages = sorted(
        page.get("title") or page.get("id", "")
        for page in brain.pages.values()
        if note_id in page.get("sources", [])
    )

    return {
        "id": note_id,
        "summary": _note_label(note),
        "content": note.get("content", ""),
        "category": note.get("category", "resources"),
        "note_type": note.get("note_type", "general"),
        "status": note.get("status", "active"),
        "confidence": _normalize_confidence(note.get("confidence")),
        "tags": list(note.get("tags", [])),
        "topics": get_note_topics(brain, note_id),
        "evidence": _normalize_evidence(note.get("evidence")),
        "source": note.get("source", "agent"),
        "created_at": note.get("created_at", ""),
        "updated_at": note.get("updated_at", ""),
        "last_accessed_at": note.get("last_accessed_at", ""),
        "last_confirmed_at": note.get("last_confirmed_at", ""),
        "recall_count": _normalize_counter(note.get("recall_count")),
        "positive_feedback": _normalize_counter(note.get("positive_feedback")),
        "negative_feedback": _normalize_counter(note.get("negative_feedback")),
        "contradiction_count": _normalize_counter(note.get("contradiction_count")),
        "contradicted_by": _normalize_string_list(note.get("contradicted_by")),
        "feedback_log": _normalize_event_log(note.get("feedback_log")),
        "contradiction_log": _normalize_event_log(note.get("contradiction_log")),
        "connected_notes": connected_notes,
        "source_pages": source_pages,
    }


def trace_topic(
    brain: BrainState,
    topic_name: str,
    *,
    depth: int = 1,
    limit: int = 10,
    include_connected: bool = True,
    record_access: bool = False,
) -> dict[str, Any] | None:
    """Return a structured topic trace for CLI inspection."""
    lower = topic_name.lower().strip()
    root = next(
        (t for t in brain.topics.values() if t.get("name", "").lower() == lower),
        None,
    )
    if root is None:
        return None

    visited: set[str] = set()
    depths: dict[str, int] = {}
    queue: list[tuple[str, int]] = [(root["id"], 0)]
    note_ids: set[str] = set()

    while queue:
        tid, current_depth = queue.pop(0)
        if tid in visited:
            continue
        visited.add(tid)
        depths[tid] = current_depth
        topic = brain.topics.get(tid)
        if not topic:
            continue
        note_ids.update(topic.get("note_ids", []))
        if current_depth < depth:
            for related in topic.get("related_topics", []):
                queue.append((related, current_depth + 1))

    if include_connected:
        expanded: set[str] = set()
        for conn in brain.connections:
            if conn.get("from") in note_ids:
                expanded.add(conn.get("to", ""))
            if conn.get("to") in note_ids:
                expanded.add(conn.get("from", ""))
        note_ids |= {nid for nid in expanded if nid}

    topics = []
    for tid in visited:
        topic = brain.topics.get(tid)
        if topic is None:
            continue
        topics.append({
            "id": tid,
            "name": topic.get("name", ""),
            "depth": depths.get(tid, 0),
            "notes": len(topic.get("note_ids", [])),
            "related": len(topic.get("related_topics", [])),
        })
    topics.sort(key=lambda item: (item["depth"], -item["notes"], item["name"].lower()))

    notes = []
    for nid in note_ids:
        note = brain.notes.get(nid)
        if note is None:
            continue
        notes.append({
            "id": nid,
            "summary": _note_label(note),
            "category": note.get("category", "resources"),
            "note_type": note.get("note_type", "general"),
            "tags": list(note.get("tags", [])),
            "sort_key": note.get("updated_at") or note.get("created_at", ""),
        })
    notes.sort(key=lambda item: (item["sort_key"], item["id"]), reverse=True)
    if record_access and notes[:limit]:
        record_note_access(brain, [note["id"] for note in notes[:limit]])

    return {
        "topic": root.get("name", topic_name),
        "depth": depth,
        "topics": topics,
        "total_notes": len(notes),
        "notes": [
            {k: v for k, v in note.items() if k != "sort_key"}
            for note in notes[:limit]
        ],
    }


def _load_brain_for_queries(state_file: str) -> BrainState:
    """Synchronously load the best available brain representation for read-only queries."""
    db_path = _brain_db_path(state_file)
    if os.path.exists(db_path):
        try:
            return _brain_state_from_data(_read_brain_sqlite(db_path))
        except (sqlite3.DatabaseError, ValueError, UnicodeDecodeError):
            logger.warning("Falling back to JSON snapshot for brain queries", exc_info=True)

    path = _brain_path(state_file)
    if os.path.exists(path):
        try:
            return _brain_state_from_data(_read_brain(path))
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            logger.warning("Failed to load brain snapshot for queries", exc_info=True)

    return BrainState()


def build_brain_stats_from_store(state_file: str, *, topic_limit: int = 8) -> dict[str, Any]:
    """Build brain stats directly from SQLite when available."""
    db_path = _brain_db_path(state_file)
    if not os.path.exists(db_path):
        return build_brain_stats(_load_brain_for_queries(state_file), topic_limit=topic_limit)

    try:
        conn = _ensure_brain_db(db_path)
        try:
            meta = {
                key: json.loads(value_json)
                for key, value_json in conn.execute(
                    "SELECT key, value_json FROM brain_meta"
                ).fetchall()
            }
            notes_count = conn.execute("SELECT COUNT(*) FROM brain_notes").fetchone()[0]
            pages_count = conn.execute("SELECT COUNT(*) FROM brain_pages").fetchone()[0]
            topics_count = conn.execute("SELECT COUNT(*) FROM brain_topics").fetchone()[0]
            connections_count = conn.execute("SELECT COUNT(*) FROM brain_connections").fetchone()[0]
            reviews_count = conn.execute("SELECT COUNT(*) FROM brain_review_log").fetchone()[0]
            categories = dict(conn.execute("SELECT category, COUNT(*) FROM brain_notes GROUP BY category").fetchall())
            note_types = dict(conn.execute("SELECT note_type, COUNT(*) FROM brain_notes GROUP BY note_type").fetchall())
            statuses = dict(conn.execute("SELECT status, COUNT(*) FROM brain_notes GROUP BY status").fetchall())
            note_rows = conn.execute("SELECT id, category FROM brain_notes").fetchall()
            connected_ids: set[str] = set()
            recall_total = 0
            positive_feedback_total = 0
            negative_feedback_total = 0
            contradiction_total = 0
            frequently_accessed = 0
            never_accessed = 0
            for from_id, to_id in conn.execute("SELECT from_id, to_id FROM brain_connections").fetchall():
                if from_id:
                    connected_ids.add(from_id)
                if to_id:
                    connected_ids.add(to_id)
            orphan_count = sum(
                1 for note_id, category in note_rows
                if category != "archive" and note_id not in connected_ids
            )

            consolidated_ids: set[str] = set()
            for (raw_json,) in conn.execute("SELECT raw_json FROM brain_pages").fetchall():
                page = json.loads(raw_json)
                if isinstance(page, dict):
                    consolidated_ids.update(page.get("sources", []))

            unconsolidated = sum(
                1 for note_id, category in note_rows
                if category != "archive" and note_id not in consolidated_ids
            )

            for note_id, raw_json in conn.execute("SELECT id, raw_json FROM brain_notes").fetchall():
                note = json.loads(raw_json)
                if not isinstance(note, dict):
                    continue
                _normalize_note_dict(note, note_id)
                rc = _normalize_counter(note.get("recall_count"))
                recall_total += rc
                positive_feedback_total += _normalize_counter(note.get("positive_feedback"))
                negative_feedback_total += _normalize_counter(note.get("negative_feedback"))
                contradiction_total += _normalize_counter(note.get("contradiction_count"))
                if rc == 0:
                    never_accessed += 1
                elif _access_frequency_bonus(note) >= 0.25:
                    frequently_accessed += 1

            top_topics = []
            for topic_id, name, raw_json in conn.execute(
                "SELECT id, name, raw_json FROM brain_topics ORDER BY id"
            ).fetchall():
                topic = json.loads(raw_json)
                if not isinstance(topic, dict):
                    continue
                top_topics.append({
                    "id": topic_id,
                    "name": name or topic.get("name", ""),
                    "notes": len(topic.get("note_ids", [])),
                    "related": len(topic.get("related_topics", [])),
                })
        finally:
            conn.close()
    except (sqlite3.DatabaseError, ValueError, UnicodeDecodeError):
        logger.warning("Failed to query SQLite brain stats; falling back to in-memory path", exc_info=True)
        return build_brain_stats(_load_brain_for_queries(state_file), topic_limit=topic_limit)

    top_topics = sorted(
        top_topics,
        key=lambda topic: (topic["notes"], topic["related"], topic["name"].lower()),
        reverse=True,
    )[:topic_limit]

    return {
        "notes": notes_count,
        "pages": pages_count,
        "topics": topics_count,
        "connections": connections_count,
        "reviews": reviews_count,
        "unconsolidated": unconsolidated,
        "orphans": orphan_count,
        "connection_density": (connections_count / notes_count) if notes_count else 0.0,
        "last_review": meta.get("last_review") or "never",
        "last_consolidation": meta.get("last_consolidation") or "never",
        "last_dream": meta.get("last_dream") or "never",
        "last_dream_heartbeat": _normalize_counter(meta.get("last_dream_heartbeat")),
        "dream_runs_recent": list(reversed(_normalize_event_log(meta.get("dream_log"), limit=10))),
        "categories": categories,
        "note_types": note_types,
        "statuses": statuses,
        "recalls": recall_total,
        "frequently_accessed": frequently_accessed,
        "never_accessed": never_accessed,
        "positive_feedback": positive_feedback_total,
        "negative_feedback": negative_feedback_total,
        "contradictions": contradiction_total,
        "top_topics": top_topics,
    }


def search_notes_from_store(
    state_file: str,
    query: str,
    *,
    max_results: int = 10,
    record_access: bool = False,
    semantic: bool = True,
) -> list[dict]:
    """Search notes directly from SQLite when available.

    When *semantic* is True and vector dependencies are installed,
    uses embedding similarity to pre-filter candidates before
    applying the standard lexical/recency/connection re-ranking.
    """
    db_path = _brain_db_path(state_file)
    if not os.path.exists(db_path):
        brain = _load_brain_for_queries(state_file)
        results = search_notes(brain, query, max_results=max_results, record_access=record_access)
        if record_access and results:
            brain_dict = asdict(brain)
            _write_brain_sqlite(brain_dict, db_path)
        return results

    # --- Semantic pre-filtering ---
    semantic_hits: dict[str, float] = {}
    if semantic:
        try:
            from brain_index import semantic_search as _sem_search
            raw_hits = _sem_search(query, k=max_results * 3)
            semantic_hits = dict(raw_hits)
        except Exception:
            pass  # graceful fallback to lexical-only

    try:
        conn = _ensure_brain_db(db_path)
        try:
            if semantic_hits:
                # Fast path: load only candidate notes
                candidate_ids = list(semantic_hits.keys())
                placeholders = ",".join("?" for _ in candidate_ids)
                notes = []
                for note_id, raw_json in conn.execute(
                    f"SELECT id, raw_json FROM brain_notes WHERE id IN ({placeholders})",
                    candidate_ids,
                ).fetchall():
                    note = json.loads(raw_json)
                    if isinstance(note, dict):
                        _normalize_note_dict(note, note_id)
                        notes.append(note)
                connections = [
                    {"from": from_id, "to": to_id}
                    for from_id, to_id in conn.execute(
                        "SELECT from_id, to_id FROM brain_connections"
                    ).fetchall()
                ]
            else:
                # Full-scan path: load all notes
                notes = []
                for note_id, raw_json in conn.execute(
                    "SELECT id, raw_json FROM brain_notes ORDER BY id"
                ).fetchall():
                    note = json.loads(raw_json)
                    if isinstance(note, dict):
                        _normalize_note_dict(note, note_id)
                        notes.append(note)
                connections = [
                    {"from": from_id, "to": to_id}
                    for from_id, to_id in conn.execute(
                        "SELECT from_id, to_id FROM brain_connections"
                    ).fetchall()
                ]
        finally:
            conn.close()
    except (sqlite3.DatabaseError, ValueError, UnicodeDecodeError):
        logger.warning("Failed to query SQLite note search; falling back to in-memory path", exc_info=True)
        brain = _load_brain_for_queries(state_file)
        results = search_notes(brain, query, max_results=max_results, record_access=record_access)
        if record_access and results:
            brain_dict = asdict(brain)
            _write_brain_sqlite(brain_dict, db_path)
            _write_brain(brain_dict, _brain_path(state_file))
        return results

    if semantic_hits:
        results = _rank_notes_hybrid(
            notes, connections, query,
            semantic_scores=semantic_hits,
            max_results=max_results,
        )
    else:
        results = [note for _, note in _rank_notes(notes, connections, query, max_results=max_results)]

    if record_access and results:
        stamp = datetime.now(timezone.utc).isoformat()
        for note in results:
            note["last_accessed_at"] = stamp
            note["recall_count"] = _normalize_counter(note.get("recall_count")) + 1
        record_note_access_in_store(
            state_file,
            [note.get("id", "") for note in results],
            accessed_at=stamp,
        )
    return results


def get_topic_map_from_store(state_file: str) -> list[dict]:
    """Return the topic map directly from SQLite when available."""
    db_path = _brain_db_path(state_file)
    if not os.path.exists(db_path):
        return get_topic_map(_load_brain_for_queries(state_file))

    try:
        conn = _ensure_brain_db(db_path)
        try:
            topics = []
            for topic_id, name, raw_json in conn.execute(
                "SELECT id, name, raw_json FROM brain_topics ORDER BY id"
            ).fetchall():
                topic = json.loads(raw_json)
                if not isinstance(topic, dict):
                    continue
                topics.append({
                    "id": topic_id,
                    "name": name or topic.get("name", ""),
                    "notes": len(topic.get("note_ids", [])),
                    "related": len(topic.get("related_topics", [])),
                })
        finally:
            conn.close()
        return topics
    except (sqlite3.DatabaseError, ValueError, UnicodeDecodeError):
        logger.warning("Failed to query SQLite topics; falling back to in-memory path", exc_info=True)
        return get_topic_map(_load_brain_for_queries(state_file))


def describe_note_from_store(
    state_file: str,
    note_id: str,
    *,
    connected_limit: int = 5,
    record_access: bool = False,
) -> dict[str, Any] | None:
    """Describe a note directly from SQLite when available."""
    db_path = _brain_db_path(state_file)
    if not os.path.exists(db_path):
        brain = _load_brain_for_queries(state_file)
        details = describe_note(
            brain,
            note_id,
            connected_limit=connected_limit,
            record_access=record_access,
        )
        if record_access and details is not None:
            brain_dict = asdict(brain)
            _write_brain_sqlite(brain_dict, db_path)
            _write_brain(brain_dict, _brain_path(state_file))
        return details

    try:
        conn = _ensure_brain_db(db_path)
        try:
            row = conn.execute(
                "SELECT raw_json FROM brain_notes WHERE id = ?",
                (note_id,),
            ).fetchone()
            if not row:
                return None
            note = json.loads(row[0])
            if not isinstance(note, dict):
                return None
            _normalize_note_dict(note, note_id)

            connected_ids = set(note.get("connections", []))
            for from_id, to_id in conn.execute(
                "SELECT from_id, to_id FROM brain_connections WHERE from_id = ? OR to_id = ?",
                (note_id, note_id),
            ).fetchall():
                if from_id == note_id and to_id:
                    connected_ids.add(to_id)
                if to_id == note_id and from_id:
                    connected_ids.add(from_id)
            connected_ids.discard(note_id)

            topics = []
            for name, raw_json in conn.execute(
                "SELECT name, raw_json FROM brain_topics"
            ).fetchall():
                topic = json.loads(raw_json)
                if isinstance(topic, dict) and note_id in topic.get("note_ids", []):
                    topics.append(name or topic.get("name", ""))

            source_pages = []
            for page_id, title, raw_json in conn.execute(
                "SELECT id, title, raw_json FROM brain_pages"
            ).fetchall():
                page = json.loads(raw_json)
                if isinstance(page, dict) and note_id in page.get("sources", []):
                    source_pages.append(title or page_id)

            connected_notes = []
            if connected_ids:
                placeholders = ",".join("?" for _ in connected_ids)
                rows = conn.execute(
                    f"SELECT id, raw_json FROM brain_notes WHERE id IN ({placeholders})",
                    tuple(sorted(connected_ids)),
                ).fetchall()
                others = []
                for connected_id, raw_json in rows:
                    other = json.loads(raw_json)
                    if isinstance(other, dict):
                        _normalize_note_dict(other, connected_id)
                        others.append(other)
                others.sort(
                    key=lambda other: (
                        other.get("updated_at") or other.get("created_at", ""),
                        other.get("id", ""),
                    ),
                    reverse=True,
                )
                for other in others[:connected_limit]:
                    connected_notes.append({
                        "id": other.get("id", ""),
                        "summary": _note_label(other),
                        "category": other.get("category", "resources"),
                        "note_type": other.get("note_type", "general"),
                    })
        finally:
            conn.close()
    except (sqlite3.DatabaseError, ValueError, UnicodeDecodeError):
        logger.warning("Failed to query SQLite note detail; falling back to in-memory path", exc_info=True)
        brain = _load_brain_for_queries(state_file)
        details = describe_note(
            brain,
            note_id,
            connected_limit=connected_limit,
            record_access=record_access,
        )
        if record_access and details is not None:
            brain_dict = asdict(brain)
            _write_brain_sqlite(brain_dict, db_path)
            _write_brain(brain_dict, _brain_path(state_file))
        return details

    if record_access:
        stamp = datetime.now(timezone.utc).isoformat()
        note["last_accessed_at"] = stamp
        note["recall_count"] = _normalize_counter(note.get("recall_count")) + 1
        record_note_access_in_store(state_file, [note_id], accessed_at=stamp)

    return {
        "id": note_id,
        "summary": _note_label(note),
        "content": note.get("content", ""),
        "category": note.get("category", "resources"),
        "note_type": note.get("note_type", "general"),
        "status": note.get("status", "active"),
        "confidence": _normalize_confidence(note.get("confidence")),
        "tags": list(note.get("tags", [])),
        "topics": sorted([topic for topic in topics if topic], key=str.lower),
        "evidence": _normalize_evidence(note.get("evidence")),
        "source": note.get("source", "agent"),
        "created_at": note.get("created_at", ""),
        "updated_at": note.get("updated_at", ""),
        "last_accessed_at": note.get("last_accessed_at", ""),
        "last_confirmed_at": note.get("last_confirmed_at", ""),
        "recall_count": _normalize_counter(note.get("recall_count")),
        "positive_feedback": _normalize_counter(note.get("positive_feedback")),
        "negative_feedback": _normalize_counter(note.get("negative_feedback")),
        "contradiction_count": _normalize_counter(note.get("contradiction_count")),
        "contradicted_by": _normalize_string_list(note.get("contradicted_by")),
        "feedback_log": _normalize_event_log(note.get("feedback_log")),
        "contradiction_log": _normalize_event_log(note.get("contradiction_log")),
        "connected_notes": connected_notes,
        "source_pages": sorted(source_pages, key=str.lower),
    }


def trace_topic_from_store(
    state_file: str,
    topic_name: str,
    *,
    depth: int = 1,
    limit: int = 10,
    include_connected: bool = True,
    record_access: bool = False,
) -> dict[str, Any] | None:
    """Trace a topic directly from SQLite when available."""
    db_path = _brain_db_path(state_file)
    if not os.path.exists(db_path):
        brain = _load_brain_for_queries(state_file)
        trace = trace_topic(
            brain,
            topic_name,
            depth=depth,
            limit=limit,
            include_connected=include_connected,
            record_access=record_access,
        )
        if record_access and trace is not None:
            brain_dict = asdict(brain)
            _write_brain_sqlite(brain_dict, db_path)
            _write_brain(brain_dict, _brain_path(state_file))
        return trace

    try:
        conn = _ensure_brain_db(db_path)
        try:
            topics_by_id: dict[str, dict] = {}
            for topic_id, raw_json in conn.execute(
                "SELECT id, raw_json FROM brain_topics ORDER BY id"
            ).fetchall():
                topic = json.loads(raw_json)
                if isinstance(topic, dict):
                    topic.setdefault("id", topic_id)
                    topics_by_id[topic_id] = topic

            lower = topic_name.lower().strip()
            root = next(
                (topic for topic in topics_by_id.values() if topic.get("name", "").lower() == lower),
                None,
            )
            if root is None:
                return None

            visited: set[str] = set()
            depths: dict[str, int] = {}
            queue: list[tuple[str, int]] = [(root["id"], 0)]
            note_ids: set[str] = set()

            while queue:
                topic_id, current_depth = queue.pop(0)
                if topic_id in visited:
                    continue
                visited.add(topic_id)
                depths[topic_id] = current_depth
                topic = topics_by_id.get(topic_id)
                if topic is None:
                    continue
                note_ids.update(topic.get("note_ids", []))
                if current_depth < depth:
                    for related in topic.get("related_topics", []):
                        queue.append((related, current_depth + 1))

            if include_connected:
                expanded: set[str] = set()
                for from_id, to_id in conn.execute(
                    "SELECT from_id, to_id FROM brain_connections"
                ).fetchall():
                    if from_id in note_ids and to_id:
                        expanded.add(to_id)
                    if to_id in note_ids and from_id:
                        expanded.add(from_id)
                note_ids |= expanded

            topics = []
            for topic_id in visited:
                topic = topics_by_id.get(topic_id)
                if topic is None:
                    continue
                topics.append({
                    "id": topic_id,
                    "name": topic.get("name", ""),
                    "depth": depths.get(topic_id, 0),
                    "notes": len(topic.get("note_ids", [])),
                    "related": len(topic.get("related_topics", [])),
                })
            topics.sort(key=lambda item: (item["depth"], -item["notes"], item["name"].lower()))

            notes = []
            if note_ids:
                placeholders = ",".join("?" for _ in note_ids)
                rows = conn.execute(
                    f"SELECT id, raw_json FROM brain_notes WHERE id IN ({placeholders})",
                    tuple(sorted(note_ids)),
                ).fetchall()
                for note_id, raw_json in rows:
                    note = json.loads(raw_json)
                    if not isinstance(note, dict):
                        continue
                    _normalize_note_dict(note, note_id)
                    notes.append({
                        "id": note_id,
                        "summary": _note_label(note),
                        "category": note.get("category", "resources"),
                        "note_type": note.get("note_type", "general"),
                        "tags": list(note.get("tags", [])),
                        "sort_key": note.get("updated_at") or note.get("created_at", ""),
                    })
        finally:
            conn.close()
    except (sqlite3.DatabaseError, ValueError, UnicodeDecodeError):
        logger.warning("Failed to query SQLite topic trace; falling back to in-memory path", exc_info=True)
        brain = _load_brain_for_queries(state_file)
        trace = trace_topic(
            brain,
            topic_name,
            depth=depth,
            limit=limit,
            include_connected=include_connected,
            record_access=record_access,
        )
        if record_access and trace is not None:
            brain_dict = asdict(brain)
            _write_brain_sqlite(brain_dict, db_path)
            _write_brain(brain_dict, _brain_path(state_file))
        return trace

    notes.sort(key=lambda item: (item["sort_key"], item["id"]), reverse=True)
    if record_access and notes[:limit]:
        record_note_access_in_store(
            state_file,
            [note["id"] for note in notes[:limit]],
        )
    return {
        "topic": root.get("name", topic_name),
        "depth": depth,
        "topics": topics,
        "total_notes": len(notes),
        "notes": [
            {k: v for k, v in note.items() if k != "sort_key"}
            for note in notes[:limit]
        ],
    }


# ──────────────────────────────────────────────────────────────────────
# Phase 3: Store-backed retrieval (avoids full in-memory rebuild)
# ──────────────────────────────────────────────────────────────────────


def _has_fts(conn: sqlite3.Connection) -> bool:
    """Check whether the FTS5 virtual table exists."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='brain_notes_fts'"
    ).fetchone()
    return row is not None


def get_recent_notes_from_store(
    state_file: str, count: int = 10,
) -> list[dict]:
    """Return the most recently created notes directly from SQLite."""
    db_path = _brain_db_path(state_file)
    if not os.path.exists(db_path):
        return get_recent_notes(_load_brain_for_queries(state_file), count)

    try:
        conn = _ensure_brain_db(db_path)
        try:
            notes = []
            for note_id, raw_json in conn.execute(
                "SELECT id, raw_json FROM brain_notes "
                "ORDER BY created_at DESC LIMIT ?",
                (count,),
            ).fetchall():
                note = json.loads(raw_json)
                if isinstance(note, dict):
                    _normalize_note_dict(note, note_id)
                    notes.append(note)
            return notes
        finally:
            conn.close()
    except (sqlite3.DatabaseError, ValueError, UnicodeDecodeError):
        logger.warning("get_recent_notes_from_store fallback", exc_info=True)
        return get_recent_notes(_load_brain_for_queries(state_file), count)


def get_unconsolidated_notes_from_store(state_file: str) -> list[dict]:
    """Return notes not yet consumed by any wiki page, queried from SQLite."""
    db_path = _brain_db_path(state_file)
    if not os.path.exists(db_path):
        return get_unconsolidated_notes(_load_brain_for_queries(state_file))

    try:
        conn = _ensure_brain_db(db_path)
        try:
            # Collect consolidated IDs from page sources
            consolidated_ids: set[str] = set()
            for (raw_json,) in conn.execute(
                "SELECT raw_json FROM brain_pages"
            ).fetchall():
                page = json.loads(raw_json)
                if isinstance(page, dict):
                    consolidated_ids.update(page.get("sources", []))

            notes = []
            for note_id, raw_json in conn.execute(
                "SELECT id, raw_json FROM brain_notes "
                "WHERE category != 'archive' ORDER BY created_at DESC"
            ).fetchall():
                if note_id in consolidated_ids:
                    continue
                note = json.loads(raw_json)
                if isinstance(note, dict):
                    _normalize_note_dict(note, note_id)
                    notes.append(note)
            return notes
        finally:
            conn.close()
    except (sqlite3.DatabaseError, ValueError, UnicodeDecodeError):
        logger.warning("get_unconsolidated_notes_from_store fallback", exc_info=True)
        return get_unconsolidated_notes(_load_brain_for_queries(state_file))


def decay_stale_notes_from_store(
    state_file: str, max_age_days: int = 30,
) -> int:
    """Archive orphan notes older than max_age_days via SQL UPDATE."""
    db_path = _brain_db_path(state_file)
    if not os.path.exists(db_path):
        return 0

    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    try:
        conn = _ensure_brain_db(db_path)
        try:
            # Find all note IDs that have at least one connection
            connected_ids: set[str] = set()
            for from_id, to_id in conn.execute(
                "SELECT from_id, to_id FROM brain_connections"
            ).fetchall():
                if from_id:
                    connected_ids.add(from_id)
                if to_id:
                    connected_ids.add(to_id)

            # Find stale orphan notes
            stale = []
            for (note_id,) in conn.execute(
                "SELECT id FROM brain_notes "
                "WHERE category != 'archive' AND created_at < ? AND created_at != ''",
                (cutoff,),
            ).fetchall():
                if note_id not in connected_ids:
                    stale.append(note_id)

            if not stale:
                return 0

            # Batch-update to archive
            with conn:
                for note_id in stale:
                    raw = conn.execute(
                        "SELECT raw_json FROM brain_notes WHERE id = ?", (note_id,)
                    ).fetchone()
                    if raw:
                        note = json.loads(raw[0])
                        note["category"] = "archive"
                        conn.execute(
                            "UPDATE brain_notes SET category = 'archive', raw_json = ? WHERE id = ?",
                            (json.dumps(note), note_id),
                        )
            return len(stale)
        finally:
            conn.close()
    except (sqlite3.DatabaseError, ValueError, UnicodeDecodeError):
        logger.warning("decay_stale_notes_from_store failed", exc_info=True)
        return 0


def find_duplicate_from_store(
    state_file: str, content: str, threshold: float = 0.50,
) -> str | None:
    """Check for duplicate notes using FTS5 when available, else token overlap."""
    db_path = _brain_db_path(state_file)
    if not os.path.exists(db_path):
        return find_duplicate(_load_brain_for_queries(state_file), content, threshold)

    try:
        conn = _ensure_brain_db(db_path)
        try:
            candidates: list[tuple[str, str]] = []

            if _has_fts(conn):
                # Use FTS5 to find candidates — much faster than full scan
                words = _query_words(content)
                if words:
                    fts_query = " OR ".join(words[:10])
                    try:
                        candidates = conn.execute(
                            "SELECT bn.id, bn.content FROM brain_notes bn "
                            "JOIN brain_notes_fts fts ON bn.rowid = fts.rowid "
                            "WHERE brain_notes_fts MATCH ? LIMIT 50",
                            (fts_query,),
                        ).fetchall()
                    except sqlite3.OperationalError:
                        candidates = []

            if not candidates:
                # Fallback: scan all notes (same as in-memory path)
                candidates = conn.execute(
                    "SELECT id, content FROM brain_notes WHERE category != 'archive' "
                    "ORDER BY created_at DESC LIMIT 500"
                ).fetchall()

            for note_id, note_content in candidates:
                if _token_overlap(content, note_content or "") >= threshold:
                    return note_id
            return None
        finally:
            conn.close()
    except (sqlite3.DatabaseError, ValueError, UnicodeDecodeError):
        logger.warning("find_duplicate_from_store fallback", exc_info=True)
        return find_duplicate(_load_brain_for_queries(state_file), content, threshold)


def build_brain_summary_from_store(
    state_file: str,
    max_notes: int = 10,
    *,
    query_topic: str = "",
    max_pages: int = 10,
) -> str:
    """Build brain summary for prompt injection directly from SQLite.

    This is the Phase 3 replacement for ``build_brain_summary(brain)``:
    it queries only the rows needed instead of loading the full brain.
    """
    db_path = _brain_db_path(state_file)
    if not os.path.exists(db_path):
        brain = _load_brain_for_queries(state_file)
        return build_brain_summary(brain, max_notes, query_topic=query_topic, max_pages=max_pages)

    try:
        conn = _ensure_brain_db(db_path)
        try:
            meta = {
                key: json.loads(value_json)
                for key, value_json in conn.execute(
                    "SELECT key, value_json FROM brain_meta"
                ).fetchall()
            }
            notes_total = conn.execute("SELECT COUNT(*) FROM brain_notes").fetchone()[0]
            pages_total = conn.execute("SELECT COUNT(*) FROM brain_pages").fetchone()[0]
            connections_total = conn.execute("SELECT COUNT(*) FROM brain_connections").fetchone()[0]
            topics_total = conn.execute("SELECT COUNT(*) FROM brain_topics").fetchone()[0]

            # Unconsolidated count
            consolidated_ids: set[str] = set()
            for (raw_json,) in conn.execute("SELECT raw_json FROM brain_pages").fetchall():
                page = json.loads(raw_json)
                if isinstance(page, dict):
                    consolidated_ids.update(page.get("sources", []))
            unconsolidated_count = conn.execute(
                "SELECT COUNT(*) FROM brain_notes WHERE category != 'archive'"
            ).fetchone()[0]
            unconsolidated_count -= sum(
                1 for (nid,) in conn.execute(
                    "SELECT id FROM brain_notes WHERE category != 'archive'"
                ).fetchall() if nid in consolidated_ids
            )

            lines = ["== SECOND BRAIN =="]
            lines.append(f"Wiki pages: {pages_total}")
            lines.append(f"Total notes: {notes_total} ({unconsolidated_count} pending consolidation)")
            lines.append(f"Total connections: {connections_total}")
            lines.append(f"Total topics: {topics_total}")
            lines.append(f"Last review: {meta.get('last_review') or 'never'}")
            lines.append(f"Last consolidation: {meta.get('last_consolidation') or 'never'}")
            lines.append(f"Last dream: {meta.get('last_dream') or 'never'}")

            # Category breakdown — direct SQL aggregation
            categories = dict(conn.execute(
                "SELECT category, COUNT(*) FROM brain_notes GROUP BY category"
            ).fetchall())
            if categories:
                lines.append(f"Categories: {', '.join(f'{k}={v}' for k, v in categories.items())}")

            # Wiki page summaries (long-term memory)
            if pages_total > 0:
                wiki_lines = ["== WIKI PAGES =="]
                for page_id, raw_json in conn.execute(
                    "SELECT id, raw_json FROM brain_pages ORDER BY updated_at DESC LIMIT ?",
                    (max_pages,),
                ).fetchall():
                    page = json.loads(raw_json)
                    if not isinstance(page, dict):
                        continue
                    first_line = page.get("content", "").split("\n", 1)[0][:80]
                    source_count = len(page.get("sources", []))
                    wiki_lines.append(f"  📄 {page.get('title', page_id)} ({source_count} sources): {first_line}")
                lines.append("\n" + "\n".join(wiki_lines))

            # Topic map (top topics by note count)
            top_topics = []
            for topic_id, raw_json in conn.execute(
                "SELECT id, raw_json FROM brain_topics ORDER BY id"
            ).fetchall():
                topic = json.loads(raw_json)
                if isinstance(topic, dict):
                    top_topics.append({
                        "name": topic.get("name", ""),
                        "notes": len(topic.get("note_ids", [])),
                    })
            if top_topics:
                top_topics.sort(key=lambda t: t["notes"], reverse=True)
                topic_strs = [f"{t['name']}({t['notes']})" for t in top_topics[:8]]
                lines.append("\nTopics: " + ", ".join(topic_strs))

            # Select notes for the prompt
            if query_topic:
                # Topic-based recall via SQL
                lower = query_topic.lower().strip()
                topic_row = conn.execute(
                    "SELECT raw_json FROM brain_topics WHERE name = ? COLLATE NOCASE",
                    (lower,),
                ).fetchone()
                selected_ids: list[str] = []
                if topic_row:
                    topic = json.loads(topic_row[0])
                    selected_ids = topic.get("note_ids", [])[:max_notes]
                selected = []
                if selected_ids:
                    ph = ",".join("?" for _ in selected_ids)
                    for nid, rj in conn.execute(
                        f"SELECT id, raw_json FROM brain_notes WHERE id IN ({ph})",
                        tuple(selected_ids),
                    ).fetchall():
                        note = json.loads(rj)
                        if isinstance(note, dict):
                            _normalize_note_dict(note, nid)
                            selected.append(note)
                label = f"Knowledge related to '{query_topic}'"
            elif pages_total > 0:
                # When pages exist, only show recent unconsolidated notes
                capped = min(max_notes, 5)
                selected = []
                for nid, rj in conn.execute(
                    "SELECT id, raw_json FROM brain_notes "
                    "WHERE category != 'archive' "
                    "ORDER BY created_at DESC LIMIT ?",
                    (capped * 3,),  # over-fetch to filter out consolidated
                ).fetchall():
                    if nid in consolidated_ids:
                        continue
                    note = json.loads(rj)
                    if isinstance(note, dict):
                        _normalize_note_dict(note, nid)
                        selected.append(note)
                    if len(selected) >= capped:
                        break
                label = "Recent unconsolidated notes"
            else:
                # No pages — show recent notes
                selected = []
                for nid, rj in conn.execute(
                    "SELECT id, raw_json FROM brain_notes "
                    "ORDER BY created_at DESC LIMIT ?",
                    (max_notes,),
                ).fetchall():
                    note = json.loads(rj)
                    if isinstance(note, dict):
                        _normalize_note_dict(note, nid)
                        selected.append(note)
                label = "Recent knowledge"

            if selected:
                lines.append(f"\n{label}:")
                for note in selected:
                    summary = note.get("summary") or note.get("content", "")[:80]
                    tags = ", ".join(note.get("tags", []))
                    tag_str = f" [{tags}]" if tags else ""
                    lines.append(f"  - ({note['id']}) {summary}{tag_str}")

            return "\n".join(lines)
        finally:
            conn.close()
    except (sqlite3.DatabaseError, ValueError, UnicodeDecodeError):
        logger.warning("build_brain_summary_from_store fallback", exc_info=True)
        brain = _load_brain_for_queries(state_file)
        return build_brain_summary(brain, max_notes, query_topic=query_topic, max_pages=max_pages)


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
        lines.append(f"Last dream: {brain.last_dream or 'never'}")

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
    contradicted  Note has active contradiction signals and should be reviewed.
    weak_signal   Note is being demoted by feedback or low confidence.

    Each issue is ``{"type": str, "severity": str, "note_id": str|None,
    "message": str}``.
    """
    issues: list[dict] = []
    low_signal_tags = {"general", "note", "misc", "temp", "todo"}

    connected_ids = (
        {c["from"] for c in brain.connections} | {c["to"] for c in brain.connections}
    )

    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

    for nid, note in brain.notes.items():
        _normalize_note_dict(note, nid)
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

        contradictions = _normalize_counter(note.get("contradiction_count"))
        positive_feedback = _normalize_counter(note.get("positive_feedback"))
        negative_feedback = _normalize_counter(note.get("negative_feedback"))
        confidence = _normalize_confidence(note.get("confidence"))
        if contradictions > 0:
            issues.append({
                "type": "contradicted",
                "severity": "warning" if note.get("status") in {"active", "confirmed"} else "info",
                "note_id": nid,
                "message": f"Note {nid} is contradicted by {contradictions} note(s)",
            })

        if negative_feedback > positive_feedback or (confidence is not None and confidence < 35 and not note.get("last_confirmed_at")):
            issues.append({
                "type": "weak_signal",
                "severity": "warning" if negative_feedback > positive_feedback else "info",
                "note_id": nid,
                "message": f"Note {nid} is weakly supported and may need review",
            })

        source = note.get("source", {})
        persona = source.get("persona") if isinstance(source, dict) else ""
        evidence = _normalize_evidence(note.get("evidence"))
        tags = _normalize_string_list(note.get("tags"))

        # Observer notes must cite concrete workspace evidence.
        if persona == "workspace_observer" and note.get("status") != "archive" and not evidence:
            issues.append({
                "type": "missing_citation",
                "severity": "warning",
                "note_id": nid,
                "message": f"Workspace observer note {nid} is missing evidence/citation entries",
            })

        # Tag hygiene: ensure useful non-generic tags for active notes.
        meaningful_tags = [t for t in tags if t.lower() not in low_signal_tags]
        if note.get("category") != "archive" and len(meaningful_tags) < 1:
            issues.append({
                "type": "tag_quality",
                "severity": "info",
                "note_id": nid,
                "message": f"Note {nid} has low-quality tags; add at least one specific tag",
            })

        # Stale repeated observer pattern notes should be consolidated.
        created_at = note.get("created_at", "")
        if (
            persona == "workspace_observer"
            and isinstance(created_at, str)
            and created_at < (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            and "same files touched" in note.get("content", "").lower()
            and note.get("category") != "archive"
        ):
            issues.append({
                "type": "stale_pattern",
                "severity": "info",
                "note_id": nid,
                "message": f"Observer note {nid} repeats stale 'same files touched' pattern",
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
