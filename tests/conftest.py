import pytest
import asyncio
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
import os
import sys

# Add project root to path so tests can import modules
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Global fixtures
@pytest.fixture
def mock_agent():
    """Mock agent framework"""
    with patch.dict('sys.modules', {'agent_framework_github_copilot': MagicMock()}):
        yield MagicMock()

@pytest.fixture
def mock_copilot():
    """Mock copilot module"""
    with patch.dict('sys.modules', {'copilot': MagicMock()}):
        yield MagicMock()

@pytest.fixture
def mock_config():
    """Mock configuration"""
    config = MagicMock()
    config.workspace = "C:\\test\\workspace"
    config.state_file = "C:\\test\\workspace\\state.json"
    config.brain_dir = "C:\\test\\workspace\\brain"
    config.log_file = "C:\\test\\workspace\\app.log"
    config.max_history = 100
    config.max_notes = 1000
    config.max_connections = 100
    return config

@pytest.fixture
def mock_logger():
    """Mock logger"""
    logger = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    return logger

@pytest.fixture
def mock_filesystem():
    """Mock filesystem operations"""
    with patch('os.path.exists', return_value=True), \
         patch('os.makedirs') as mock_mkdir, \
         patch('os.path.abspath') as mock_abspath, \
         patch('os.path.getsize', return_value=0), \
         patch('builtins.open', create=True) as mock_open:
        mock_abspath.return_value = "C:\\test\\workspace"
        mock_open.return_value = MagicMock()
        yield mock_mkdir, mock_abspath, mock_open

@pytest.fixture
def mock_asyncio():
    """Mock asyncio functions"""
    with patch('asyncio.sleep') as mock_sleep, \
         patch('asyncio.to_thread') as mock_to_thread:
        yield mock_sleep, mock_to_thread

@pytest.fixture
def temp_brain_dir(tmp_path):
    """Temporary brain directory for tests"""
    brain_dir = tmp_path / "brain"
    brain_dir.mkdir()
    return str(brain_dir)

@pytest.fixture
def mock_input():
    """Mock user input"""
    with patch('builtins.input', return_value="yes"):
        yield MagicMock()

@pytest.fixture
def event_loop():
    """Create a new event loop for each test"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(autouse=True)
def setup_mocks():
    """Automatically apply common mocks"""
    pass