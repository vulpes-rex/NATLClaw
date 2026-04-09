#!/usr/bin/env python3
"""Test suite for agent_setup.py - Permission handling and security fixes."""

import logging
import pytest
import sys
from unittest.mock import MagicMock, patch

# Mock external dependencies BEFORE importing anything from agent_setup
sys.modules['copilot'] = MagicMock()
sys.modules['agent_framework_github_copilot'] = MagicMock()
sys.modules['agent_framework'] = MagicMock()
sys.modules['agent_framework.foundry'] = MagicMock()
sys.modules['agent_framework.openai'] = MagicMock()
sys.modules['agent_framework.ollama'] = MagicMock()
sys.modules['azure.identity'] = MagicMock()

from agent_setup import _create_secure_permission_handler
from config import AppConfig

# Set up logging to avoid warnings during tests
logging.basicConfig(level=logging.DEBUG)

@pytest.fixture
def mock_config():
    """Create a mock AppConfig for testing."""
    config = MagicMock(spec=AppConfig)
    config.provider = "copilot"
    config.model = "test-model"
    config.agent_name = "test-agent"
    return config

def test_permission_handler_approves_non_sensitive():
    """Test that non-sensitive permissions are auto-approved."""
    handler = _create_secure_permission_handler()
    
    # Test with a non-sensitive permission (e.g., agent/instruction)
    result = handler({
        "name": "agent/instruction",
        "description": "Provide instructions to the agent"
    })
    assert result is True

def test_permission_handler_denies_sensitive_without_confirmation():
    """Test that sensitive permissions require user confirmation."""
    # This test simulates non-interactive environment
    with patch("builtins.input", side_effect=EOFError):
        handler = _create_secure_permission_handler()
        result = handler({
            "name": "io/fs/write",
            "description": "Write to file system"
        })
        assert result is False

def test_permission_handler_handles_user_denial():
    """Test that user can deny sensitive permissions."""
    with patch("builtins.input", return_value="no"):
        handler = _create_secure_permission_handler()
        result = handler({
            "name": "io/fs/scan",
            "description": "Scan file system"
        })
        assert result is False

def test_permission_handler_handles_user_grant():
    """Test that user can grant sensitive permissions."""
    with patch("builtins.input", return_value="yes"):
        handler = _create_secure_permission_handler()
        result = handler({
            "name": "system/environment",
            "description": "Access environment variables"
        })
        assert result is True

def test_permission_handler_handles_invalid_permission_name():
    """Test that invalid permission names are handled gracefully."""
    handler = _create_secure_permission_handler()
    # Empty permission name should be auto-approved (non-sensitive)
    result = handler({"name": ""})
    assert result is True

def test_permission_handler_logs_exceptions():
    """Test that exceptions in permission handler are logged."""
    handler = _create_secure_permission_handler()
    
    # Mock the logger to capture logs
    with patch("agent_setup._LOGGER.error") as mock_error:
        # Simulate an exception by passing None instead of dict
        result = handler(None)
        assert result is False
        mock_error.assert_called_once()

def test_permission_handler_sensitive_permission_patterns():
    """Test that permission patterns are correctly identified as sensitive."""
    handler = _create_secure_permission_handler()
    
    # Test various sensitive patterns
    sensitive_permissions = [
        "io/fs/write",
        "io/fs/scan",
        "system/environment",
        "network/request",
        "process/start",
        "fs/read",
        "os/exec",
        "storage/write"
    ]
    
    for perm in sensitive_permissions:
        # In non-interactive test, should return False
        result = handler({"name": perm})
        assert result is False

def test_permission_handler_non_sensitive_patterns():
    """Test that non-sensitive permissions are auto-approved."""
    handler = _create_secure_permission_handler()
    
    # Test various non-sensitive patterns
    non_sensitive_permissions = [
        "agent/instruction",
        "agent/tools/list",
        "agent/tools/invoke",
        "log/info",
        "log/error"
    ]
    
    for perm in non_sensitive_permissions:
        result = handler({"name": perm})
        assert result is True

def test_permission_handler_handles_missing_description():
    """Test that missing description is handled gracefully."""
    with patch("builtins.input", return_value="yes"):
        handler = _create_secure_permission_handler()
        result = handler({"name": "io/fs/write"})
        assert result is True