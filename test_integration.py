"""Integration tests — real component interactions, real files, minimal mocking.

These tests verify that NATLClaw's modules work correctly *together*:
  - State ↔ Brain round-trips through real JSON files
  - Workflow helpers mutating real Brain / State objects
  - Persona loader resolving real mcp.json entries
  - Learning engine feeding real state data
  - Config loading from real environment variables

Only external LLM SDKs are mocked (they require network / credentials).
Everything else uses real code paths.

Run subsets:
    pytest test_integration.py -m "state_brain"      # persistence round-trips
    pytest test_integration.py -m "workflow"          # workflow ↔ brain ↔ state
    pytest test_integration.py -m "persona"           # persona loader chain
    pytest test_integration.py -m "learning"          # learning + context
    pytest test_integration.py -m "config"            # config from env vars
    pytest test_integration.py --lf                   # re-run only failures
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

import pytest
import sys
from unittest.mock import MagicMock, patch, AsyncMock

# ---------------------------------------------------------------------------
# Mock only the external LLM SDKs — everything else is real
# ---------------------------------------------------------------------------
sys.modules['copilot'] = MagicMock()
sys.modules['agent_framework_github_copilot'] = MagicMock()
sys.modules['agent_framework'] = MagicMock()
sys.modules['agent_framework.foundry'] = MagicMock()
sys.modules['agent_framework.openai'] = MagicMock()
sys.modules['agent_framework.ollama'] = MagicMock()
sys.modules['azure.identity'] = MagicMock()

from state import AgentState, load_state, save_state
from second_brain import (
    BrainState, Note, add_note, connect_notes,
    get_notes_by_category, get_recent_notes, build_brain_summary,
    load_brain, save_brain, _brain_path,
)
from workflow import (
    _store_capture, _store_connection, _run_step,
    run_heartbeat, _run_second_brain_heartbeat,
    _run_freeform_heartbeat, _run_steps_heartbeat,
    _run_all_steps, _run_one_step, _distil_to_brain,
)
from scheduler import retry
from learning import extract_lessons, build_context_block
from config import AppConfig, load_config
from persona_loader import load_persona, list_personas, Persona
from execution_log import set_db_path, recent_entries, total_count, clear_log


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _use_temp_execution_db(tmp_path):
    """Point the execution log at a per-test temp DB."""
    set_db_path(str(tmp_path / "execution_log.db"))
    yield
    clear_log()

@pytest.fixture
def tmp_data_dir():
    """Create a temp directory that acts as the data/ folder."""
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def state_file(tmp_data_dir):
    """Return a state file path inside the temp data dir."""
    return os.path.join(tmp_data_dir, "agent_state.json")


@pytest.fixture
def fresh_state():
    """A clean AgentState with no history."""
    return AgentState()


@pytest.fixture
def populated_state():
    """An AgentState with some history and lessons."""
    return AgentState(
        last_heartbeat="2026-04-07T10:00:00Z",
        execution_count=5,
        memory={"project": "NATLClaw", "focus": "integration tests"},
        context={"last_persona": "python_developer"},
        execution_history=[
            {"timestamp": "2026-04-07T09:00:00Z", "step": "status_check",
             "prompt": "Check status", "response": "All systems operational."},
            {"timestamp": "2026-04-07T09:02:00Z", "step": "capture",
             "prompt": "Capture insight", "response": '{"topic":"test","content":"x","tags":[],"category":"resources"}'},
        ],
        lessons_learned=[
            {"type": "success_achieved", "step": "deploy",
             "description": "Deployment completed successfully", "timestamp": "2026-04-07T09:00:00Z"},
        ],
    )


@pytest.fixture
def fresh_brain():
    """A clean BrainState."""
    return BrainState()


@pytest.fixture
def populated_brain():
    """A BrainState with several notes and connections."""
    brain = BrainState()
    add_note(brain, content="Python dataclasses provide default factory support",
             summary="dataclass factories", tags=["python", "patterns"], category="resources")
    add_note(brain, content="AsyncIO event loop should be shared across modules",
             summary="asyncio sharing", tags=["python", "async"], category="areas")
    add_note(brain, content="JSON atomic writes prevent partial reads",
             summary="atomic writes", tags=["reliability", "json"], category="resources")
    connect_notes(brain, "n0001", "n0002", reason="Both are Python patterns")
    return brain


# ═══════════════════════════════════════════════════════════════════════
# 1. State ↔ Brain persistence round-trips
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.state_brain
class TestStateBrainRoundTrip:
    """Test that state and brain survive save → load cycles through real files."""

    def test_state_save_load_roundtrip(self, populated_state, state_file):
        """State survives a save → load cycle with all fields intact."""
        loop = asyncio.get_event_loop()
        loop.run_until_complete(save_state(populated_state, state_file))
        loaded = loop.run_until_complete(load_state(state_file))

        assert loaded.last_heartbeat == populated_state.last_heartbeat
        assert loaded.execution_count == populated_state.execution_count
        assert loaded.memory == populated_state.memory
        assert loaded.context == populated_state.context
        # execution_history is now in SQLite — state field is always empty
        assert loaded.execution_history == []
        assert len(loaded.lessons_learned) == len(populated_state.lessons_learned)

    def test_brain_save_load_roundtrip(self, populated_brain, state_file):
        """Brain survives a save → load cycle with notes and connections intact."""
        asyncio.get_event_loop().run_until_complete(save_brain(populated_brain, state_file))
        loaded = asyncio.get_event_loop().run_until_complete(load_brain(state_file))

        assert len(loaded.notes) == 3
        assert len(loaded.connections) == 1
        assert loaded.capture_count == 3
        assert loaded.notes["n0001"]["summary"] == "dataclass factories"
        assert loaded.connections[0]["reason"] == "Both are Python patterns"

    def test_state_and_brain_coexist_in_same_directory(self, populated_state, populated_brain, state_file):
        """State and brain files are saved to the same directory without conflict."""
        loop = asyncio.get_event_loop()
        loop.run_until_complete(save_state(populated_state, state_file))
        loop.run_until_complete(save_brain(populated_brain, state_file))

        # Both files should exist
        brain_file = _brain_path(state_file)
        assert os.path.isfile(state_file)
        assert os.path.isfile(brain_file)

        # Both should load independently
        loaded_state = loop.run_until_complete(load_state(state_file))
        loaded_brain = loop.run_until_complete(load_brain(state_file))

        assert loaded_state.execution_count == 5
        assert len(loaded_brain.notes) == 3

    def test_state_history_truncation_on_save_reload(self, state_file):
        """Lessons beyond max_history are trimmed on save. execution_history is in SQLite."""
        state = AgentState(
            execution_history=[],
            lessons_learned=[
                {"type": "info", "step": "s", "description": f"lesson_{i}",
                 "timestamp": "2026-04-07T00:00:00Z"}
                for i in range(200)
            ],
        )
        loop = asyncio.get_event_loop()
        loop.run_until_complete(save_state(state, state_file, max_history=50))
        loaded = loop.run_until_complete(load_state(state_file))

        assert loaded.execution_history == []  # always empty in JSON
        assert len(loaded.lessons_learned) == 50
        # Should keep the LAST 50 (most recent)
        assert loaded.lessons_learned[0]["description"] == "lesson_150"

    def test_brain_review_log_truncation(self, state_file):
        """Review log beyond max_reviews is trimmed on save."""
        brain = BrainState(
            review_log=[
                {"timestamp": f"2026-04-{i:02d}T00:00:00Z", "summary": f"review_{i}"}
                for i in range(1, 80)
            ]
        )
        asyncio.get_event_loop().run_until_complete(save_brain(brain, state_file, max_reviews=30))
        loaded = asyncio.get_event_loop().run_until_complete(load_brain(state_file))

        assert len(loaded.review_log) == 30

    def test_brain_unicode_roundtrip(self, state_file):
        """Unicode content in notes survives the save/load cycle."""
        brain = BrainState()
        add_note(brain, content="日本語テスト 🧠 émojis — dashes", summary="Unicode test",
                 tags=["i18n", "测试"], category="resources")

        asyncio.get_event_loop().run_until_complete(save_brain(brain, state_file))
        loaded = asyncio.get_event_loop().run_until_complete(load_brain(state_file))

        note = loaded.notes["n0001"]
        assert "日本語" in note["content"]
        assert "🧠" in note["content"]
        assert "测试" in note["tags"]

    def test_empty_state_and_brain_roundtrip(self, state_file):
        """Default-constructed state and brain survive a round-trip."""
        loop = asyncio.get_event_loop()
        loop.run_until_complete(save_state(AgentState(), state_file))
        loop.run_until_complete(save_brain(BrainState(), state_file))

        loaded_state = asyncio.get_event_loop().run_until_complete(load_state(state_file))
        loaded_brain = asyncio.get_event_loop().run_until_complete(load_brain(state_file))

        assert loaded_state.execution_count == 0
        assert loaded_state.memory == {}
        assert len(loaded_brain.notes) == 0
        assert len(loaded_brain.connections) == 0

    def test_brain_path_derivation_matches_save_location(self, state_file):
        """The brain file ends up exactly where _brain_path says it should."""
        brain = BrainState()
        add_note(brain, content="test")
        asyncio.get_event_loop().run_until_complete(save_brain(brain, state_file))

        expected_path = _brain_path(state_file)
        assert os.path.isfile(expected_path)

        with open(expected_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "n0001" in data["notes"]


# ═══════════════════════════════════════════════════════════════════════
# 2. Brain mutation — add_note / connect / query integration
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.state_brain
class TestBrainMutationChain:
    """Test sequences of brain operations that build on each other."""

    def test_add_connect_query_cycle(self):
        """add_note → connect_notes → get_by_category → get_recent → summary all work together."""
        brain = BrainState()

        # Add notes across categories
        id1 = add_note(brain, content="React hooks simplify state management",
                       summary="hooks intro", tags=["react"], category="resources")
        id2 = add_note(brain, content="Custom hook for form validation pattern",
                       summary="form hooks", tags=["react", "forms"], category="projects")
        id3 = add_note(brain, content="Performance: useMemo for expensive computations",
                       summary="perf tip", tags=["react", "performance"], category="areas")

        # Connect related notes
        connect_notes(brain, id1, id2, reason="Both cover React hooks")
        connect_notes(brain, id2, id3, reason="Form hooks benefit from memoization")

        # Query by category
        resources = get_notes_by_category(brain, "resources")
        assert len(resources) == 1
        assert resources[0]["summary"] == "hooks intro"

        projects = get_notes_by_category(brain, "projects")
        assert len(projects) == 1

        # Get recent (should return all 3 in reverse order)
        recent = get_recent_notes(brain, count=10)
        assert len(recent) == 3
        assert recent[0]["id"] == "n0003"  # most recent first

        # Summary should reflect all content
        summary = build_brain_summary(brain)
        assert "Total notes: 3" in summary
        assert "Total connections: 2" in summary
        assert "resources=1" in summary
        assert "projects=1" in summary
        assert "areas=1" in summary

    def test_connection_creates_bidirectional_links(self):
        """connect_notes updates both notes' connection lists."""
        brain = BrainState()
        id1 = add_note(brain, content="Note A")
        id2 = add_note(brain, content="Note B")
        connect_notes(brain, id1, id2, reason="related")

        assert id2 in brain.notes[id1]["connections"]
        assert id1 in brain.notes[id2]["connections"]

    def test_notes_auto_increment_across_save_reload(self, state_file):
        """Note IDs continue incrementing after a save/load cycle."""
        brain = BrainState()
        add_note(brain, content="First note")
        add_note(brain, content="Second note")
        assert brain.capture_count == 2

        # Save and reload
        asyncio.get_event_loop().run_until_complete(save_brain(brain, state_file))
        loaded = asyncio.get_event_loop().run_until_complete(load_brain(state_file))

        # Add more notes — IDs should continue from n0003
        id3 = add_note(loaded, content="Third note after reload")
        assert id3 == "n0003"
        assert loaded.capture_count == 3


# ═══════════════════════════════════════════════════════════════════════
# 3. Workflow ↔ Brain ↔ State integration
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.workflow
class TestWorkflowBrainIntegration:
    """Test workflow helpers that mutate brain and state with real data."""

    def test_store_capture_valid_json(self, fresh_brain):
        """_store_capture parses valid JSON and creates a real brain note."""
        raw = '{"topic": "Integration testing", "content": "Tests verify cross-module behavior", "tags": ["testing", "quality"], "category": "resources"}'
        note_id = _store_capture(fresh_brain, raw)

        assert note_id is not None
        assert note_id.startswith("n")
        note = fresh_brain.notes[note_id]
        assert note["content"] == "Tests verify cross-module behavior"
        assert note["summary"] == "Integration testing"
        assert "testing" in note["tags"]
        assert note["category"] == "resources"

    def test_store_capture_json_in_code_fence(self, fresh_brain):
        """_store_capture strips markdown code fences before parsing."""
        raw = '```json\n{"topic": "Fenced JSON", "content": "Wrapped in backticks", "tags": [], "category": "resources"}\n```'
        note_id = _store_capture(fresh_brain, raw)

        assert note_id is not None
        assert fresh_brain.notes[note_id]["summary"] == "Fenced JSON"

    def test_store_capture_invalid_json_falls_back(self, fresh_brain):
        """_store_capture falls back to storing raw text when JSON is invalid."""
        raw = "This is not JSON at all, just plain text from the LLM"
        note_id = _store_capture(fresh_brain, raw)

        assert note_id is not None
        # Fallback stores raw content
        assert "This is not JSON" in fresh_brain.notes[note_id]["content"]

    def test_store_connection_valid_json(self, populated_brain):
        """_store_connection parses JSON and creates a real connection."""
        initial_connections = len(populated_brain.connections)
        raw = '{"from": "n0001", "to": "n0003", "reason": "Both deal with Python data handling"}'
        _store_connection(populated_brain, raw)

        assert len(populated_brain.connections) == initial_connections + 1
        new_conn = populated_brain.connections[-1]
        assert new_conn["from"] == "n0001"
        assert new_conn["to"] == "n0003"

    def test_store_connection_invalid_json_no_crash(self, populated_brain):
        """_store_connection silently handles invalid JSON."""
        initial = len(populated_brain.connections)
        _store_connection(populated_brain, "not json")
        assert len(populated_brain.connections) == initial

    def test_store_connection_nonexistent_notes_ignored(self, populated_brain):
        """_store_connection ignores connections to notes that don't exist."""
        initial = len(populated_brain.connections)
        raw = '{"from": "n0001", "to": "n9999", "reason": "Ghost note"}'
        _store_connection(populated_brain, raw)
        assert len(populated_brain.connections) == initial

    def test_run_step_records_history_in_state(self, fresh_state):
        """_run_step records entries in SQLite execution log."""
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=MagicMock(text="Step completed successfully"))

        result = asyncio.get_event_loop().run_until_complete(
            _run_step(mock_agent, "test_step", "Do something", fresh_state)
        )

        assert result == "Step completed successfully"
        log_entries = recent_entries(100)
        assert len(log_entries) == 1
        entry = log_entries[0]
        assert entry["step"] == "test_step"
        assert entry["prompt"] == "Do something"
        assert "timestamp" in entry
        # Should also have extracted lessons (success signal present)
        assert any(l["type"] == "success_achieved" for l in fresh_state.lessons_learned)

    def test_run_step_extracts_error_lessons(self, fresh_state):
        """_run_step extracts error lessons from agent responses containing error signals."""
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(
            return_value=MagicMock(text="Error: failed to connect to database. Traceback follows.")
        )

        result = asyncio.get_event_loop().run_until_complete(
            _run_step(mock_agent, "db_check", "Check database", fresh_state)
        )

        error_lessons = [l for l in fresh_state.lessons_learned if l["type"] == "error_encountered"]
        assert len(error_lessons) >= 1
        assert "db_check" in error_lessons[0]["step"]

    def test_run_step_chains_build_state_history(self, fresh_state):
        """Multiple _run_step calls accumulate in SQLite execution log."""
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=MagicMock(text="ok"))

        loop = asyncio.get_event_loop()
        loop.run_until_complete(_run_step(mock_agent, "step_1", "First", fresh_state))
        loop.run_until_complete(_run_step(mock_agent, "step_2", "Second", fresh_state))
        loop.run_until_complete(_run_step(mock_agent, "step_3", "Third", fresh_state))

        log_entries = recent_entries(100)
        assert len(log_entries) == 3
        assert [e["step"] for e in log_entries] == ["step_1", "step_2", "step_3"]

    def test_full_capture_then_persist_cycle(self, fresh_brain, state_file):
        """Capture a note via workflow helper, then persist to disk and reload."""
        raw = '{"topic": "E2E test", "content": "Full cycle: capture → save → load", "tags": ["e2e"], "category": "projects"}'
        note_id = _store_capture(fresh_brain, raw)
        assert note_id is not None

        # Persist
        asyncio.get_event_loop().run_until_complete(save_brain(fresh_brain, state_file))

        # Reload and verify
        loaded = asyncio.get_event_loop().run_until_complete(load_brain(state_file))
        assert note_id in loaded.notes
        assert loaded.notes[note_id]["summary"] == "E2E test"
        assert loaded.notes[note_id]["tags"] == ["e2e"]


# ═══════════════════════════════════════════════════════════════════════
# 4. Learning ↔ State integration
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.learning
class TestLearningStateIntegration:
    """Test that the learning engine works with real state data."""

    def test_extract_lessons_feeds_into_state(self, fresh_state):
        """Lessons extracted from a response are the same format that state stores."""
        lessons = extract_lessons("deploy", "deploy to prod", "Deployment completed successfully in 2m30s")

        fresh_state.lessons_learned.extend(lessons)
        assert len(fresh_state.lessons_learned) >= 1
        assert fresh_state.lessons_learned[0]["type"] == "success_achieved"
        assert fresh_state.lessons_learned[0]["step"] == "deploy"
        assert "timestamp" in fresh_state.lessons_learned[0]

    def test_extract_lessons_multiple_signals(self):
        """A response with both error and warning signals produces multiple lessons."""
        response = "Error: failed to compile. Warning: deprecated API in use. Traceback shown above."
        lessons = extract_lessons("build", "build project", response)

        types = {l["type"] for l in lessons}
        assert "error_encountered" in types
        assert "warning_noted" in types

    def test_build_context_block_with_populated_state(self, populated_state):
        """build_context_block produces a coherent context string from real state."""
        from execution_log import append_entry
        # Populate SQLite with the history from the populated_state fixture
        for entry in populated_state.execution_history:
            append_entry(entry["step"], entry["prompt"], entry["response"],
                         timestamp=entry["timestamp"])
        populated_state.execution_history = []  # cleared since it's in SQLite

        context = build_context_block(populated_state)

        assert "AGENT MEMORY" in context
        assert "2026-04-07T10:00:00Z" in context  # last heartbeat
        assert "Total executions: 5" in context
        assert "success_achieved" in context  # from lessons
        assert "status_check" in context  # from history in SQLite

    def test_context_block_with_empty_state(self, fresh_state):
        """build_context_block handles a fresh state without crashing."""
        context = build_context_block(fresh_state)

        assert "AGENT MEMORY" in context
        assert "never" in context  # no last heartbeat
        assert "Total executions: 0" in context

    def test_lessons_survive_state_roundtrip(self, populated_state, state_file):
        """Lessons in state persist through a save/load cycle."""
        # Add a fresh lesson
        lessons = extract_lessons("review", "review code", "Warning: unused import detected")
        populated_state.lessons_learned.extend(lessons)

        loop = asyncio.get_event_loop()
        loop.run_until_complete(save_state(populated_state, state_file))
        loaded = loop.run_until_complete(load_state(state_file))

        assert len(loaded.lessons_learned) == len(populated_state.lessons_learned)
        assert any(l["type"] == "warning_noted" for l in loaded.lessons_learned)

    def test_context_block_after_multiple_steps(self, fresh_state):
        """Context block reflects accumulated history from multiple steps."""
        from execution_log import append_entry
        # Simulate 3 heartbeat steps in SQLite
        for i in range(3):
            append_entry(f"step_{i}", f"prompt_{i}", f"response_{i}",
                         timestamp=f"2026-04-07T{10+i}:00:00Z")
        fresh_state.execution_count = 3

        context = build_context_block(fresh_state, max_recent=2)

        # Should show only the 2 most recent
        assert "step_2" in context
        assert "step_1" in context
        # step_0 should be truncated by max_recent=2
        assert "step_0" not in context


# ═══════════════════════════════════════════════════════════════════════
# 5. Persona loader integration (real mcp.json)
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.persona
class TestPersonaLoaderIntegration:
    """Test persona loading against the real mcp.json in the project."""

    def test_load_default_persona(self):
        """Loading 'default' returns a valid Persona with real instructions."""
        persona = load_persona("default")
        assert persona.name == "default"
        assert persona.workflow == "second_brain"
        assert len(persona.instructions) > 0
        assert persona.heartbeat_task != ""

    def test_load_researcher_persona(self):
        """Loading 'researcher' returns second_brain workflow."""
        persona = load_persona("researcher")
        assert persona.name == "researcher"
        assert persona.workflow == "second_brain"
        assert "Research analyst" in persona.description

    def test_load_project_manager_persona(self):
        """Loading 'project_manager' returns freeform workflow with tools."""
        persona = load_persona("project_manager")
        assert persona.name == "project_manager"
        assert persona.workflow == "freeform"
        assert persona.heartbeat_task != ""
        assert len(persona.tools) > 0

    def test_load_react_developer_persona_has_steps(self):
        """Loading 'react_developer' returns steps workflow with defined steps."""
        persona = load_persona("react_developer")
        assert persona.name == "react_developer"
        assert persona.workflow == "steps"
        assert persona.steps is not None
        assert len(persona.steps) >= 3
        step_names = [s["name"] for s in persona.steps]
        assert "status_check" in step_names

    def test_load_react_site_builder_is_stepwise(self):
        """react_site_builder has stepwise=True for one-step-per-heartbeat execution."""
        persona = load_persona("react_site_builder")
        assert persona.stepwise is True
        assert persona.workflow == "steps"
        assert len(persona.steps) == 7  # plan through style_and_build

    def test_load_devops_has_mcp_servers(self):
        """devops_engineer persona has Docker MCP server attached."""
        persona = load_persona("devops_engineer")
        assert persona.mcp_servers is not None
        assert "docker" in persona.mcp_servers
        assert persona.mcp_servers["docker"]["command"] == "docker"

    def test_load_python_developer_has_filtered_tools(self):
        """python_developer loads only the specified function subset."""
        persona = load_persona("python_developer")
        tool_names = [t.__name__ for t in persona.tools]
        assert "list_files" in tool_names
        assert "read_source_file" in tool_names
        assert "write_source_file" in tool_names
        assert "run_shell_command" in tool_names

    def test_load_nonexistent_falls_back_to_default(self):
        """Loading a persona that doesn't exist falls back to 'default'."""
        persona = load_persona("nonexistent_persona_xyz")
        assert persona.name == "default"

    def test_list_personas_includes_all_defined(self):
        """list_personas returns all personas from mcp.json."""
        names = list_personas()
        assert "default" in names
        assert "react_developer" in names
        assert "python_developer" in names
        assert "devops_engineer" in names
        assert "project_manager" in names
        assert "researcher" in names

    def test_persona_instructions_come_from_markdown_files(self):
        """Persona instructions are loaded from the referenced .md files."""
        persona = load_persona("python_developer")
        # Instructions should be non-trivial (from the actual .md file)
        assert len(persona.instructions) > 50
        # Should contain markdown-like content
        assert any(c in persona.instructions for c in ["#", "-", "**", "1."])


# ═══════════════════════════════════════════════════════════════════════
# 6. Config integration
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.config
class TestConfigIntegration:
    """Test config loading from environment variables."""

    def test_load_config_defaults(self):
        """load_config with no env vars returns sensible defaults."""
        with patch.dict(os.environ, {}, clear=True):
            # load_config calls load_dotenv which may read .env — isolate it
            with patch("config.load_dotenv"):
                config = load_config()

        assert config.provider == "copilot"
        assert config.heartbeat_interval_sec == 120
        assert config.state_file == "data/agent_state.json"
        assert config.max_history == 100
        assert config.agent_name == "NATLClaw"
        assert config.persona == "default"

    def test_load_config_from_env_vars(self):
        """load_config respects environment variable overrides."""
        env = {
            "PROVIDER": "foundry",
            "AZURE_AI_MODEL_DEPLOYMENT_NAME": "gpt-4o",
            "AZURE_AI_PROJECT_ENDPOINT": "https://test.endpoint.com",
            "HEARTBEAT_INTERVAL_SEC": "60",
            "STATE_FILE": "custom/state.json",
            "MAX_HISTORY": "50",
            "AGENT_NAME": "TestClaw",
            "PERSONA": "researcher",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("config.load_dotenv"):
                config = load_config()

        assert config.provider == "foundry"
        assert config.model == "gpt-4o"
        assert config.project_endpoint == "https://test.endpoint.com"
        assert config.heartbeat_interval_sec == 60
        assert config.state_file == "custom/state.json"
        assert config.max_history == 50
        assert config.agent_name == "TestClaw"
        assert config.persona == "researcher"

    def test_config_model_priority(self):
        """Model selection follows priority: GITHUB_COPILOT_MODEL > AZURE > OPENAI > OLLAMA."""
        with patch.dict(os.environ, {
            "GITHUB_COPILOT_MODEL": "claude-sonnet-4",
            "AZURE_AI_MODEL_DEPLOYMENT_NAME": "gpt-4o",
            "OPENAI_MODEL": "gpt-4-turbo",
        }, clear=True):
            with patch("config.load_dotenv"):
                config = load_config()
        assert config.model == "claude-sonnet-4"

        # Remove copilot model — should fall to Azure
        with patch.dict(os.environ, {
            "AZURE_AI_MODEL_DEPLOYMENT_NAME": "gpt-4o",
            "OPENAI_MODEL": "gpt-4-turbo",
        }, clear=True):
            with patch("config.load_dotenv"):
                config = load_config()
        assert config.model == "gpt-4o"


# ═══════════════════════════════════════════════════════════════════════
# 7. Cross-module end-to-end flows
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.e2e
class TestEndToEnd:
    """Test complete flows that cross multiple module boundaries."""

    def test_full_heartbeat_data_flow(self, fresh_state, fresh_brain, state_file):
        """Simulate one heartbeat cycle: step → capture → connect → persist → reload."""
        # Step 1: Simulate agent producing a capture response
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=MagicMock(
            text='{"topic": "E2E insight", "content": "Integration tests catch bugs unit tests miss", "tags": ["testing"], "category": "resources"}'
        ))

        loop = asyncio.get_event_loop()

        # Run a step (records in state history)
        result = loop.run_until_complete(
            _run_step(mock_agent, "capture", "Capture an insight", fresh_state)
        )
        assert total_count() == 1

        # Store the capture in the brain
        note_id = _store_capture(fresh_brain, result)
        assert note_id is not None

        # Add a second note so we can connect them
        id2 = add_note(fresh_brain, content="Unit tests are fast but narrow",
                       summary="unit test limits", tags=["testing"], category="resources")

        # Connect notes
        _store_connection(fresh_brain, json.dumps({
            "from": note_id, "to": id2, "reason": "Both discuss testing strategies"
        }))

        # Build summary (used for next prompt injection)
        summary = build_brain_summary(fresh_brain)
        assert "Total notes: 2" in summary
        assert "Total connections: 1" in summary

        # Build context block (used for next prompt injection)
        context = build_context_block(fresh_state)
        assert "capture" in context

        # Persist everything
        loop.run_until_complete(save_state(fresh_state, state_file))
        loop.run_until_complete(save_brain(fresh_brain, state_file))

        # Reload and verify
        loaded_state = loop.run_until_complete(load_state(state_file))
        loaded_brain = loop.run_until_complete(load_brain(state_file))

        log_entries = recent_entries(100)
        assert log_entries[0]["step"] == "capture"
        assert len(loaded_brain.notes) == 2
        assert len(loaded_brain.connections) == 1

    def test_persona_brain_summary_integration(self):
        """Brain summary accurately reflects notes added by different 'personas'."""
        brain = BrainState()

        # Simulate notes from different personas
        add_note(brain, content="Docker health check passed",
                 summary="docker ok", tags=["devops"], category="areas", source="heartbeat")
        add_note(brain, content="Found unused import in auth.py",
                 summary="code lint", tags=["python", "quality"], category="projects", source="heartbeat")
        add_note(brain, content="React 19 release notes reviewed",
                 summary="react 19", tags=["react", "frontend"], category="resources", source="heartbeat")

        summary = build_brain_summary(brain)
        assert "Total notes: 3" in summary
        assert "areas=1" in summary
        assert "projects=1" in summary
        assert "resources=1" in summary
        # Recent notes should appear
        assert "react 19" in summary or "React 19" in summary

    def test_state_accumulates_across_simulated_heartbeats(self, state_file):
        """State grows across multiple save/load cycles (simulating multiple heartbeats)."""
        from execution_log import append_entry
        loop = asyncio.get_event_loop()

        for heartbeat_num in range(1, 4):
            state = loop.run_until_complete(load_state(state_file))
            state.execution_count = heartbeat_num
            state.last_heartbeat = f"2026-04-07T{10 + heartbeat_num}:00:00Z"
            append_entry(f"heartbeat_{heartbeat_num}", "p", "r",
                         timestamp=state.last_heartbeat)
            loop.run_until_complete(save_state(state, state_file))

        # Final reload should reflect all 3 heartbeats
        final = loop.run_until_complete(load_state(state_file))
        assert final.execution_count == 3
        log_entries = recent_entries(100)
        assert len(log_entries) == 3
        assert log_entries[-1]["step"] == "heartbeat_3"

    def test_brain_accumulates_across_simulated_heartbeats(self, state_file):
        """Brain grows across multiple save/load cycles."""
        loop = asyncio.get_event_loop()

        for i in range(1, 4):
            brain = loop.run_until_complete(load_brain(state_file))
            add_note(brain, content=f"Insight from heartbeat {i}",
                     summary=f"hb{i}", tags=[f"cycle_{i}"])
            loop.run_until_complete(save_brain(brain, state_file))

        final = loop.run_until_complete(load_brain(state_file))
        assert len(final.notes) == 3
        assert final.capture_count == 3


# ═══════════════════════════════════════════════════════════════════════
# 8. Scheduler — retry decorator integration
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.scheduler
class TestRetryDecorator:
    """Test the retry decorator with real async functions."""

    def test_retry_succeeds_on_first_try(self):
        """A function that succeeds immediately is called once."""
        call_count = 0

        async def good_fn():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = asyncio.get_event_loop().run_until_complete(
            retry(max_attempts=3)(good_fn)()
        )
        assert result == "ok"
        assert call_count == 1

    def test_retry_recovers_from_transient_error(self):
        """Function fails with OSError once, then succeeds — retry saves it."""
        call_count = 0

        async def flaky_fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise OSError("disk busy")
            return "recovered"

        result = asyncio.get_event_loop().run_until_complete(
            retry(max_attempts=3, delay=0.01)(flaky_fn)()
        )
        assert result == "recovered"
        assert call_count == 3

    def test_retry_exhausts_all_attempts(self):
        """Function always fails — raises RuntimeError after all attempts."""
        call_count = 0

        async def always_fails():
            nonlocal call_count
            call_count += 1
            raise IOError("permanent failure")

        with pytest.raises(RuntimeError, match="All 3 attempts failed"):
            asyncio.get_event_loop().run_until_complete(
                retry(max_attempts=3, delay=0.01)(always_fails)()
            )
        assert call_count == 3

    def test_retry_does_not_retry_non_transient(self):
        """Non-transient errors (ValueError etc.) are raised immediately."""
        call_count = 0

        async def bad_logic():
            nonlocal call_count
            call_count += 1
            raise ValueError("bad input")

        with pytest.raises(ValueError, match="bad input"):
            asyncio.get_event_loop().run_until_complete(
                retry(max_attempts=3, delay=0.01)(bad_logic)()
            )
        assert call_count == 1  # no retries

    def test_retry_handles_timeout_error(self):
        """asyncio.TimeoutError is treated as transient and retried."""
        call_count = 0

        async def timeout_fn():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise asyncio.TimeoutError()
            return "ok after timeout"

        result = asyncio.get_event_loop().run_until_complete(
            retry(max_attempts=3, delay=0.01)(timeout_fn)()
        )
        assert result == "ok after timeout"
        assert call_count == 2

    def test_retry_with_real_load_state(self, state_file):
        """retry wrapping load_state — loads from real file with retry wrapper."""
        # Save a real state first
        state = AgentState(execution_count=42)
        asyncio.get_event_loop().run_until_complete(save_state(state, state_file))

        # Load it through the retry decorator (same pattern as run_scheduler)
        result = asyncio.get_event_loop().run_until_complete(
            retry()(load_state)(state_file)
        )
        assert result.execution_count == 42

    def test_retry_with_real_save_load_brain(self, state_file):
        """retry wrapping save_brain + load_brain — full cycle with retry."""
        brain = BrainState()
        add_note(brain, content="Retry test note", summary="retry test")

        loop = asyncio.get_event_loop()
        loop.run_until_complete(retry()(save_brain)(brain, state_file))

        loaded = loop.run_until_complete(retry()(load_brain)(state_file))
        assert len(loaded.notes) == 1
        assert loaded.notes["n0001"]["summary"] == "retry test"


# ═══════════════════════════════════════════════════════════════════════
# 9. Full workflow mode integration
# ═══════════════════════════════════════════════════════════════════════

def _make_agent(*responses):
    """Create a mock agent that returns responses in sequence."""
    agent = MagicMock()
    side_effects = [MagicMock(text=r) for r in responses]
    agent.run = AsyncMock(side_effect=side_effects)
    return agent


@pytest.mark.workflow
class TestSecondBrainWorkflow:
    """Test the complete second_brain workflow mode with mock agent."""

    def test_second_brain_full_cycle(self, state_file):
        """Run the full 4-step second_brain heartbeat and verify mutations."""
        state = AgentState(execution_count=1, last_heartbeat="2026-04-07T10:00:00Z")
        brain = BrainState()
        # Seed the brain with 2 notes so the connect step fires
        add_note(brain, content="Existing note 1", summary="note1", tags=["test"])
        add_note(brain, content="Existing note 2", summary="note2", tags=["test"])

        config = AppConfig(agent_name="TestClaw", persona="default")
        persona = load_persona("default")

        # Mock agent: status → capture JSON → connect JSON → review
        agent = _make_agent(
            "System is operational. Knowledge base growing steadily.",
            '{"topic": "Workflow insight", "content": "Integration tests catch cross-module bugs", "tags": ["testing"], "category": "resources"}',
            '{"from": "n0001", "to": "n0002", "reason": "Both are test notes"}',
            "Heartbeat complete. Brain is expanding nicely.",
        )

        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            _run_second_brain_heartbeat(agent, state, brain, config, persona)
        )

        # Verify state: 4 steps recorded in SQLite
        log_entries = recent_entries(100)
        assert len(log_entries) == 4
        steps = [e["step"] for e in log_entries]
        assert steps == ["status_check", "capture", "connect", "review"]

        # Verify brain: original 2 + 1 captured = 3 notes
        assert len(brain.notes) == 3
        assert brain.notes["n0003"]["summary"] == "Workflow insight"

        # Verify review log updated
        assert len(brain.review_log) == 1
        assert brain.last_review is not None

    def test_second_brain_persists_after_heartbeat(self, state_file):
        """State and brain survive a save/load after a full second_brain heartbeat."""
        state = AgentState(execution_count=1)
        brain = BrainState()
        config = AppConfig(agent_name="TestClaw", state_file=state_file, persona="default")
        persona = load_persona("default")

        agent = _make_agent(
            "All good.",
            '{"topic": "Persist test", "content": "Data survives", "tags": ["persistence"], "category": "resources"}',
            "no connection found",
            "Heartbeat done.",
        )

        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            _run_second_brain_heartbeat(agent, state, brain, config, persona)
        )

        # Persist like run_scheduler does
        loop.run_until_complete(save_state(state, state_file, config.max_history))
        loop.run_until_complete(save_brain(brain, state_file))

        # Reload and verify
        loaded_state = loop.run_until_complete(load_state(state_file))
        loaded_brain = loop.run_until_complete(load_brain(state_file))

        # 3 steps: status, capture, review (connect skipped — brain has < 2 notes)
        assert total_count() == 3
        assert len(loaded_brain.notes) == 1
        assert loaded_brain.notes["n0001"]["summary"] == "Persist test"


@pytest.mark.workflow
class TestFreeformWorkflow:
    """Test the freeform workflow mode."""

    def test_freeform_full_cycle(self):
        """Run freeform heartbeat: status → task → capture → review."""
        state = AgentState(execution_count=1)
        brain = BrainState()
        config = AppConfig(agent_name="TestClaw", persona="project_manager")
        persona = load_persona("project_manager")

        agent = _make_agent(
            "Project status: on track with 3 open tasks.",
            "Reviewed all Jira tickets. Found 2 blockers that need immediate attention.",
            '{"topic": "PM insight", "content": "Blockers should be escalated within 24h", "tags": ["process"], "category": "areas"}',
            "Heartbeat complete. Will follow up on blockers next cycle.",
        )

        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            _run_freeform_heartbeat(agent, state, brain, config, persona)
        )

        # 4 steps: status_check, task, capture, review
        log_entries = recent_entries(100)
        assert len(log_entries) == 4
        steps = [e["step"] for e in log_entries]
        assert steps == ["status_check", "task", "capture", "review"]

        # Brain got the captured note
        assert len(brain.notes) == 1
        assert brain.notes["n0001"]["summary"] == "PM insight"
        assert brain.last_review is not None


@pytest.mark.workflow
class TestStepsWorkflow:
    """Test the steps workflow mode — both all-at-once and stepwise."""

    def test_steps_all_at_once(self):
        """steps workflow with stepwise=False runs all steps in one heartbeat."""
        state = AgentState(execution_count=1)
        brain = BrainState()
        config = AppConfig(agent_name="TestClaw", persona="react_developer")
        persona = load_persona("react_developer")

        # react_developer has steps; need one response per step
        num_steps = len(persona.steps)
        responses = [f"Step {i+1} complete." for i in range(num_steps)]
        # For steps with storeToBrain=True, the agent is called again for
        # the distil prompt — add extra responses for those
        store_steps = sum(1 for s in persona.steps if s.get("storeToBrain", False))
        # Each storeToBrain step needs: the step response + a JSON distil response
        for _ in range(store_steps):
            responses.append('{"topic": "distilled", "content": "insight", "tags": [], "category": "resources"}')

        agent = _make_agent(*responses)

        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            _run_steps_heartbeat(agent, state, brain, config, persona)
        )

        # All steps should be in execution log
        assert total_count() >= num_steps

    def test_stepwise_one_per_heartbeat(self):
        """stepwise=True runs exactly one step per heartbeat."""
        state = AgentState(execution_count=1)
        brain = BrainState()
        config = AppConfig(agent_name="TestClaw", persona="react_site_builder")
        persona = load_persona("react_site_builder")

        assert persona.stepwise is True
        total = len(persona.steps)

        loop = asyncio.get_event_loop()

        # Run 3 heartbeats — each should advance by one step
        for hb in range(3):
            # storeToBrain may add extra agent calls; provide enough responses
            agent = _make_agent(
                f"Step {hb+1} result.",
                '{"topic": "step insight", "content": "learned something", "tags": [], "category": "resources"}',
            )
            loop.run_until_complete(
                _run_steps_heartbeat(agent, state, brain, config, persona)
            )
            # The step index should have advanced
            idx_key = f"steps_{persona.name}_idx"
            assert state.context[idx_key] == hb + 1

        # After 3 heartbeats, 3 steps executed
        # Each step produces at least 1 history entry
        assert total_count() >= 3

    def test_stepwise_resets_after_completion(self):
        """After all stepwise steps complete, index resets to 0."""
        state = AgentState(execution_count=1)
        brain = BrainState()
        config = AppConfig(agent_name="TestClaw", persona="react_site_builder")
        persona = load_persona("react_site_builder")

        total = len(persona.steps)
        loop = asyncio.get_event_loop()

        # Set the index to the last step
        idx_key = f"steps_{persona.name}_idx"
        state.context[idx_key] = total  # already past last step

        agent = _make_agent("done")
        loop.run_until_complete(
            _run_steps_heartbeat(agent, state, brain, config, persona)
        )

        # Should have reset
        assert state.context[idx_key] == 0

    def test_steps_missing_falls_back_to_freeform(self):
        """Persona with workflow=steps but no steps defined falls back to freeform."""
        state = AgentState(execution_count=1)
        brain = BrainState()
        config = AppConfig(agent_name="TestClaw")

        # Create a persona with steps workflow but empty steps list
        persona = Persona(
            name="empty_steps",
            workflow="steps",
            description="test persona",
            heartbeat_task="do something",
            instructions="You are a test agent.",
            steps=[],
        )

        agent = _make_agent(
            "Status ok.",
            "Did the task.",
            '{"topic": "fallback", "content": "fell back to freeform", "tags": [], "category": "resources"}',
            "Review done.",
        )

        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            _run_steps_heartbeat(agent, state, brain, config, persona)
        )

        # Should have executed freeform (4 steps)
        log_entries = recent_entries(100)
        assert len(log_entries) == 4
        steps = [e["step"] for e in log_entries]
        assert "task" in steps  # freeform-specific step name


@pytest.mark.workflow
class TestDistilToBrain:
    """Test the _distil_to_brain helper that stores step output as brain notes."""

    def test_distil_stores_valid_note(self):
        """_distil_to_brain stores a note when agent returns valid JSON."""
        state = AgentState()
        brain = BrainState()
        agent = _make_agent(
            '{"topic": "Distilled insight", "content": "Step produced useful finding", "tags": ["distil"], "category": "resources"}'
        )

        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            _distil_to_brain(agent, state, brain, "test_step", "original output text")
        )

        assert len(brain.notes) == 1
        assert brain.notes["n0001"]["summary"] == "Distilled insight"
        # _distil_to_brain calls _run_step internally, recording in SQLite log
        log_entries = recent_entries(100)
        assert len(log_entries) == 1
        assert log_entries[0]["step"] == "test_step_capture"

    def test_distil_handles_bad_json_gracefully(self):
        """_distil_to_brain handles non-JSON agent output without crashing."""
        state = AgentState()
        brain = BrainState()
        agent = _make_agent("I couldn't produce JSON for this one, sorry.")

        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            _distil_to_brain(agent, state, brain, "test_step", "some output")
        )

        # Should still store a fallback note
        assert len(brain.notes) == 1
        assert "couldn't produce JSON" in brain.notes["n0001"]["content"]


# ═══════════════════════════════════════════════════════════════════════
# 10. Scheduler loop integration (single iteration)
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.scheduler
class TestSchedulerLoop:
    """Test run_scheduler by breaking out of the loop after one iteration."""

    def test_scheduler_single_heartbeat(self, state_file):
        """Simulate one iteration of run_scheduler with real state/brain persistence."""
        from scheduler import run_scheduler

        config = AppConfig(
            provider="copilot",
            model="test-model",
            agent_name="TestClaw",
            persona="default",
            state_file=state_file,
            heartbeat_interval_sec=1,
        )

        # Build a mock agent that acts as an async context manager (copilot provider)
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(side_effect=[
            MagicMock(text="Status looks good."),
            MagicMock(text='{"topic": "scheduler test", "content": "Scheduler works end-to-end", "tags": ["scheduler"], "category": "resources"}'),
            MagicMock(text="No connections found."),
            MagicMock(text="Heartbeat summary: all good."),
        ])
        mock_agent.__aenter__ = AsyncMock(return_value=mock_agent)
        mock_agent.__aexit__ = AsyncMock(return_value=False)

        loop = asyncio.get_event_loop()

        # Use max_iterations to stop cleanly after 1 heartbeat; also mock sleep as safety net
        with patch("scheduler.create_agent", return_value=mock_agent):
            with patch("scheduler.asyncio.sleep", new_callable=AsyncMock):
                loop.run_until_complete(run_scheduler(config, max_iterations=1))

        # After one full iteration, state and brain should be persisted.
        loaded_state = loop.run_until_complete(load_state(state_file))
        loaded_brain = loop.run_until_complete(load_brain(state_file))

        assert loaded_state.execution_count == 1
        # 3 steps: status, capture, review (connect skipped — brain starts empty, < 2 notes)
        assert total_count() == 3
        assert loaded_brain.notes.get("n0001") is not None
        assert loaded_brain.notes["n0001"]["summary"] == "scheduler test"
    def test_scheduler_persists_state_even_on_heartbeat_error(self, state_file):
        """State and brain are saved even when the heartbeat raises an error."""
        from scheduler import run_scheduler

        config = AppConfig(
            provider="foundry",  # non-copilot — no async context manager
            model="test-model",
            agent_name="TestClaw",
            persona="default",
            state_file=state_file,
            heartbeat_interval_sec=1,
        )

        # Agent that fails on the first step
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(side_effect=Exception("LLM provider down"))

        loop = asyncio.get_event_loop()

        with patch("scheduler.create_agent", return_value=mock_agent):
            with patch("scheduler.asyncio.sleep", new_callable=AsyncMock):
                loop.run_until_complete(run_scheduler(config, max_iterations=1))

        # State should still be persisted (finally block)
        loaded_state = loop.run_until_complete(load_state(state_file))
        assert loaded_state.execution_count == 1  # incremented before heartbeat
        assert loaded_state.last_heartbeat is not None


# ═══════════════════════════════════════════════════════════════════════
# 11. run_heartbeat dispatcher integration
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.workflow
class TestHeartbeatDispatcher:
    """Test that run_heartbeat correctly dispatches to the right workflow mode."""

    def test_dispatches_to_second_brain(self):
        """Persona with workflow='second_brain' routes to the second_brain heartbeat."""
        state = AgentState(execution_count=1)
        brain = BrainState()
        config = AppConfig(agent_name="TestClaw")
        persona = load_persona("default")
        assert persona.workflow == "second_brain"

        agent = _make_agent("ok", '{"topic":"t","content":"c","tags":[],"category":"resources"}', "no conn", "done")
        loop = asyncio.get_event_loop()
        loop.run_until_complete(run_heartbeat(agent, state, brain, config, persona))

        # second_brain mode: status_check, capture, connect, review
        log_steps = [e["step"] for e in recent_entries(100)]
        assert "status_check" in log_steps
        assert "capture" in log_steps

    def test_dispatches_to_freeform(self):
        """Persona with workflow='freeform' routes to the freeform heartbeat."""
        state = AgentState(execution_count=1)
        brain = BrainState()
        config = AppConfig(agent_name="TestClaw")
        persona = load_persona("project_manager")
        assert persona.workflow == "freeform"

        agent = _make_agent("ok", "task done", '{"topic":"t","content":"c","tags":[],"category":"resources"}', "review")
        loop = asyncio.get_event_loop()
        loop.run_until_complete(run_heartbeat(agent, state, brain, config, persona))

        # freeform mode: status_check, task, capture, review
        log_steps = [e["step"] for e in recent_entries(100)]
        assert "task" in log_steps  # freeform-specific

    def test_dispatches_to_steps(self):
        """Persona with workflow='steps' routes to the steps heartbeat."""
        state = AgentState(execution_count=1)
        brain = BrainState()
        config = AppConfig(agent_name="TestClaw")
        persona = load_persona("react_developer")
        assert persona.workflow == "steps"

        num_steps = len(persona.steps)
        store_count = sum(1 for s in persona.steps if s.get("storeToBrain", False))
        responses = [f"Step {i} done" for i in range(num_steps + store_count)]
        for _ in range(store_count):
            responses.append('{"topic":"d","content":"d","tags":[],"category":"resources"}')

        agent = _make_agent(*responses)
        loop = asyncio.get_event_loop()
        loop.run_until_complete(run_heartbeat(agent, state, brain, config, persona))

        # Steps mode runs the persona-defined steps
        assert total_count() >= num_steps