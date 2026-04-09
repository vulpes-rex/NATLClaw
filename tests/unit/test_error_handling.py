#!/usr/bin/env python3
"""Test script to verify error handling in core modules."""

import asyncio
import tempfile
import os
import json
from unittest.mock import patch, MagicMock
from dataclasses import is_dataclass

# Import our modules
from scheduler import run_scheduler
from workflow import _run_step, _store_capture, _store_connection
from second_brain import load_brain, save_brain, add_note, connect_notes, get_recent_notes, build_brain_summary, BrainState
from state import AgentState, load_state, save_state
from agent_setup import create_agent, _build_mcp_servers, _create_secure_permission_handler

# Test load_brain with non-existent file
print("=== Testing load_brain with non-existent file ===")
brain = asyncio.run(load_brain("nonexistent.file"))
assert isinstance(brain, BrainState)
print("✓ load_brain returns empty BrainState when file doesn't exist")

# Test load_brain with corrupted JSON
print("=== Testing load_brain with corrupted JSON ===")
with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
    f.write("{ invalid json")
    corrupted_file = f.name

try:
    brain = asyncio.run(load_brain(corrupted_file))
    assert isinstance(brain, BrainState)
    print("✓ load_brain handles corrupted JSON and returns empty BrainState")
finally:
    os.unlink(corrupted_file)

# Test save_brain with permission error (mocked)
print("=== Testing save_brain with permission error ===")
brain = BrainState()
with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
    temp_file = f.name

# Change permissions to read-only to simulate error
os.chmod(temp_file, 0o444)

try:
    try:
        asyncio.run(save_brain(brain, temp_file))
        print("✗ save_brain should have raised an error")
    except Exception as e:
        print(f"✓ save_brain correctly raised error: {type(e).__name__}")
finally:
    # Clean up - change permissions back first
    os.chmod(temp_file, 0o600)
    os.unlink(temp_file)

# Test add_note error handling
print("=== Testing add_note error handling ===")
brain = BrainState()
try:
    # This should succeed
    note_id = add_note(brain, "test content")
    assert note_id is not None
    print("✓ add_note works correctly")
except Exception as e:
    print(f"✗ add_note failed: {e}")

# Test _store_capture with invalid JSON
print("=== Testing _store_capture with invalid JSON ===")
brain = BrainState()
invalid_json = "not json at all"
try:
    note_id = _store_capture(brain, invalid_json)
    assert note_id is not None  # Should fallback to simple note
    print("✓ _store_capture handles invalid JSON and falls back to simple note")
except Exception as e:
    print(f"✗ _store_capture failed: {e}")

# Test _store_connection with invalid JSON
print("=== Testing _store_connection with invalid JSON ===")
brain = BrainState()
invalid_json = "not json at all"
try:
    _store_connection(brain, invalid_json)
    print("✓ _store_connection handles invalid JSON gracefully")
except Exception as e:
    print(f"✗ _store_connection failed: {e}")

# Test get_recent_notes with empty brain
print("=== Testing get_recent_notes with empty brain ===")
brain = BrainState()
notes = get_recent_notes(brain, 10)
assert isinstance(notes, list)
assert len(notes) == 0
print("✓ get_recent_notes returns empty list for empty brain")

# Test build_brain_summary with empty brain
print("=== Testing build_brain_summary with empty brain ===")
brain = BrainState()
summary = build_brain_summary(brain)
assert isinstance(summary, str)
assert "TOTAL NOTES" in summary.upper()
print("✓ build_brain_summary returns valid summary for empty brain")

# Test create_agent with invalid provider (should raise ValueError)
print("=== Testing create_agent with invalid provider ===")
try:
    # Mock config with invalid provider
    config = MagicMock()
    config.provider = "invalid_provider"
    config.model = "test-model"
    config.agent_name = "test-agent"
    config.openai_api_key = "test-key"
    config.project_endpoint = "test-endpoint"
    config.ollama_host = "test-host"
    
    create_agent(config, "test instructions")
    print("✗ create_agent should have raised ValueError")
except ValueError as e:
    print(f"✓ create_agent correctly raises ValueError for invalid provider: {e}")
except Exception as e:
    print(f"✗ create_agent raised unexpected error: {type(e).__name__}: {e}")

# Test _build_mcp_servers with missing required fields
print("=== Testing _build_mcp_servers with missing command ===")
try:
    raw = {
        "test_server": {
            "type": "local",
            "command": ""  # Missing command should cause error
        }
    }
    servers = _build_mcp_servers(raw)
    print("✗ _build_mcp_servers should have raised error for missing command")
except (KeyError, ValueError) as e:
    print(f"✓ _build_mcp_servers correctly raises error for missing command: {e}")
except Exception as e:
    print(f"✗ _build_mcp_servers raised unexpected error: {type(e).__name__}: {e}")

print("\n=== All error handling tests completed ===")