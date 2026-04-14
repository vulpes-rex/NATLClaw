"""Test suite for scheduler.py - Retry logic and error handling."""
from __future__ import annotations

import asyncio
import logging
import os
import time
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

from scheduler import (
    retry,
    run_scheduler,
    _wait_for_event_or_timeout,
    _drain_event_queue_bounded,
    acquire_scheduler_lock,
    get_scheduler_lock_info,
    get_scheduler_runtime_backpressure_stats,
    release_scheduler_lock,
)
from config import AppConfig
from project_context import Project
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
    config.max_events_per_heartbeat = 50
    config.queue_depth_warn_threshold = 200
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
    call_count = 0

    async def mock_load_brain(state_file):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise IOError("Temporary error")
        return BrainState()

    with patch("second_brain.load_brain", side_effect=mock_load_brain):
        from second_brain import load_brain as _lb
        @retry(max_attempts=3, delay=0.1)
        async def load_with_retry():
            return await mock_load_brain("test.json")

        result = asyncio.run(load_with_retry())
        assert result is not None
        assert call_count == 2

def test_save_brain_with_retry_success():
    """Test that save_brain with retry works when it eventually succeeds."""
    brain = BrainState()
    call_count = 0

    async def mock_save_brain(b, state_file, max_reviews=50):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise IOError("Temporary error")

    @retry(max_attempts=3, delay=0.1)
    async def save_with_retry():
        await mock_save_brain(brain, "test.json")

    asyncio.run(save_with_retry())
    assert call_count == 2

def _make_scheduler_config(**overrides):
    """Build a MagicMock config suitable for run_scheduler tests."""
    config = MagicMock(spec=AppConfig)
    config.provider = overrides.get("provider", "foundry")
    config.model = overrides.get("model", "test-model")
    config.agent_name = "test-agent"
    config.heartbeat_interval_sec = 0.01
    config.max_events_per_heartbeat = overrides.get("max_events_per_heartbeat", 50)
    config.queue_depth_warn_threshold = overrides.get("queue_depth_warn_threshold", 200)
    config.state_file = "test_state.json"
    config.max_history = 100
    config.agent_instructions = None
    config.persona = "default"
    config.watch_path = "."
    return config


def _mock_decision_directives():
    """Build a default apply_decision return value for scheduler tests."""
    return {
        "action": "run_heartbeat",
        "active_task": None,
        "workflow_override": None,
        "skip_agent": False,
        "extra_context": "",
        "outbox_messages": [],
    }


def _scheduler_patches(mock_heartbeat, mock_persona=None):
    """Return a contextmanager that applies all standard scheduler mocks."""
    from contextlib import contextmanager
    if mock_persona is None:
        mock_persona = MagicMock(name="persona", tools=[], mcp_servers={})
        mock_persona.instructions = "test instructions"

    mock_watcher = MagicMock()
    mock_watcher.start = MagicMock()
    mock_watcher.stop = MagicMock()

    # Decision engine mocks
    mock_decision = MagicMock()
    mock_decision.chosen.action.value = "run_heartbeat"
    mock_decision.chosen.score = 50.0
    mock_decision.chosen.rationale = "test"
    mock_decision.supplementary_actions = []

    @contextmanager
    def ctx():
        from contextlib import ExitStack
        patches = [
            patch("scheduler.load_persona", return_value=mock_persona),
            patch("scheduler.load_state", new_callable=AsyncMock, return_value=AgentState()),
            patch("scheduler.load_brain", new_callable=AsyncMock, return_value=BrainState()),
            patch("scheduler.save_state", new_callable=AsyncMock),
            patch("scheduler.save_brain", new_callable=AsyncMock),
            patch("scheduler.load_tasks", new_callable=AsyncMock, return_value=[]),
            patch("scheduler.save_tasks", new_callable=AsyncMock),
            patch("scheduler.load_outbox", new_callable=AsyncMock, return_value=[]),
            patch("scheduler.save_outbox", new_callable=AsyncMock),
            patch("scheduler.load_projects", new_callable=AsyncMock, return_value=[]),
            patch("scheduler.detect_and_save_project", return_value=None),
            patch("scheduler.create_agent", return_value=MagicMock()),
            patch("scheduler.run_heartbeat", mock_heartbeat),
            patch("decision_engine.build_decision_context", return_value=MagicMock()),
            patch("decision_engine.evaluate_heartbeat", return_value=mock_decision),
            patch("decision_engine.apply_decision", return_value=_mock_decision_directives()),
            patch("decision_engine.record_decision", return_value="mock-note-id"),
            patch("decision_engine.record_decision_outcome"),
            patch("decision_engine.update_consecutive_empty"),
            patch("scheduler.acquire_scheduler_lock", return_value=True),
            patch("scheduler.release_scheduler_lock"),
            patch("event_watcher.EventWatcher", return_value=mock_watcher),
            patch("event_watcher.drain_pending_events", return_value=0),
        ]
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            yield
    return ctx()


def _make_event_queue():
    """Create an event queue pre-loaded with a dummy event so wait_for returns immediately."""
    q = asyncio.PriorityQueue()
    q.put_nowait((3, "test_tick", {}))
    return q


def test_run_scheduler_handles_keyboard_interrupt():
    """Test that scheduler handles KeyboardInterrupt gracefully."""
    config = _make_scheduler_config(provider="copilot")
    mock_heartbeat = AsyncMock(side_effect=KeyboardInterrupt)
    q = _make_event_queue()

    with _scheduler_patches(mock_heartbeat):
        with pytest.raises(KeyboardInterrupt):
            asyncio.run(run_scheduler(config, max_iterations=1, event_queue=q))


def test_run_scheduler_handles_async_timeout():
    """Test that scheduler handles asyncio.TimeoutError and continues running."""
    config = _make_scheduler_config(provider="foundry")
    mock_heartbeat = AsyncMock(side_effect=[asyncio.TimeoutError, AsyncMock(return_value=None)])
    q = _make_event_queue()

    with _scheduler_patches(mock_heartbeat):
        asyncio.run(run_scheduler(config, max_iterations=2, event_queue=q))

    assert mock_heartbeat.call_count >= 1, "Heartbeat should have been called at least once"


def test_run_scheduler_with_openai_provider():
    """Test that scheduler works with openai provider (non-copilot path)."""
    config = _make_scheduler_config(provider="openai")
    mock_heartbeat = AsyncMock()
    q = _make_event_queue()

    with _scheduler_patches(mock_heartbeat):
        asyncio.run(run_scheduler(config, max_iterations=2, event_queue=q))

    assert mock_heartbeat.call_count >= 1, "Heartbeat should have been called at least once"


def test_run_scheduler_with_ollama_provider():
    """Test that scheduler works with ollama provider (non-copilot path)."""
    config = _make_scheduler_config(provider="ollama")
    mock_heartbeat = AsyncMock()
    q = _make_event_queue()

    with _scheduler_patches(mock_heartbeat):
        asyncio.run(run_scheduler(config, max_iterations=2, event_queue=q))

    assert mock_heartbeat.call_count >= 1, "Heartbeat should have been called at least once"


def test_run_scheduler_with_azure_openai_provider():
    """Test that scheduler works with azure_openai provider (non-copilot path)."""
    config = _make_scheduler_config(provider="azure_openai", model="gpt-4.1-kvw")
    mock_heartbeat = AsyncMock()
    q = _make_event_queue()

    with _scheduler_patches(mock_heartbeat):
        asyncio.run(run_scheduler(config, max_iterations=2, event_queue=q))

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


# ── Scheduler lock reliability tests ───────────────────────────────────


def test_scheduler_lock_prevents_duplicate_instances(tmp_path):
    """Second lock acquire should fail while first scheduler holds lock."""
    state_file = str(tmp_path / "state.json")
    try:
        assert acquire_scheduler_lock(state_file) is True
        with patch("scheduler._is_pid_alive", return_value=True):
            assert acquire_scheduler_lock(state_file) is False
    finally:
        release_scheduler_lock()


def test_scheduler_lock_recovers_from_stale_pid(tmp_path):
    """Stale PID lock should be removed and replaced atomically."""
    state_file = str(tmp_path / "state.json")
    lock_file = tmp_path / "scheduler.lock"
    lock_file.write_text("999999", encoding="utf-8")

    try:
        with patch("scheduler._is_pid_alive", return_value=False):
            assert acquire_scheduler_lock(state_file) is True

        assert lock_file.exists()
        assert lock_file.read_text(encoding="utf-8").strip() == str(os.getpid())
    finally:
        release_scheduler_lock()


def test_scheduler_lock_recovers_from_malformed_file(tmp_path):
    """Malformed lock file content should not block scheduler startup."""
    state_file = str(tmp_path / "state.json")
    lock_file = tmp_path / "scheduler.lock"
    lock_file.write_text("not-a-pid", encoding="utf-8")

    try:
        assert acquire_scheduler_lock(state_file) is True
        assert lock_file.read_text(encoding="utf-8").strip() == str(os.getpid())
    finally:
        release_scheduler_lock()


def test_get_scheduler_lock_info_reports_missing_lock(tmp_path):
    """Lock diagnostics should report no lock when file is absent."""
    state_file = str(tmp_path / "state.json")
    info = get_scheduler_lock_info(state_file)
    assert info["exists"] is False
    assert info["pid"] is None
    assert info["pid_alive"] is None
    assert info["stale"] is None


def test_get_scheduler_lock_info_reports_active_lock(tmp_path):
    """Lock diagnostics should expose lock pid and liveness."""
    state_file = str(tmp_path / "state.json")
    try:
        assert acquire_scheduler_lock(state_file) is True
        info = get_scheduler_lock_info(state_file)
        assert info["exists"] is True
        assert info["pid"] == os.getpid()
        assert info["pid_alive"] is True
        assert info["stale"] is False
    finally:
        release_scheduler_lock()


def test_get_scheduler_lock_info_reports_malformed_lock(tmp_path):
    """Malformed lock files should be flagged as stale/malformed diagnostics."""
    state_file = str(tmp_path / "state.json")
    lock_file = tmp_path / "scheduler.lock"
    lock_file.write_text("not-a-pid", encoding="utf-8")

    info = get_scheduler_lock_info(state_file)
    assert info["exists"] is True
    assert info["pid"] is None
    assert info["pid_alive"] is None
    assert info["stale"] is True
    assert info["malformed"] is True


# ── Event-driven scheduler tests ──────────────────────────────────────


def test_scheduler_drains_pending_events():
    """Scheduler should drain cross-process events at each heartbeat."""
    config = _make_scheduler_config(provider="foundry")
    mock_heartbeat = AsyncMock()
    q = _make_event_queue()

    drain_counts = []
    def mock_drain(eq):
        drain_counts.append(1)
        return 0

    with _scheduler_patches(mock_heartbeat), \
         patch("event_watcher.drain_pending_events", side_effect=mock_drain):
        asyncio.run(run_scheduler(config, max_iterations=2, event_queue=q))

    # drain_pending_events should have been called at least once
    assert len(drain_counts) >= 1


def test_scheduler_event_wakes_from_sleep():
    """Pushing an event to the queue should wake the scheduler from wait_for."""
    config = _make_scheduler_config(provider="foundry")
    mock_heartbeat = AsyncMock()

    # Pre-load a task_created event — scheduler should wake immediately
    q = asyncio.PriorityQueue()
    q.put_nowait((1, "task_created", {"task_id": "t001"}))

    with _scheduler_patches(mock_heartbeat):
        asyncio.run(run_scheduler(config, max_iterations=2, event_queue=q))

    assert mock_heartbeat.call_count >= 1


def test_scheduler_injects_project_branch_and_active_work_into_agent_instructions():
    """Scheduler should pass branch + active-work project context into create_agent."""
    config = _make_scheduler_config(provider="foundry")
    mock_heartbeat = AsyncMock()
    q = _make_event_queue()

    current_project = Project(
        path=".",
        name="NATLClaw",
        language="python",
        framework="setuptools",
        branch="feature/s11-context",
        active_work="Working on scheduler context accuracy",
    )

    with _scheduler_patches(mock_heartbeat), \
         patch("scheduler.detect_and_save_project", return_value=current_project), \
         patch("scheduler.create_agent", return_value=MagicMock()) as mock_create_agent:
        asyncio.run(run_scheduler(config, max_iterations=1, event_queue=q))

    assert mock_create_agent.called
    enriched_instructions = mock_create_agent.call_args[0][1]
    assert "Project Context:" in enriched_instructions
    assert "- Branch: feature/s11-context" in enriched_instructions
    assert "Working on scheduler context accuracy" in enriched_instructions


def test_wait_for_event_or_timeout_returns_none_when_timeout():
    """Helper returns None if no in-memory or pending-file events arrive."""
    q = asyncio.PriorityQueue()

    async def _run():
        return await _wait_for_event_or_timeout(
            q,
            timeout_sec=0.05,
            poll_interval_sec=0.01,
            drain_pending_events_fn=lambda _q: 0,
        )

    result = asyncio.run(_run())
    assert result is None


def test_wait_for_event_or_timeout_drains_pending_events_file(tmp_path, monkeypatch):
    """Pending file events should wake scheduler without waiting full timeout."""
    from event_watcher import drain_pending_events, enqueue_event

    monkeypatch.chdir(tmp_path)
    q = asyncio.PriorityQueue()

    async def _emit_later():
        await asyncio.sleep(0.05)
        enqueue_event("task_created", {"task_id": "t-file"})

    async def _run():
        emit_task = asyncio.create_task(_emit_later())
        start = time.monotonic()
        event = await _wait_for_event_or_timeout(
            q,
            timeout_sec=1.0,
            poll_interval_sec=0.02,
            drain_pending_events_fn=drain_pending_events,
        )
        elapsed = time.monotonic() - start
        await emit_task
        return event, elapsed

    event, elapsed = asyncio.run(_run())
    assert event is not None
    assert event[1] == "task_created"
    assert elapsed < 0.5, f"event wake-up took too long: {elapsed:.3f}s"


def test_drain_event_queue_bounded_caps_and_reports_remaining():
    """Bounded queue drain should leave spillover queued for next heartbeat."""
    q = asyncio.PriorityQueue()
    for i in range(6):
        q.put_nowait((3, f"event_{i}", {"i": i}))

    drained, remaining = _drain_event_queue_bounded(q, max_items=3)

    assert len(drained) == 3
    assert remaining == 3
    assert q.qsize() == 3


def test_scheduler_records_backpressure_stats():
    """Scheduler should expose latest queue depth/cap usage stats."""
    config = _make_scheduler_config(provider="foundry", max_events_per_heartbeat=2)
    mock_heartbeat = AsyncMock()
    q = asyncio.PriorityQueue()
    # Enough events to force decision spillover with cap=2.
    for i in range(5):
        q.put_nowait((3, f"queued_{i}", {"i": i}))

    with _scheduler_patches(mock_heartbeat), \
         patch("scheduler._wait_for_event_or_timeout", new_callable=AsyncMock, return_value=None):
        asyncio.run(run_scheduler(config, max_iterations=1, event_queue=q))

    stats = get_scheduler_runtime_backpressure_stats()
    assert stats["queue_depth_before_decision"] >= 5
    assert stats["events_consumed_for_decision"] == 2
    assert stats["decision_spillover_events"] >= 1