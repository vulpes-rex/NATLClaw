"""Test suite for state.py - State management and atomic operations."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import pytest
import sys
from unittest.mock import MagicMock, patch

# Mock external dependencies
with patch.dict('sys.modules', {
    'agent_framework_github_copilot': MagicMock(),
    'agent_framework': MagicMock(),
    'agent_framework.foundry': MagicMock(),
    'agent_framework.openai': MagicMock(),
    'agent_framework.ollama': MagicMock(),
    'azure.identity': MagicMock(),
}):
    from state import AgentState, load_state, save_state
    import state as _state_mod

# Set up logging to avoid warnings during tests
logging.basicConfig(level=logging.DEBUG)


def _run(coro):
    """Helper to run an async coroutine synchronously in tests."""
    return asyncio.run(coro)


@pytest.fixture
def temp_state_file():
    """Create a temporary state file."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        f.write("{}")  # Empty state
        state_file = f.name
    yield state_file
    # Cleanup
    if os.path.exists(state_file):
        os.remove(state_file)


def test_agent_state_dataclass():
    """Test that AgentState is properly defined as a dataclass."""
    state = AgentState()
    assert hasattr(state, 'last_heartbeat')
    assert hasattr(state, 'execution_count')
    assert hasattr(state, 'memory')
    assert hasattr(state, 'context')
    assert hasattr(state, 'execution_history')
    assert hasattr(state, 'lessons_learned')


def test_load_state_file_exists():
    """Test loading state from existing file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "state.json")
        with open(state_file, "w") as f:
            json.dump({
                "last_heartbeat": "2024-01-01T00:00:00Z",
                "execution_count": 42,
                "memory": {"key": "value"},
                "context": {"some": "data"},
                "execution_history": [{"timestamp": "2024-01-01T00:00:00Z", "step": "test", "prompt": "test", "response": "test"}],
                "lessons_learned": [{"description": "Test lesson", "category": "insight"}]
            }, f, indent=2)

        from execution_log import set_db_path, total_count
        set_db_path(os.path.join(tmpdir, "execution_log.db"))

        state = _run(load_state(state_file))
        assert state.last_heartbeat == "2024-01-01T00:00:00Z"
        assert state.execution_count == 42
        assert state.memory == {"key": "value"}
        assert state.context == {"some": "data"}
        # execution_history migrated to SQLite — state field is empty
        assert state.execution_history == []
        assert total_count() == 1
        assert len(state.lessons_learned) == 1
        assert state.lessons_learned[0]["description"] == "Test lesson"


def test_load_state_file_not_exists():
    """Test loading state when file doesn't exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "nonexistent.json")
        state = _run(load_state(state_file))
        assert isinstance(state, AgentState)
        assert state.last_heartbeat is None
        assert state.execution_count == 0
        assert state.memory == {}
        assert state.context == {}
        assert state.execution_history == []
        assert state.lessons_learned == []


def test_load_state_invalid_json():
    """Test loading state with invalid JSON raises an error."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        f.write("{ invalid json }")
        state_file = f.name

    try:
        with pytest.raises(json.JSONDecodeError):
            _run(load_state(state_file))
    finally:
        os.remove(state_file)


def test_save_state_atomic_write():
    """Test that save_state writes a valid JSON file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "state.json")

        state = AgentState(
            last_heartbeat="2024-01-01T00:00:00Z",
            execution_count=42,
            memory={"key": "value"},
            context={"some": "data"},
            execution_history=[{"timestamp": "2024-01-01T00:00:00Z", "step": "test", "prompt": "test", "response": "test"}],
            lessons_learned=[{"description": "Test lesson", "category": "insight"}]
        )

        _run(save_state(state, state_file))

        # Verify file was written and is valid JSON
        with open(state_file, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded["execution_count"] == 42
        assert loaded["memory"] == {"key": "value"}


def test_save_state_creates_parent_directories():
    """Test that save_state creates parent directories if they don't exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "subdir", "state.json")
        assert not os.path.exists(os.path.dirname(state_file))

        state = AgentState()
        _run(save_state(state, state_file))

        assert os.path.exists(os.path.dirname(state_file))
        assert os.path.isfile(state_file)


def test_save_state_history_truncation():
    """Test that save_state truncates lessons_learned to max_history.

    execution_history is now in SQLite and no longer stored in the JSON.
    """
    state = AgentState(
        last_heartbeat="2024-01-01T00:00:00Z",
        execution_count=0,
        execution_history=[],
        lessons_learned=[{"description": f"Lesson {i}", "category": "insight"} for i in range(150)]
    )

    assert len(state.lessons_learned) == 150

    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "state.json")
        _run(save_state(state, state_file, max_history=100))

        # Reload and check truncation
        loaded_state = _run(load_state(state_file))
        assert loaded_state.execution_history == [], "execution_history should be empty in JSON"
        assert len(loaded_state.lessons_learned) == 100


def test_save_state_default_max_history():
    """Test that save_state uses default max_history of 100 for lessons."""
    state = AgentState(
        execution_history=[],
        lessons_learned=[{"description": f"Lesson {i}", "category": "insight"} for i in range(150)]
    )

    assert len(state.lessons_learned) == 150

    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "state.json")
        _run(save_state(state, state_file))

        loaded_state = _run(load_state(state_file))
        assert loaded_state.execution_history == [], "execution_history should be empty in JSON"
        assert len(loaded_state.lessons_learned) == 100


def test_save_state_handles_permission_denied():
    """Test that save_state handles permission errors gracefully."""
    state = AgentState()

    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "state.json")
        # Use patch.object on the actual module object (not the string path)
        # because the patch.dict context at import time means sys.modules["state"]
        # may differ from the module save_state was defined in.
        with patch.object(_state_mod, "_write_state", side_effect=PermissionError("Access denied")):
            with pytest.raises((PermissionError, OSError)):
                _run(save_state(state, state_file))


def test_load_state_handles_corrupted_file():
    """Test that load_state handles files with unexpected structure."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        # Write valid JSON but with wrong structure
        json.dump({"invalid": "data"}, f)
        state_file = f.name

    try:
        state = _run(load_state(state_file))
        # Should return default state with only valid fields
        assert state.last_heartbeat is None
        assert state.execution_count == 0
    finally:
        os.remove(state_file)


def test_save_state_with_special_characters():
    """Test saving state with special characters in data."""
    state = AgentState(
        memory={"key": "value with unicode: \u4f60\u597d \u2713", "path": "C:\\test\\path"},
        context={"complex": {"nested": [1, 2, 3]}}
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "state.json")
        _run(save_state(state, state_file))

        loaded = _run(load_state(state_file))
        assert loaded.memory["key"] == "value with unicode: \u4f60\u597d \u2713"
        assert loaded.memory["path"] == "C:\\test\\path"


def test_agent_state_equality():
    """Test that AgentState equality works correctly."""
    state1 = AgentState(
        last_heartbeat="2024-01-01T00:00:00Z",
        execution_count=42,
        memory={"a": 1},
        context={"b": 2}
    )
    state2 = AgentState(
        last_heartbeat="2024-01-01T00:00:00Z",
        execution_count=42,
        memory={"a": 1},
        context={"b": 2}
    )
    assert state1 == state2

    # Different execution_count
    state3 = AgentState(execution_count=43)
    assert state1 != state3


def test_agent_state_copy():
    """Test that AgentState can be copied."""
    original = AgentState(
        last_heartbeat="2024-01-01T00:00:00Z",
        execution_count=42,
        memory={"key": "value"},
        context={"some": "data"}
    )
    # Create a copy using asdict
    copied_dict = asdict(original)
    copied_state = AgentState(**copied_dict)

    assert copied_state == original
    assert copied_state.last_heartbeat == original.last_heartbeat
    assert copied_state.execution_count == original.execution_count


def test_load_state_with_large_history():
    """Test loading state with very large history migrates to SQLite."""
    large_history = [{"timestamp": "2024-01-01T00:00:00Z", "step": f"step_{i}", "prompt": "x"*200, "response": "y"*200} for i in range(500)]

    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "state.json")
        with open(state_file, "w") as f:
            json.dump({
                "last_heartbeat": "2024-01-01T00:00:00Z",
                "execution_count": 1000,
                "execution_history": large_history,
                "lessons_learned": []
            }, f, indent=2)

        from execution_log import set_db_path, total_count
        set_db_path(os.path.join(tmpdir, "execution_log.db"))

        state = _run(load_state(state_file))
        # execution_history is empty in state (migrated to SQLite)
        assert state.execution_history == []
        assert state.execution_count == 1000
        # Entries were migrated to SQLite
        assert total_count() == 500


def test_save_state_with_large_data():
    """Test saving state with very large data."""
    large_data = {"key": "value" * 100000}  # Very large string

    state = AgentState(
        memory=large_data,
        execution_history=[{"timestamp": "2024-01-01T00:00:00Z", "step": "test", "prompt": "test", "response": "test"}]
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "state.json")
        _run(save_state(state, state_file))
        loaded = _run(load_state(state_file))
        assert loaded.memory["key"] == large_data["key"]


def test_state_file_atomicity_in_multi_process():
    """Test that atomic write produces a valid file."""
    state = AgentState(execution_count=1)

    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "state.json")
        _run(save_state(state, state_file))

        assert os.path.exists(state_file)

        loaded = _run(load_state(state_file))
        assert loaded.execution_count == 1


def test_save_state_with_none_values():
    """Test that save_state handles None values correctly."""
    state = AgentState(
        last_heartbeat=None,
        execution_count=0,
        memory=None,
        context=None,
        execution_history=None,
        lessons_learned=None
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "state.json")
        _run(save_state(state, state_file))

        loaded = _run(load_state(state_file))
        assert loaded.last_heartbeat is None
        assert loaded.execution_count == 0


def test_load_state_with_missing_optional_fields():
    """Test that load_state handles missing optional fields."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump({
            "execution_count": 42,
            "memory": {"key": "value"}
        }, f, indent=2)
        state_file = f.name

    try:
        state = _run(load_state(state_file))
        assert state.execution_count == 42
        assert state.memory == {"key": "value"}
        assert state.last_heartbeat is None
        assert state.context == {}
        assert state.execution_history == []
        assert state.lessons_learned == []
    finally:
        os.remove(state_file)


def test_save_state_with_empty_state():
    """Test saving an empty AgentState."""
    state = AgentState()

    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "state.json")
        _run(save_state(state, state_file))

        loaded = _run(load_state(state_file))
        assert loaded.last_heartbeat is None
        assert loaded.execution_count == 0
        assert loaded.memory == {}
        assert loaded.context == {}
        assert loaded.execution_history == []
        assert loaded.lessons_learned == []


def test_state_file_permissions():
    """Test that saved state files have appropriate permissions."""
    state = AgentState(execution_count=1)

    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "state.json")
        _run(save_state(state, state_file))

        assert os.path.isfile(state_file)
        assert os.access(state_file, os.R_OK)
