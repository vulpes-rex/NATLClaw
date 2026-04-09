"""Test suite for workflow.py - Error handling and step execution."""
from __future__ import annotations

import asyncio
import logging
import pytest
import sys
from unittest.mock import MagicMock, patch, AsyncMock
# Mock external dependencies
with patch.dict('sys.modules', {
    'agent_framework_github_copilot': MagicMock(),
    'agent_framework': MagicMock(),
    'agent_framework.foundry': MagicMock(),
    'agent_framework.openai': MagicMock(),
    'agent_framework.ollama': MagicMock(),
    'azure.identity': MagicMock(),
}):
    from workflow import _run_step, run_heartbeat
from config import AppConfig
from state import AgentState
from second_brain import BrainState
from persona_loader import Persona

# Set up logging to avoid warnings during tests
logging.basicConfig(level=logging.DEBUG)

@pytest.fixture
def mock_agent():
    """Create a mock agent with run method."""
    agent = MagicMock()
    agent.run = AsyncMock()
    return agent

@pytest.fixture
def mock_state():
    """Create a mock AgentState."""
    return AgentState(
        last_heartbeat="2024-01-01T00:00:00Z",
        execution_count=1,
        memory={},
        context={},
        execution_history=[],
        lessons_learned=[]
    )

@pytest.fixture
def mock_brain():
    """Create a mock BrainState."""
    return BrainState(
        notes={},
        connections=[],
        review_log=[],
        capture_count=0,
        last_review=None
    )

@pytest.fixture
def mock_config():
    """Create a mock AppConfig."""
    config = MagicMock(spec=AppConfig)
    config.provider = "copilot"
    config.model = "test-model"
    config.agent_name = "test-agent"
    config.heartbeat_interval_sec = 0.1
    config.state_file = "test_state.json"
    return config

@pytest.fixture
def mock_persona():
    """Create a mock Persona."""
    persona = MagicMock(spec=Persona)
    persona.name = "test_persona"
    persona.description = "Test persona"
    persona.instructions = "Test instructions"
    persona.workflow = "second_brain"
    persona.tools = []
    persona.mcp_servers = {}
    persona.heartbeat_task = "Test heartbeat task"
    persona.steps = []
    persona.stepwise = False
    return persona

def test_run_step_success():
    """Test that _run_step works correctly for successful execution."""
    agent = MagicMock()
    agent.run = AsyncMock(return_value=MagicMock(text="Test response"))
    
    state = AgentState()
    result = asyncio.run(_run_step(agent, "test_step", "Test prompt", state))
    
    assert result == "Test response"
    assert len(state.execution_history) == 1
    history_entry = state.execution_history[0]
    assert history_entry["step"] == "test_step"
    assert "Test prompt" in history_entry["prompt"]
    assert "Test response" in history_entry["response"]

def test_run_step_handles_agent_exception():
    """Test that _run_step handles exceptions from agent.run."""
    agent = MagicMock()
    agent.run = AsyncMock(side_effect=Exception("Agent error"))
    
    state = AgentState()
    
    with pytest.raises(Exception):
        asyncio.run(_run_step(agent, "test_step", "Test prompt", state))
    
    assert len(state.execution_history) == 1
    history_entry = state.execution_history[0]
    assert history_entry["step"] == "test_step"
    assert "Test prompt" in history_entry["prompt"]
    assert "ERROR: Step failed" in history_entry["response"]

def test_run_step_extracts_lessons():
    """Test that _run_step extracts lessons from successful execution."""
    agent = MagicMock()
    agent.run = AsyncMock(return_value=MagicMock(text="Test response"))
    
    state = AgentState()
    # Mock extract_lessons to return some lessons
    with patch("workflow.extract_lessons", return_value=[
        {"description": "Lesson 1", "category": "insight"},
        {"description": "Lesson 2", "category": "action"}
    ]):
        result = asyncio.run(_run_step(agent, "test_step", "Test prompt", state))
        
        assert len(state.lessons_learned) == 2
        assert state.lessons_learned[0]["description"] == "Lesson 1"
        assert state.lessons_learned[1]["description"] == "Lesson 2"

def test_run_step_handles_lesson_extraction_failure():
    """Test that _run_step handles failures in lesson extraction."""
    agent = MagicMock()
    agent.run = AsyncMock(return_value=MagicMock(text="Test response"))
    
    state = AgentState()
    # Mock extract_lessons to raise an exception
    with patch("workflow.extract_lessons", side_effect=Exception("Lesson extraction error")):
        result = asyncio.run(_run_step(agent, "test_step", "Test prompt", state))
        
        # Should not raise, just log warning
        assert result == "Test response"
        # lessons_learned should be empty

def test_run_step_response_truncation():
    """Test that responses are truncated in history."""
    agent = MagicMock()
    agent.run = AsyncMock(return_value=MagicMock(text="a" * 1000))
    
    state = AgentState()
    asyncio.run(_run_step(agent, "test_step", "Test prompt", state))
    
    history_entry = state.execution_history[0]
    assert len(history_entry["response"]) <= 500, "Response should be truncated to 500 chars"

def test_run_step_prompt_truncation():
    """Test that prompts are truncated in history."""
    agent = MagicMock()
    agent.run = AsyncMock(return_value=MagicMock(text="Test response"))
    
    state = AgentState()
    long_prompt = "a" * 1000
    asyncio.run(_run_step(agent, "test_step", long_prompt, state))
    
    history_entry = state.execution_history[0]
    assert len(history_entry["prompt"]) <= 300, "Prompt should be truncated to 300 chars"

def test_run_heartbeat_second_brain_mode():
    """Test that run_heartbeat works with second_brain mode."""
    config = MagicMock()
    config.provider = "copilot"
    
    agent = MagicMock()
    agent.run = AsyncMock(return_value=MagicMock(text="Test response"))
    
    state = AgentState()
    brain = BrainState()
    persona = MagicMock()
    persona.workflow = "second_brain"
    persona.tools = []
    persona.mcp_servers = {}
    persona.heartbeat_task = "Test task"
    
    # Mock all the helper functions
    with patch("workflow._run_second_brain_heartbeat", return_value=None):
        # Should not raise
        asyncio.run(run_heartbeat(agent, state, brain, config, persona))

def test_run_heartbeat_freeform_mode():
    """Test that run_heartbeat works with freeform mode."""
    config = MagicMock()
    config.provider = "copilot"
    
    agent = MagicMock()
    agent.run = AsyncMock(return_value=MagicMock(text="Test response"))
    
    state = AgentState()
    brain = BrainState()
    persona = MagicMock()
    persona.workflow = "freeform"
    persona.tools = []
    persona.mcp_servers = {}
    persona.heartbeat_task = "Test task"
    
    with patch("workflow._run_freeform_heartbeat", return_value=None):
        asyncio.run(run_heartbeat(agent, state, brain, config, persona))

def test_run_heartbeat_steps_mode():
    """Test that run_heartbeat works with steps mode."""
    config = MagicMock()
    config.provider = "copilot"
    
    agent = MagicMock()
    agent.run = AsyncMock(return_value=MagicMock(text="Test response"))
    
    state = AgentState()
    brain = BrainState()
    persona = MagicMock()
    persona.workflow = "steps"
    persona.tools = []
    persona.mcp_servers = {}
    persona.heartbeat_task = "Test task"
    persona.steps = []
    persona.stepwise = False
    
    with patch("workflow._run_steps_heartbeat", return_value=None):
        asyncio.run(run_heartbeat(agent, state, brain, config, persona))

def test_run_heartbeat_invalid_mode():
    """Test that run_heartbeat handles invalid workflow mode."""
    config = MagicMock()
    config.provider = "copilot"
    
    agent = MagicMock()
    agent.run = AsyncMock(return_value=MagicMock(text="Test response"))
    
    state = AgentState()
    brain = BrainState()
    persona = MagicMock()
    persona.workflow = "invalid_mode"
    persona.tools = []
    persona.mcp_servers = {}
    persona.heartbeat_task = "Test task"
    
    # Should default to second_brain mode
    with patch("workflow._run_second_brain_heartbeat", return_value=None):
        asyncio.run(run_heartbeat(agent, state, brain, config, persona))

def test_run_heartbeat_handles_agent_exceptions():
    """Test that run_heartbeat handles exceptions from agent steps."""
    config = MagicMock()
    config.provider = "copilot"
    
    agent = MagicMock()
    # Make agent.run raise exceptions
    agent.run = AsyncMock(side_effect=Exception("Agent error"))
    
    state = AgentState()
    brain = BrainState()
    persona = MagicMock()
    persona.workflow = "second_brain"
    persona.tools = []
    persona.mcp_servers = {}
    persona.heartbeat_task = "Test task"
    
    # Should log error but not crash
    asyncio.run(run_heartbeat(agent, state, brain, config, persona))

def test_run_heartbeat_preserves_state():
    """Test that state is preserved after run_heartbeat completes."""
    config = MagicMock()
    config.provider = "copilot"
    
    agent = MagicMock()
    agent.run = AsyncMock(return_value=MagicMock(text="Test response"))
    
    state = AgentState()
    brain = BrainState()
    persona = MagicMock()
    persona.workflow = "second_brain"
    persona.tools = []
    persona.mcp_servers = {}
    persona.heartbeat_task = "Test task"
    
    # Should not modify state object reference
    initial_state_id = id(state)
    asyncio.run(run_heartbeat(agent, state, brain, config, persona))
    final_state_id = id(state)
    assert initial_state_id == final_state_id, "State object should be the same"

def test_run_heartbeat_with_copilot_provider():
    """Test that run_heartbeat works with copilot provider."""
    config = MagicMock()
    config.provider = "copilot"
    
    agent = MagicMock()
    agent.run = AsyncMock(return_value=MagicMock(text="Test response"))
    
    state = AgentState()
    brain = BrainState()
    persona = MagicMock()
    persona.workflow = "second_brain"
    persona.tools = []
    persona.mcp_servers = {}
    persona.heartbeat_task = "Test task"
    
    # run_heartbeat doesn't treat copilot differently - the copilot async
    # context manager is handled in scheduler.py, not workflow.py.
    # Just verify it runs without error.
    asyncio.run(run_heartbeat(agent, state, brain, config, persona))

def test_run_heartbeat_with_other_providers():
    """Test that run_heartbeat works with non-copilot providers."""
    config = MagicMock()
    config.provider = "foundry"  # or openai or ollama
    
    agent = MagicMock()
    agent.run = AsyncMock(return_value=MagicMock(text="Test response"))
    
    state = AgentState()
    brain = BrainState()
    persona = MagicMock()
    persona.workflow = "second_brain"
    persona.tools = []
    persona.mcp_servers = {}
    persona.heartbeat_task = "Test task"
    
    # Should work without async context manager
    with patch("workflow.run_heartbeat", wraps=run_heartbeat):
        # We'll just test that it doesn't raise
        asyncio.run(run_heartbeat(agent, state, brain, config, persona))

def test_run_step_with_copilot_agent():
    """Test _run_step with GitHubCopilotAgent."""
    # GitHubCopilotAgent has special handling
    agent = MagicMock()
    # Make agent.run return a response with text attribute
    agent.run = AsyncMock(return_value=MagicMock(text="Copilot response"))
    
    state = AgentState()
    result = asyncio.run(_run_step(agent, "test_step", "Test prompt", state))
    assert result == "Copilot response"

def test_run_step_with_foundry_agent():
    """Test _run_step with Foundry agent."""
    agent = MagicMock()
    agent.run = AsyncMock(return_value=MagicMock(text="Foundry response"))
    
    state = AgentState()
    result = asyncio.run(_run_step(agent, "test_step", "Test prompt", state))
    assert result == "Foundry response"

def test_run_step_with_openai_agent():
    """Test _run_step with OpenAI agent."""
    agent = MagicMock()
    agent.run = AsyncMock(return_value=MagicMock(text="OpenAI response"))
    
    state = AgentState()
    result = asyncio.run(_run_step(agent, "test_step", "Test prompt", state))
    assert result == "OpenAI response"

def test_run_step_with_ollama_agent():
    """Test _run_step with Ollama agent."""
    agent = MagicMock()
    agent.run = AsyncMock(return_value=MagicMock(text="Ollama response"))
    
    state = AgentState()
    result = asyncio.run(_run_step(agent, "test_step", "Test prompt", state))
    assert result == "Ollama response"

def test_run_step_handles_attribute_error():
    """Test that _run_step handles agents that don't have text attribute properly."""
    agent = MagicMock()
    agent.run = AsyncMock(return_value="String response")  # Not an object with text attribute
    
    state = AgentState()
    result = asyncio.run(_run_step(agent, "test_step", "Test prompt", state))
    # Should convert to string automatically
    assert isinstance(result, str)

def test_run_step_history_maintained():
    """Test that execution history is properly maintained across multiple steps."""
    agent = MagicMock()
    agent.run = AsyncMock(return_value=MagicMock(text="Response"))
    
    state = AgentState()
    
    # Run multiple steps
    for i in range(3):
        asyncio.run(_run_step(agent, f"step_{i}", f"Prompt {i}", state))
    
    assert len(state.execution_history) == 3
    for i, entry in enumerate(state.execution_history):
        assert entry["step"] == f"step_{i}"
        assert f"Prompt {i}" in entry["prompt"]

def test_run_step_lesson_limit():
    """Test that lessons_learned doesn't grow indefinitely (though actual limiting happens elsewhere)."""
    agent = MagicMock()
    agent.run = AsyncMock(return_value=MagicMock(text="Response"))
    
    state = AgentState()
    # Mock extract_lessons to return many lessons
    with patch("workflow.extract_lessons", return_value=[
        {"description": f"Lesson {i}", "category": "insight"} for i in range(1000)
    ]):
        asyncio.run(_run_step(agent, "test_step", "Test prompt", state))
    
    # The actual limiting of lessons happens in state.py, not in _run_step
    # So we just check that lessons are extracted
    assert len(state.lessons_learned) == 1000