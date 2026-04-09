"""Structured metrics collection for NATLClaw.

Provides two capabilities:

1. **JSON structured log formatter** — emits machine-parseable JSON log lines
   with consistent fields (heartbeat number, persona, elapsed, etc.).

2. **SQLite metrics store** — persists per-heartbeat metrics in a local
   ``data/metrics.db`` database for later analysis and dashboarding.

Usage
-----
::

    from metrics import MetricsStore, JsonFormatter

    # SQLite store
    store = MetricsStore("data/metrics.db")
    store.record_heartbeat(
        heartbeat_number=1,
        persona="researcher",
        workflow="second_brain",
        elapsed_sec=4.2,
        notes_created=2,
        connections_created=1,
    )
    rows = store.recent(10)

    # JSON formatter — attach to a logger handler
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ──────────────────────────────────────────────────────────────────────
# JSON log formatter
# ──────────────────────────────────────────────────────────────────────

class JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line.

    Standard fields:  ``timestamp``, ``level``, ``logger``, ``message``.
    Extra fields passed via ``extra={...}`` are merged into the top level.
    """

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Merge any extra fields the caller attached
        for key in ("heartbeat", "elapsed_sec", "notes_created",
                     "connections_created", "persona", "workflow",
                     "score", "interval"):
            val = getattr(record, key, None)
            if val is not None:
                entry[key] = val
        return json.dumps(entry, default=str)


# ──────────────────────────────────────────────────────────────────────
# SQLite metrics store
# ──────────────────────────────────────────────────────────────────────

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS heartbeat_metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL,
    heartbeat_number INTEGER NOT NULL,
    persona         TEXT    NOT NULL DEFAULT '',
    workflow        TEXT    NOT NULL DEFAULT '',
    elapsed_sec     REAL    NOT NULL DEFAULT 0,
    notes_created   INTEGER NOT NULL DEFAULT 0,
    connections_created INTEGER NOT NULL DEFAULT 0,
    score           INTEGER NOT NULL DEFAULT 0,
    interval_sec    REAL    NOT NULL DEFAULT 0
);
"""


class MetricsStore:
    """Lightweight SQLite store for heartbeat metrics."""

    def __init__(self, db_path: str = "data/metrics.db") -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(_CREATE_TABLE)
        self._conn.commit()

    # ── write ────────────────────────────────────────────────────────

    def record_heartbeat(
        self,
        *,
        heartbeat_number: int,
        persona: str = "",
        workflow: str = "",
        elapsed_sec: float = 0.0,
        notes_created: int = 0,
        connections_created: int = 0,
        score: int = 0,
        interval_sec: float = 0.0,
    ) -> None:
        """Insert one heartbeat metrics row."""
        self._conn.execute(
            """INSERT INTO heartbeat_metrics
               (timestamp, heartbeat_number, persona, workflow,
                elapsed_sec, notes_created, connections_created,
                score, interval_sec)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                heartbeat_number,
                persona,
                workflow,
                elapsed_sec,
                notes_created,
                connections_created,
                score,
                interval_sec,
            ),
        )
        self._conn.commit()

    # ── read ─────────────────────────────────────────────────────────

    def recent(self, limit: int = 20) -> list[dict]:
        """Return the most recent *limit* metrics rows as dicts."""
        cur = self._conn.execute(
            """SELECT id, timestamp, heartbeat_number, persona, workflow,
                      elapsed_sec, notes_created, connections_created,
                      score, interval_sec
               FROM heartbeat_metrics
               ORDER BY id DESC LIMIT ?""",
            (limit,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def summary(self) -> dict[str, Any]:
        """Return aggregate statistics across all recorded heartbeats."""
        cur = self._conn.execute(
            """SELECT
                 COUNT(*)                  AS total_heartbeats,
                 AVG(elapsed_sec)          AS avg_elapsed_sec,
                 SUM(notes_created)        AS total_notes_created,
                 SUM(connections_created)  AS total_connections_created,
                 AVG(score)                AS avg_score
               FROM heartbeat_metrics"""
        )
        cols = [d[0] for d in cur.description]
        row = cur.fetchone()
        return dict(zip(cols, row)) if row else {}

    def close(self) -> None:
        self._conn.close()
