"""Tests for the SQLite-backed execution_log module."""
from __future__ import annotations

import os
import pytest

from execution_log import (
    set_db_path,
    get_db_path,
    append_entry,
    recent_entries,
    total_count,
    clear_log,
    prune_old,
    migrate_from_state,
)


@pytest.fixture(autouse=True)
def _temp_db(tmp_path):
    """Use a fresh temp DB for every test."""
    db = str(tmp_path / "test_execution_log.db")
    set_db_path(db)
    yield db
    clear_log()


# ── set_db_path / get_db_path ──────────────────────────────────────

def test_set_and_get_db_path(tmp_path):
    custom = str(tmp_path / "custom.db")
    set_db_path(custom)
    assert get_db_path() == custom


# ── append_entry / recent_entries ───────────────────────────────────

def test_append_and_retrieve_single_entry():
    append_entry("step1", "prompt text", "response text")
    entries = recent_entries(10)
    assert len(entries) == 1
    assert entries[0]["step"] == "step1"
    assert entries[0]["prompt"] == "prompt text"
    assert entries[0]["response"] == "response text"
    assert "timestamp" in entries[0]


def test_append_preserves_full_text():
    """No truncation — large strings survive intact."""
    big = "x" * 50_000
    append_entry("big_step", big, big)
    entries = recent_entries(1)
    assert len(entries[0]["prompt"]) == 50_000
    assert len(entries[0]["response"]) == 50_000


def test_recent_entries_respects_limit():
    for i in range(10):
        append_entry(f"s{i}", "p", "r")
    entries = recent_entries(3)
    assert len(entries) == 3
    # Should be the 3 most recent, oldest-first
    assert entries[0]["step"] == "s7"
    assert entries[2]["step"] == "s9"


def test_recent_entries_oldest_first_ordering():
    append_entry("first", "p1", "r1", timestamp="2024-01-01T00:00:00Z")
    append_entry("second", "p2", "r2", timestamp="2024-01-02T00:00:00Z")
    entries = recent_entries(10)
    assert entries[0]["step"] == "first"
    assert entries[1]["step"] == "second"


def test_recent_entries_empty_db_returns_empty():
    assert recent_entries(10) == []


def test_recent_entries_nonexistent_db_returns_empty(tmp_path):
    entries = recent_entries(10, db_path=str(tmp_path / "nonexistent.db"))
    assert entries == []


def test_custom_timestamp():
    append_entry("t", "p", "r", timestamp="2025-06-15T12:00:00Z")
    entries = recent_entries(1)
    assert entries[0]["timestamp"] == "2025-06-15T12:00:00Z"


# ── total_count ─────────────────────────────────────────────────────

def test_total_count_empty():
    assert total_count() == 0


def test_total_count_after_inserts():
    for i in range(5):
        append_entry(f"s{i}", "p", "r")
    assert total_count() == 5


def test_total_count_nonexistent_db(tmp_path):
    assert total_count(db_path=str(tmp_path / "gone.db")) == 0


# ── clear_log ───────────────────────────────────────────────────────

def test_clear_log_removes_all_entries():
    for i in range(5):
        append_entry(f"s{i}", "p", "r")
    assert total_count() == 5
    clear_log()
    assert total_count() == 0


def test_clear_log_nonexistent_db_is_noop(tmp_path):
    clear_log(db_path=str(tmp_path / "nope.db"))  # should not raise


# ── prune_old ───────────────────────────────────────────────────────

def test_prune_old_keeps_max_rows():
    for i in range(20):
        append_entry(f"s{i}", "p", "r")
    deleted = prune_old(max_rows=10)
    assert deleted == 10
    assert total_count() == 10
    # Should keep the 10 most recent
    entries = recent_entries(100)
    assert entries[0]["step"] == "s10"
    assert entries[-1]["step"] == "s19"


def test_prune_old_noop_when_under_limit():
    for i in range(5):
        append_entry(f"s{i}", "p", "r")
    deleted = prune_old(max_rows=10)
    assert deleted == 0
    assert total_count() == 5


def test_prune_old_nonexistent_db(tmp_path):
    assert prune_old(db_path=str(tmp_path / "nope.db")) == 0


# ── migrate_from_state ──────────────────────────────────────────────

def test_migrate_from_state_inserts_entries():
    old_entries = [
        {"timestamp": "2024-01-01T00:00:00Z", "step": "s0", "prompt": "p0", "response": "r0"},
        {"timestamp": "2024-01-02T00:00:00Z", "step": "s1", "prompt": "p1", "response": "r1"},
    ]
    inserted = migrate_from_state(old_entries)
    assert inserted == 2
    assert total_count() == 2
    entries = recent_entries(10)
    assert entries[0]["step"] == "s0"
    assert entries[1]["step"] == "s1"


def test_migrate_from_state_skips_duplicates():
    old_entries = [
        {"timestamp": "2024-01-01T00:00:00Z", "step": "s0", "prompt": "p", "response": "r"},
    ]
    migrate_from_state(old_entries)
    # Migrate again — should skip
    inserted = migrate_from_state(old_entries)
    assert inserted == 0
    assert total_count() == 1


def test_migrate_from_state_empty_list():
    assert migrate_from_state([]) == 0


def test_migrate_from_state_handles_missing_fields():
    old_entries = [
        {"timestamp": "2024-01-01T00:00:00Z"},  # missing step, prompt, response
    ]
    inserted = migrate_from_state(old_entries)
    assert inserted == 1
    entries = recent_entries(1)
    assert entries[0]["step"] == ""
    assert entries[0]["prompt"] == ""
    assert entries[0]["response"] == ""


# ── db_path kwarg override ──────────────────────────────────────────

def test_explicit_db_path_override(tmp_path):
    alt_db = str(tmp_path / "alt.db")
    append_entry("alt_step", "p", "r", db_path=alt_db)

    # Module-level DB should be empty
    assert total_count() == 0
    # Alt DB should have the entry
    assert total_count(db_path=alt_db) == 1
    entries = recent_entries(10, db_path=alt_db)
    assert entries[0]["step"] == "alt_step"


# ── concurrent safety (WAL mode) ───────────────────────────────────

def test_wal_mode_enabled(_temp_db):
    """Verify the database uses WAL journal mode."""
    import sqlite3
    conn = sqlite3.connect(_temp_db)
    try:
        append_entry("wal_test", "p", "r")
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
    finally:
        conn.close()
