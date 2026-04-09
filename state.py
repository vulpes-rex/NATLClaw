from __future__ import annotations

import json
import logging
import os
import tempfile
import asyncio
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

@dataclass
class AgentState:
    """Persistent agent state between heartbeats."""

    last_heartbeat: str | None = None
    execution_count: int = 0
    memory: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)
    # execution_history has moved to SQLite — see execution_log.py
    # Kept as a field for backward-compatible load (auto-migrated).
    execution_history: list[dict] = field(default_factory=list)
    lessons_learned: list[dict] = field(default_factory=list)
    # FP/TP calibration per lesson rule — keyed by "{type}_{step}",
    # e.g. "error_encountered_capture".  Values: {"fp": int, "tp": int,
    # "confidence_floor": int, "confidence_bonus": int}
    lesson_calibration: dict = field(default_factory=dict)


def _read_state(path: str) -> dict:
    """Read state JSON from disk (runs in executor)."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _migrate_execution_history(data: dict, state_path: str) -> None:
    """One-time migration: move execution_history rows into SQLite."""
    entries = data.get("execution_history")
    if not entries:
        return
    # Lazy import to avoid circular dependency at module level
    from execution_log import migrate_from_state
    db_path = os.path.join(os.path.dirname(state_path), "execution_log.db")
    inserted = migrate_from_state(entries, db_path=db_path)
    if inserted:
        logger.info("Migrated %d execution_history entries to SQLite", inserted)


def _write_state(state: AgentState, path: str, max_history: int) -> None:
    """Write state JSON atomically (runs in executor)."""
    if state.lessons_learned is None:
        state.lessons_learned = []
    if state.lesson_calibration is None:
        state.lesson_calibration = {}
    if len(state.lessons_learned) > max_history:
        state.lessons_learned = state.lessons_learned[-max_history:]

    # Execution history is now in SQLite — write an empty list to JSON
    state.execution_history = []

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    dir_name = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(asdict(state), f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except BaseException:
        os.unlink(tmp_path)
        raise


async def load_state(path: str) -> AgentState:
    """Load agent state from a JSON file. Returns default state if missing."""
    if os.path.exists(path):
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, _read_state, path)

        # One-time migration: execution_history → SQLite
        _migrate_execution_history(data, path)

        filtered = {
            k: v for k, v in data.items() if k in AgentState.__dataclass_fields__
        }
        # Always start with empty history (it's in SQLite now)
        filtered["execution_history"] = []
        # Normalize None → [] / {} for collection fields
        if filtered.get("lessons_learned") is None:
            filtered["lessons_learned"] = []
        if filtered.get("lesson_calibration") is None:
            filtered["lesson_calibration"] = {}
        return AgentState(**filtered)
    return AgentState()


async def save_state(state: AgentState, path: str, max_history: int = 100) -> None:
    """Save agent state to JSON atomically (write tmp then rename)."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _write_state, state, path, max_history)
