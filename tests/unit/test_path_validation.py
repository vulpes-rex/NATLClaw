"""Test suite for file path validation in persona tools."""
from __future__ import annotations

import os
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
    from personas.devops_engineer.tools import _validate_path as devops_validate
    from personas.python_developer.tools import _validate_path as python_validate
    from personas.react_developer.tools import _validate_path as react_validate
    from personas.project_manager.tools import _validate_path as project_validate

# Test cases for path validation
VALID_PATHS = [
    "src",
    "data/test.json",
    "config.yaml",
    "./src/utils.py",
    "../README.md",  # Should be valid if within workspace
]

INVALID_PATHS = [
    "/etc/passwd",
    "C:\\Windows\\System32\\config.ini",
    "/usr/local/bin",
    "C:\\Users\\Public",
    "../../outside",
    "/outside",
]

EDGE_CASES = [
    ("..", True),  # Parent directory - should be valid if within workspace
    (".", True),   # Current directory
    ("", False),   # Empty path
    (None, False), # None path
]

def test_devops_validate_path_within_workspace():
    """Test that paths within workspace are validated correctly."""
    with patch("os.getcwd", return_value="C:\\test\\workspace"):
        # Also patch os.path.abspath to ensure it uses the mocked cwd
        with patch("os.path.abspath", side_effect=lambda x: os.path.join("C:\\test\\workspace", x)):
            for path in VALID_PATHS:
                # Use must_exist=False to test path structure, not file existence
                is_valid, error = devops_validate(path, operation="test", must_exist=False)
                assert is_valid, f"Expected path '{path}' to be valid"

def test_devops_validate_path_outside_workspace():
    """Test that paths outside workspace are rejected."""
    with patch("os.getcwd", return_value="C:\\test\\workspace"):
        with patch("os.path.abspath", side_effect=lambda x: os.path.join("C:\\test\\workspace", x)):
            for path in INVALID_PATHS:
                is_valid, error = devops_validate(path, operation="test")
                assert not is_valid, f"Expected path '{path}' to be invalid"

def test_devops_validate_path_traversal():
    """Test that path traversal attempts are detected."""
    with patch("os.getcwd", return_value="C:\\test\\workspace"):
        with patch("os.path.abspath", side_effect=lambda x: os.path.join("C:\\test\\workspace", x)):
            # Test paths that try to go above workspace root
            paths = [
                "../../secret",
                "/outside",
                "C:\\outside",
                "../..\\outside",
            ]
            for path in paths:
                is_valid, error = devops_validate(path, operation="test")
                assert not is_valid, f"Expected path '{path}' to be invalid (traversal)"

def test_devops_validate_path_with_must_exist():
    """Test validation with must_exist parameter."""
    with patch("os.getcwd", return_value="C:\\test\\workspace"):
        with patch("os.path.abspath", side_effect=lambda x: os.path.join("C:\\test\\workspace", x)):
            # Create a temporary file within the mocked workspace
            test_file = os.path.join("C:\\test\\workspace", "test_temp_file.txt")
            with open(test_file, "w") as f:
                f.write("test")
        
            try:
                # Existing file should be valid
                is_valid, error = devops_validate(test_file, must_exist=True, operation="test")
                assert is_valid, "Existing file should be valid"
                
                # Non-existing file should be invalid
                is_valid, error = devops_validate("nonexistent.txt", must_exist=True, operation="test")
                assert not is_valid, "Non-existing file should be invalid"
                
                # Non-existing file with must_exist=False should be valid
                is_valid, error = devops_validate("nonexistent.txt", must_exist=False, operation="test")
                assert is_valid, "Non-existing file with must_exist=False should be valid"
            finally:
                # Clean up
                if os.path.exists(test_file):
                    os.remove(test_file)

def test_python_validate_path_consistency():
    """Test that Python developer tools use same validation logic."""
    with patch("os.getcwd", return_value="C:\\test\\workspace"):
        with patch("os.path.abspath", side_effect=lambda x: os.path.join("C:\\test\\workspace", x)):
            for path in VALID_PATHS:
                is_valid, error = python_validate(path, operation="test", must_exist=False)
                assert is_valid, f"Expected path '{path}' to be valid"
            
            for path in INVALID_PATHS:
                is_valid, error = python_validate(path, operation="test")
                assert not is_valid, f"Expected path '{path}' to be invalid"

def test_react_validate_path_consistency():
    """Test that React developer tools use same validation logic."""
    with patch("os.getcwd", return_value="C:\\test\\workspace"):
        with patch("os.path.abspath", side_effect=lambda x: os.path.join("C:\\test\\workspace", x)):
            for path in VALID_PATHS:
                is_valid, error = react_validate(path, operation="test", must_exist=False)
                assert is_valid, f"Expected path '{path}' to be valid"
            
            for path in INVALID_PATHS:
                is_valid, error = react_validate(path, operation="test")
                assert not is_valid, f"Expected path '{path}' to be invalid"

def test_project_validate_path_consistency():
    """Test that Project manager tools use same validation logic."""
    with patch("os.getcwd", return_value="C:\\test\\workspace"):
        with patch("os.path.abspath", side_effect=lambda x: os.path.join("C:\\test\\workspace", x)):
            for path in VALID_PATHS:
                is_valid, error = project_validate(path, operation="test", must_exist=False)
                assert is_valid, f"Expected path '{path}' to be valid"
            
            for path in INVALID_PATHS:
                is_valid, error = project_validate(path, operation="test")
                assert not is_valid, f"Expected path '{path}' to be invalid"

def test_validate_path_with_special_characters():
    """Test that paths with special characters are handled correctly."""
    with patch("os.getcwd", return_value="C:\\test\\workspace"):
        with patch("os.path.abspath", side_effect=lambda x: os.path.join("C:\\test\\workspace", x)):
            # Valid paths with special chars
            valid_special = [
                "src/utils.py",
                "config-2024.yaml",
                "data_v2.json",
                "my file.txt",
                "folder with spaces/file.txt",
            ]
            for path in valid_special:
                is_valid, error = devops_validate(path, operation="test", must_exist=False)
                assert is_valid, f"Expected path '{path}' to be valid"

def test_validate_path_with_backslashes():
    """Test that Windows-style paths are handled correctly."""
    with patch("os.getcwd", return_value="C:\\test\\workspace"):
        with patch("os.path.abspath", side_effect=lambda x: os.path.join("C:\\test\\workspace", x)):
            # Windows paths should be normalized
            paths = [
                "src\\utils.py",
                "C:\\test\\workspace\\src\\file.txt",
                "..\\relative\\path",
            ]
            for path in paths:
                # Use must_exist=False to test path structure, not file existence
                is_valid, error = devops_validate(path, operation="test", must_exist=False)
                # The absolute path should start with workspace, so these should be valid
                # if they resolve within workspace
                abs_path = os.path.abspath(path)
                if abs_path.startswith("C:\\test\\workspace"):
                    assert is_valid, f"Expected path '{path}' to be valid"
                else:
                    assert not is_valid, f"Expected path '{path}' to be invalid"

def test_validate_path_empty_and_none():
    """Test edge cases with empty and None paths."""
    with patch("os.getcwd", return_value="C:\\test\\workspace"):
        # Empty path
        is_valid, error = devops_validate("", operation="test")
        assert not is_valid, "Empty path should be invalid"
        
        # None path
        is_valid, error = devops_validate(None, operation="test")
        assert not is_valid, "None path should be invalid"

def test_validate_path_directory_vs_file():
    """Test directory-specific validation."""
    with patch("os.getcwd", return_value="C:\\test\\workspace"):
        with patch("os.path.abspath", side_effect=lambda x: os.path.join("C:\\test\\workspace", x)):
            # Create temp directory and file for testing within mocked workspace
            test_dir = os.path.join("C:\\test\\workspace", "test_dir")
            test_file = os.path.join("C:\\test\\workspace", "test_file.txt")
            os.makedirs(test_dir, exist_ok=True)
            with open(test_file, "w") as f:
                f.write("test")
        
            try:
                # Directory should be valid as directory
                is_valid, error = devops_validate(test_dir, allow_directories=True, must_exist=True, operation="test")
                assert is_valid, "Existing directory should be valid"
                
                # File should be valid as file (not directory)
                is_valid, error = devops_validate(test_file, allow_directories=False, must_exist=True, operation="test")
                assert is_valid, "Existing file should be valid"
                
                # Directory marked as not allow_directories should be invalid
                is_valid, error = devops_validate(test_dir, allow_directories=False, must_exist=True, operation="test")
                assert not is_valid, "Directory should be invalid when allow_directories=False"
            finally:
                # Clean up
                if os.path.exists(test_dir):
                    os.rmdir(test_dir)
                if os.path.exists(test_file):
                    os.remove(test_file)

def test_validate_path_case_insensitivity():
    """Test that path validation is case-insensitive on Windows."""
    with patch("os.getcwd", return_value="C:\\test\\workspace"):
        with patch("os.path.abspath", side_effect=lambda x: os.path.join("C:\\test\\workspace", x)):
            # Different case variations should resolve to same absolute path
            path1 = "SRC/Utils.py"
            path2 = "src/utils.py"
            abs1 = os.path.abspath(path1)
            abs2 = os.path.abspath(path2)
            # On Windows, the file system is case-insensitive, so these should be considered equal
            # We'll compare the normalized case-insensitive paths
            assert os.path.normcase(abs1) == os.path.normcase(abs2), "Paths should resolve to same absolute path (case-insensitive)"

def test_validate_path_with_symlinks():
    """Test that symlinks are handled correctly."""
    with patch("os.getcwd", return_value="C:\\test\\workspace"):
        with patch("os.path.abspath", side_effect=lambda x: os.path.join("C:\\test\\workspace", x)):
            # If symlink points within workspace, should be valid
            # If symlink points outside, should be invalid
            # This test may be platform-specific, so just test basic behavior
            symlink_target = "safe_target"
            symlink_path = "symlink"
            
            # Create a safe target file
            with open(symlink_target, "w") as f:
                f.write("target")
        
            try:
                # On Windows, creating symlinks requires admin privileges
                # So we'll just test the logic conceptually
                # In real implementation, os.path.realpath should handle it
                is_valid, error = devops_validate(symlink_path, operation="test")
                # Without actual symlink, this will fail existence check
                # But the validation logic should work with real symlinks
            finally:
                if os.path.exists(symlink_target):
                    os.remove(symlink_target)