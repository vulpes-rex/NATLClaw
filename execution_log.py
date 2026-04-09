"""SQLite-backed execution log.

Replaces the old in-memory ``state.execution_history`` list that was
truncated to 500 chars per response and 300 chars per prompt.  Full
text is now stored on disk and only the most-recent *N* rows are
loaded when the rest of the engine needs them.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_db_path: str = "data/execution_log.db"


def set_db_path(path: str) -> None:
    """Override the default database path (call once at startup)."""
    global _db_path
    _db_path = path


def get_db_path() -> str:
    """Return the current database path."""
    return _db_path

# ──────────────────────────────────────────────────────────────────────
# Schema
# ──────────────────────────────────────────────────────────────────────

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS execution_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT    NOT NULL,
    step      TEXT    NOT NULL,
    prompt    TEXT    NOT NULL DEFAULT '',
    response  TEXT    NOT NULL DEFAULT ''
);
"""

_CREATE_INDEX = """\
CREATE INDEX IF NOT EXISTS idx_execution_log_ts
    ON execution_log (timestamp DESC);
"""


# ──────────────────────────────────────────────────────────────────────
# Connection helpers
# ──────────────────────────────────────────────────────────────────────

def _ensure_db(db_path: str) -> sqlite3.Connection:
    """Open (or create) the database and ensure the schema exists."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(_CREATE_TABLE)
    conn.execute(_CREATE_INDEX)
    conn.commit()
    return conn


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────

def append_entry(
    step: str,
    prompt: str,
    response: str,
    *,
    timestamp: str | None = None,
    db_path: str | None = None,
) -> None:
    """Append one execution-history row.  Full text — no truncation."""
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()
    conn = _ensure_db(db_path or _db_path)
    try:
        conn.execute(
            "INSERT INTO execution_log (timestamp, step, prompt, response) "
            "VALUES (?, ?, ?, ?)",
            (timestamp, step, prompt, response),
        )
        conn.commit()
    finally:
        conn.close()


def recent_entries(
    n: int = 100,
    *,
    db_path: str | None = None,
) -> list[dict]:
    """Return the *n* most-recent rows, oldest-first (same order the
    old list had).  Each row is a dict with the same keys as before:
    ``timestamp``, ``step``, ``prompt``, ``response``.
    """
    resolved = db_path or _db_path
    if not os.path.exists(resolved):
        return []
    conn = _ensure_db(resolved)
    try:
        rows = conn.execute(
            "SELECT timestamp, step, prompt, response "
            "FROM execution_log ORDER BY id DESC LIMIT ?",
            (n,),
        ).fetchall()
    finally:
        conn.close()
    # Reverse so oldest is first (matches old list semantics)
    return [
        {"timestamp": r[0], "step": r[1], "prompt": r[2], "response": r[3]}
        for r in reversed(rows)
    ]


def total_count(*, db_path: str | None = None) -> int:
    """Return the total number of rows in the log."""
    resolved = db_path or _db_path
    if not os.path.exists(resolved):
        return 0
    conn = _ensure_db(resolved)
    try:
        return conn.execute("SELECT COUNT(*) FROM execution_log").fetchone()[0]
    finally:
        conn.close()


def clear_log(*, db_path: str | None = None) -> None:
    """Delete all rows (equivalent of the old ``/clear``)."""
    resolved = db_path or _db_path
    if not os.path.exists(resolved):
        return
    conn = _ensure_db(resolved)
    try:
        conn.execute("DELETE FROM execution_log")
        conn.commit()
    finally:
        conn.close()


def prune_old(max_rows: int = 10_000, *, db_path: str | None = None) -> int:
    """Keep only the most recent *max_rows*, deleting older entries.

    Returns the number of rows deleted.
    """
    resolved = db_path or _db_path
    if not os.path.exists(resolved):
        return 0
    conn = _ensure_db(resolved)
    try:
        total = conn.execute("SELECT COUNT(*) FROM execution_log").fetchone()[0]
        if total <= max_rows:
            return 0
        to_delete = total - max_rows
        conn.execute(
            "DELETE FROM execution_log WHERE id IN "
            "(SELECT id FROM execution_log ORDER BY id ASC LIMIT ?)",
            (to_delete,),
        )
        conn.commit()
        return to_delete
    finally:
        conn.close()


def migrate_from_state(entries: list[dict], *, db_path: str | None = None) -> int:
    """Bulk-insert rows from the old ``state.execution_history`` list.

    Skips duplicates (same timestamp + step).  Returns the number of
    rows actually inserted.
    """
    if not entries:
        return 0
    resolved = db_path or _db_path
    conn = _ensure_db(resolved)
    inserted = 0
    try:
        for entry in entries:
            ts = entry.get("timestamp", "")
            step = entry.get("step", "")
            prompt = entry.get("prompt", "")
            response = entry.get("response", "")
            # Skip if already exists
            exists = conn.execute(
                "SELECT 1 FROM execution_log WHERE timestamp = ? AND step = ? LIMIT 1",
                (ts, step),
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO execution_log (timestamp, step, prompt, response) "
                    "VALUES (?, ?, ?, ?)",
                    (ts, step, prompt, response),
                )
                inserted += 1
        conn.commit()
    finally:
        conn.close()
    return inserted
