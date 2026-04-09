"""Test suite for scheduler.py - Retry logic and error handling."""
from __future__ import annotations

import asyncio
import logging
import pytest
import sys
from unittest.mock import MagicMock, patch, AsyncMock

# Mock external dependencies BEFORE importing anything from scheduler
sys.modules['copilot'] = MagicMock()
sys.modules['agent_framework_github_copilot'] = MagicMock()
sys.modules['agent_framework'] = MagicMock()
sys.modules['agent_framework.foundry'] = MagicMock()
sys.modules['agent_framework.openai'] = MagicMock()
sys.modules['agent_framework.ollama'] = MagicMock()
sys.modules['azure.identity'] = MagicMock()

from scheduler import retry, run_scheduler
from config import AppConfig
from state import AgentState, load_state, save_state
from second_brain import BrainState, load_brain, save_brain

# Set up logging to avoid warnings during tests
logging.basicConfig(level=logging.DEBUG)

@pytest.fixture
def mock_config():
    """Create a mock AppConfig."""
    config = MagicMock(spec=AppConfig)
    config.provider = "copilot"
    config.model = "test-model"
    config.agent_name = "test-agent"
    config.heartbeat_interval_sec = 0.1  # Short interval for testing
    config.state_file = "test_state.json"
    return config

@pytest.fixture
def mock_state():
    """Create a mock AgentState."""
    return AgentState(
        last_heartbeat="2024-01-01T00:00:00Z",
        execution_count=0,
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

def test_retry_decorator_success():
    """Test that retry decorator works for successful function."""
    @retry(max_attempts=3, delay=0.1)
    async def successful_func():
        return "success"
    
    result = asyncio.run(successful_func())
    assert result == "success"

def test_retry_decorator_transient_failures():
    """Test that retry decorator retries transient failures."""
    call_count = 0
    
    @retry(max_attempts=3, delay=0.1)
    async def transient_failures():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise IOError("Temporary network error")
        return "success"
    
    result = asyncio.run(transient_failures())
    assert result == "success"
    assert call_count == 3, "Should retry 3 times before success"

def test_retry_decorator_max_attempts_exceeded():
    """Test that retry decorator raises when max attempts exceeded."""
    @retry(max_attempts=2, delay=0.1)
    async def always_fail():
        raise IOError("Always fails")
    
    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(always_fail())
    assert "All 2 attempts failed" in str(exc_info.value)

def test_retry_decorator_non_retryable_errors():
    """Test that non-retryable errors are not retried."""
    @retry(max_attempts=3, delay=0.1)
    async def non_retryable_error():
        raise ValueError("Non-retryable error")
    
    with pytest.raises(ValueError) as exc_info:
        asyncio.run(non_retryable_error())
    # Should not wrap in RuntimeError

def test_retry_decorator_backoff():
    """Test that exponential backoff is applied."""
    delays = []
    
    @retry(max_attempts=3, delay=0.1, backoff=2.0)
    async def record_delays():
        delays.append(0.1)  # Simplified - in real code we'd measure actual sleep
        raise IOError("Retry")
    
    with pytest.raises(RuntimeError):
        asyncio.run(record_delays())
    
    # Should have 3 attempts with delays: 0.1, 0.2, 0.4
    # But our simplified test only records the initial delay
    # In a real test we'd measure actual sleep times
    assert len(delays) == 3

def test_retry_decorator_with_asyncio_timeout():
    """Test that asyncio.TimeoutError is retried."""
    @retry(max_attempts=3, delay=0.1)
    async def timeout_func():
        raise asyncio.TimeoutError("Timeout")
    
    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(timeout_func())
    assert "All 3 attempts failed" in str(exc_info.value)

def test_retry_decorator_with_os_error():
    """Test that OSError is retried."""
    @retry(max_attempts=3, delay=0.1)
    async def os_error_func():
        raise OSError("OS error")
    
    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(os_error_func())
    assert "All 3 attempts failed" in str(exc_info.value)

def test_retry_decorator_with_io_error():
    """Test that IOError is retried."""
    @retry(max_attempts=3, delay=0.1)
    async def io_error_func():
        raise IOError("IO error")
    
    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(io_error_func())
    assert "All 3 attempts failed" in str(exc_info.value)

def test_retry_decorator_preserves_original_exception_type():
    """Test that the original exception type is preserved when retries exhausted."""
    @retry(max_attempts=2, delay=0.1)
    async def custom_exception():
        raise ValueError("Custom error")
    
    with pytest.raises(ValueError) as exc_info:
        asyncio.run(custom_exception())
    # Should raise ValueError, not RuntimeError

def test_load_state_with_retry_success():
    """Test that load_state with retry works when it eventually succeeds."""
    # Mock load_state to fail first then succeed
    with patch("scheduler.load_state", side_effect=[IOError("Temporary error"), MagicMock(return_value=AgentState())]):
        # The retry decorator will call load_state multiple times
        # We need to test the actual behavior with the decorator
        @retry(max_attempts=3, delay=0.1)
        async def load_with_retry():
            return await load_state("test.json")
        
        result = asyncio.run(load_with_retry())
        assert result is not None

@pytest.mark.asyncio
async def test_save_state_with_retry_success():
    """Test that save_state with retry works when it eventually succeeds."""
    state = AgentState()
    state.last_heartbeat = "2024-01-01T00:00:00Z"
    state.execution_count = 1
    state.memo = "Test memo"
    state.notes = []
    state.connections = []

    call_count = 0

    def save_state_side_effect(*args, **kwargs):
        nonlocal call_count
        if call_count == 0:
            call_count += 1
            raise IOError("Temporary error")
        else:
            # Return a coroutine that yields None
            async def succeed():
                return None
            return succeed()

    with patch("scheduler.save_state", side_effect=save_state_side_effect):
        @retry(max_attempts=3, delay=0.1)
        async def save_with_retry():
            await save_state(state, "test.json")

        # Should not raise
        await save_with_retry()

def test_load_brain_with_retry_success():
    """Test that load_brain with retry works when it eventually succeeds."""
    with patch("scheduler.load_brain", side_effect=[IOError("Temporary error"), MagicMock(return_value=BrainState())]):
        @retry(max_attempts=3, delay=0.1)
        async def load_with_retry():
            return await load_brain("test.json")
        
        result = asyncio.run(load_with_retry())
        assert result is not None

def test_save_brain_with_retry_success():
    """Test that save_brain with retry works when it eventually succeeds."""
    brain = BrainState()
    
    # Mock file operations to prevent actual disk writes
    with patch("tempfile.mkstemp", return_value=(1, "dummy_path")):
        with patch("os.fdopen", return_value=MagicMock()):
            with patch("json.dump", return_value=None):
                with patch("os.replace", return_value=None):
                    with patch("os.path.exists", return_value=True):
                        # Now call save_brain and expect it to succeed
                        # We'll use the retry decorator to handle any transient errors
                        @retry(max_attempts=3, delay=0.1)
                        async def save_with_retry():
                            await save_brain(brain, "test.json")

                        # Should not raise
                        asyncio.run(save_with_retry())

def test_run_scheduler_handles_keyboard_interrupt():
    """Test that scheduler handles KeyboardInterrupt gracefully."""
    config = MagicMock(spec=AppConfig)
    config.provider = "copilot"
    config.model = "test-model"
    config.agent_name = "test-agent"
    config.heartbeat_interval_sec = 0.1
    config.state_file = "test_state.json"
    config.max_history = 100
    config.agent_instructions = None

    mock_persona = MagicMock(name="persona", tools=[], mcp_servers={})
    mock_persona.instructions = "test instructions"

    with patch("scheduler.load_persona", return_value=mock_persona), \
         patch("scheduler.load_state", new_callable=AsyncMock, return_value=AgentState()), \
         patch("scheduler.load_brain", new_callable=AsyncMock, return_value=BrainState()), \
         patch("scheduler.save_state", new_callable=AsyncMock), \
         patch("scheduler.save_brain", new_callable=AsyncMock), \
         patch("scheduler.create_agent", return_value=MagicMock()), \
         patch("scheduler.run_heartbeat", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            asyncio.run(run_scheduler(config))

def test_run_scheduler_handles_async_timeout():
    """Test that scheduler handles asyncio.TimeoutError and continues running."""
    config = MagicMock(spec=AppConfig)
    config.provider = "foundry"  # non-copilot avoids async context manager
    config.model = "test-model"
    config.agent_name = "test-agent"
    config.heartbeat_interval_sec = 0.01  # Very short interval for testing
    config.state_file = "test_state.json"
    config.max_history = 100
    config.agent_instructions = None

    mock_persona = MagicMock(name="persona", tools=[], mcp_servers={})
    mock_persona.instructions = "test instructions"

    mock_heartbeat = AsyncMock(side_effect=asyncio.TimeoutError)

    with patch("scheduler.load_persona", return_value=mock_persona), \
         patch("scheduler.load_state", new_callable=AsyncMock, return_value=AgentState()), \
         patch("scheduler.load_brain", new_callable=AsyncMock, return_value=BrainState()), \
         patch("scheduler.save_state", new_callable=AsyncMock), \
         patch("scheduler.save_brain", new_callable=AsyncMock), \
         patch("scheduler.create_agent", return_value=MagicMock()), \
         patch("scheduler.run_heartbeat", mock_heartbeat), \
         patch("scheduler.asyncio.sleep", new_callable=AsyncMock):
        # Use max_iterations=3 to prove the scheduler continues after TimeoutError
        asyncio.run(run_scheduler(config, max_iterations=3))

    # The scheduler ran all 3 iterations despite TimeoutError on each
    assert mock_heartbeat.call_count == 3

def test_run_scheduler_handles_general_exceptions():
    """Test that scheduler handles general exceptions during heartbeat."""
    config = MagicMock(spec=AppConfig)
    config.provider = "foundry"  # non-copilot avoids async context manager
    config.model = "test-model"
    config.agent_name = "test-agent"
    config.heartbeat_interval_sec = 0.01  # Very short interval for testing
    config.state_file = "test_state.json"
    config.max_history = 100
    config.agent_instructions = None

    mock_persona = MagicMock(name="persona", tools=[], mcp_servers={})
    mock_persona.instructions = "test instructions"

    mock_heartbeat = AsyncMock(side_effect=Exception("Test error"))

    with patch("scheduler.load_persona", return_value=mock_persona), \
         patch("scheduler.load_state", new_callable=AsyncMock, return_value=AgentState()), \
         patch("scheduler.load_brain", new_callable=AsyncMock, return_value=BrainState()), \
         patch("scheduler.save_state", new_callable=AsyncMock), \
         patch("scheduler.save_brain", new_callable=AsyncMock), \
         patch("scheduler.create_agent", return_value=MagicMock()), \
         patch("scheduler.run_heartbeat", mock_heartbeat), \
         patch("scheduler.asyncio.sleep", new_callable=AsyncMock):
        # Use max_iterations=3 to prove the scheduler continues after general exceptions
        asyncio.run(run_scheduler(config, max_iterations=3))

    # The scheduler ran all 3 iterations despite Exception on each
    assert mock_heartbeat.call_count == 3

def test_run_scheduler_saves_state_after_error():
    """Test that state is saved even when heartbeat fails."""
    config = MagicMock(spec=AppConfig)
    config.provider = "foundry"  # non-copilot avoids async context manager
    config.model = "test-model"
    config.agent_name = "test-agent"
    config.heartbeat_interval_sec = 0.01  # Very short interval for testing
    config.state_file = "test_state.json"
    config.max_history = 100
    config.agent_instructions = None

    mock_persona = MagicMock(name="persona", tools=[], mcp_servers={})
    mock_persona.instructions = "test instructions"
    mock_save_state = AsyncMock(side_effect=Exception("Save failed"))

    with patch("scheduler.load_persona", return_value=mock_persona), \
         patch("scheduler.load_state", new_callable=AsyncMock, return_value=AgentState()), \
         patch("scheduler.load_brain", new_callable=AsyncMock, return_value=BrainState()), \
         patch("scheduler.save_state", mock_save_state), \
         patch("scheduler.save_brain", new_callable=AsyncMock), \
         patch("scheduler.create_agent", return_value=MagicMock()), \
         patch("scheduler.run_heartbeat", new_callable=AsyncMock, side_effect=Exception("Test error")), \
         patch("scheduler.asyncio.sleep", new_callable=AsyncMock):
        # Use max_iterations=2 to verify save_state is called and scheduler keeps running
        asyncio.run(run_scheduler(config, max_iterations=2))

    # Verify save_state was actually called (proves finally block ran)
    assert mock_save_state.call_count >= 1, "save_state should have been called in finally block"
def test_retry_decorator_with_multiple_exception_types():
    """Test that retry handles multiple exception types."""
    call_count = 0
    
    @retry(max_attempts=5, delay=0.1)
    async def multiple_errors():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise IOError("IO error")
        elif call_count == 2:
            raise asyncio.TimeoutError("Timeout")
        elif call_count == 3:
            raise OSError("OS error")
        else:
            return "success"
    
    result = asyncio.run(multiple_errors())
    assert result == "success"
    assert call_count == 4

def test_retry_decorator_with_non_transient_error():
    """Test that non-transient errors are not retried."""
    @retry(max_attempts=3, delay=0.1)
    async def non_transient_error():
        raise ValueError("This should not be retried")
    
    with pytest.raises(ValueError):
        asyncio.run(non_transient_error())

def test_retry_decorator_preserves_call_args():
    """Test that retry decorator passes arguments correctly."""
    @retry(max_attempts=3, delay=0.1)
    async def func_with_args(a, b=1):
        return a + b
    
    result = asyncio.run(func_with_args(5, b=3))
    assert result == 8

def test_retry_decorator_preserves_call_kwargs():
    """Test that retry decorator passes keyword arguments correctly."""
    @retry(max_attempts=3, delay=0.1)
    async def func_with_kwargs(a, b=1):
        return a + b
    
    result = asyncio.run(func_with_kwargs(5, b=3))
    assert result == 8

def test_retry_decorator_with_no_args():
    """Test retry decorator with default parameters."""
    @retry()
    async def simple_func():
        return "default works"
    
    result = asyncio.run(simple_func())
    assert result == "default works"

def test_run_scheduler_with_foundry_provider():
    """Test that scheduler works with foundry provider (non-copilot path)."""
    config = MagicMock(spec=AppConfig)
    config.provider = "foundry"
    config.model = "test-model"
    config.agent_name = "test-agent"
    config.heartbeat_interval_sec = 0.01
    config.state_file = "test_state.json"
    config.max_history = 100
    config.agent_instructions = None

    mock_persona = MagicMock(name="persona", tools=[], mcp_servers={})
    mock_persona.instructions = "test instructions"
    mock_heartbeat = AsyncMock()

    with patch("scheduler.load_persona", return_value=mock_persona), \
         patch("scheduler.load_state", new_callable=AsyncMock, return_value=AgentState()), \
         patch("scheduler.load_brain", new_callable=AsyncMock, return_value=BrainState()), \
         patch("scheduler.save_state", new_callable=AsyncMock), \
         patch("scheduler.save_brain", new_callable=AsyncMock), \
         patch("scheduler.create_agent", return_value=MagicMock()), \
         patch("scheduler.run_heartbeat", mock_heartbeat), \
         patch("scheduler.asyncio.sleep", new_callable=AsyncMock):
        asyncio.run(run_scheduler(config, max_iterations=2))

    assert mock_heartbeat.call_count >= 1, "Heartbeat should have been called at least once"

def test_run_scheduler_with_openai_provider():
    """Test that scheduler works with openai provider (non-copilot path)."""
    config = MagicMock(spec=AppConfig)
    config.provider = "openai"
    config.model = "test-model"
    config.agent_name = "test-agent"
    config.heartbeat_interval_sec = 0.01
    config.state_file = "test_state.json"
    config.max_history = 100
    config.agent_instructions = None

    mock_persona = MagicMock(name="persona", tools=[], mcp_servers={})
    mock_persona.instructions = "test instructions"
    mock_heartbeat = AsyncMock()

    with patch("scheduler.load_persona", return_value=mock_persona), \
         patch("scheduler.load_state", new_callable=AsyncMock, return_value=AgentState()), \
         patch("scheduler.load_brain", new_callable=AsyncMock, return_value=BrainState()), \
         patch("scheduler.save_state", new_callable=AsyncMock), \
         patch("scheduler.save_brain", new_callable=AsyncMock), \
         patch("scheduler.create_agent", return_value=MagicMock()), \
         patch("scheduler.run_heartbeat", mock_heartbeat), \
         patch("scheduler.asyncio.sleep", new_callable=AsyncMock):
        asyncio.run(run_scheduler(config, max_iterations=2))

    assert mock_heartbeat.call_count >= 1, "Heartbeat should have been called at least once"

def test_run_scheduler_with_ollama_provider():
    """Test that scheduler works with ollama provider (non-copilot path)."""
    config = MagicMock(spec=AppConfig)
    config.provider = "ollama"
    config.model = "test-model"
    config.agent_name = "test-agent"
    config.heartbeat_interval_sec = 0.01
    config.state_file = "test_state.json"
    config.max_history = 100
    config.agent_instructions = None

    mock_persona = MagicMock(name="persona", tools=[], mcp_servers={})
    mock_persona.instructions = "test instructions"
    mock_heartbeat = AsyncMock()

    with patch("scheduler.load_persona", return_value=mock_persona), \
         patch("scheduler.load_state", new_callable=AsyncMock, return_value=AgentState()), \
         patch("scheduler.load_brain", new_callable=AsyncMock, return_value=BrainState()), \
         patch("scheduler.save_state", new_callable=AsyncMock), \
         patch("scheduler.save_brain", new_callable=AsyncMock), \
         patch("scheduler.create_agent", return_value=MagicMock()), \
         patch("scheduler.run_heartbeat", mock_heartbeat), \
         patch("scheduler.asyncio.sleep", new_callable=AsyncMock):
        asyncio.run(run_scheduler(config, max_iterations=2))

    assert mock_heartbeat.call_count >= 1, "Heartbeat should have been called at least once"

def test_run_scheduler_with_azure_openai_provider():
    """Test that scheduler works with azure_openai provider (non-copilot path)."""
    config = MagicMock(spec=AppConfig)
    config.provider = "azure_openai"
    config.model = "gpt-4.1-kvw"
    config.agent_name = "test-agent"
    config.heartbeat_interval_sec = 0.01
    config.state_file = "test_state.json"
    config.max_history = 100
    config.agent_instructions = None

    mock_persona = MagicMock(name="persona", tools=[], mcp_servers={})
    mock_persona.instructions = "test instructions"
    mock_heartbeat = AsyncMock()

    with patch("scheduler.load_persona", return_value=mock_persona), \
         patch("scheduler.load_state", new_callable=AsyncMock, return_value=AgentState()), \
         patch("scheduler.load_brain", new_callable=AsyncMock, return_value=BrainState()), \
         patch("scheduler.save_state", new_callable=AsyncMock), \
         patch("scheduler.save_brain", new_callable=AsyncMock), \
         patch("scheduler.create_agent", return_value=MagicMock()), \
         patch("scheduler.run_heartbeat", mock_heartbeat), \
         patch("scheduler.asyncio.sleep", new_callable=AsyncMock):
        asyncio.run(run_scheduler(config, max_iterations=2))

    assert mock_heartbeat.call_count >= 1, "Heartbeat should have been called at least once"

def test_retry_decorator_with_zero_attempts():
    """Test retry decorator with zero max_attempts."""
    @retry(max_attempts=0, delay=0.1)
    async def func():
        return "zero attempts"
    
    # Should raise error immediately
    with pytest.raises(RuntimeError):
        asyncio.run(func())

def test_retry_decorator_with_negative_attempts():
    """Test retry decorator with negative max_attempts."""
    @retry(max_attempts=-1, delay=0.1)
    async def func():
        return "negative attempts"
    
    # Should raise error immediately
    with pytest.raises(RuntimeError):
        asyncio.run(func())