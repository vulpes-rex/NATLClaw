"""Tools for the python_developer skill — reuses dev file ops + shell."""

from __future__ import annotations

import os
import shlex
import subprocess
from typing import Annotated

# Allowed commands with their valid arguments and validation rules
ALLOWED_COMMANDS = {
    # Core file operations
    'ls': {
        'allowed_args': ['-l', '-a', '-h', '-r', '-t', '-R', '-F', '-1'],
        'allow_positional': True,
        'validate_positional': lambda arg: not arg.startswith('/') and '../' not in arg
    },
    'cat': {
        'allowed_args': ['-n', '-b', '-E', '-T'],
        'allow_positional': True,
        'validate_positional': lambda arg: not arg.startswith('/') and '../' not in arg
    },
    'grep': {
        'allowed_args': ['-i', '-v', '-r', '-n', '-c', '-l', '-H', '-h', '-A', '-B', '-C'],
        'allow_positional': True,
        'validate_positional': lambda arg: True  # pattern and file
    },
    'find': {
        'allowed_args': ['-name', '-type', '-mtime', '-size', '-user', '-group'],
        'allow_positional': True,
        'validate_positional': lambda arg: not arg.startswith('/') and '../' not in arg
    },
    'pwd': {
        'allowed_args': [],
        'allow_positional': False
    },
    'echo': {
        'allowed_args': [],
        'allow_positional': True,
        'validate_positional': lambda arg: True
    },
    # Directory operations
    'mkdir': {
        'allowed_args': ['-p', '-m'],
        'allow_positional': True,
        'validate_positional': lambda arg: not arg.startswith('/') and '../' not in arg
    },
    'rmdir': {
        'allowed_args': ['-p'],
        'allow_positional': True,
        'validate_positional': lambda arg: not arg.startswith('/') and '../' not in arg
    },
    # File operations
    'cp': {
        'allowed_args': ['-r', '-f', '-p', '-v'],
        'allow_positional': True,
        'validate_positional': lambda arg: not arg.startswith('/') and '../' not in arg
    },
    'mv': {
        'allowed_args': ['-f', '-i', '-v'],
        'allow_positional': True,
        'validate_positional': lambda arg: not arg.startswith('/') and '../' not in arg
    },
    'touch': {
        'allowed_args': [],
        'allow_positional': True,
        'validate_positional': lambda arg: not arg.startswith('/') and '../' not in arg
    },
    # System information
    'date': {
        'allowed_args': ['-u', '+%Y-%m-%d', '+%H:%M:%S'],
        'allow_positional': False
    },
    'whoami': {
        'allowed_args': [],
        'allow_positional': False
    },
    'uname': {
        'allowed_args': ['-a', '-r', '-n', '-m'],
        'allow_positional': False
    },
    # Text processing
    'head': {
        'allowed_args': ['-n', '-c'],
        'allow_positional': True,
        'validate_positional': lambda arg: not arg.startswith('/') and '../' not in arg
    },
    'tail': {
        'allowed_args': ['-n', '-c', '-f'],
        'allow_positional': True,
        'validate_positional': lambda arg: not arg.startswith('/') and '../' not in arg
    },
    'sort': {
        'allowed_args': ['-n', '-r', '-u'],
        'allow_positional': True,
        'validate_positional': lambda arg: not arg.startswith('/') and '../' not in arg
    },
    'uniq': {
        'allowed_args': ['-c', '-d', '-u'],
        'allow_positional': True,
        'validate_positional': lambda arg: not arg.startswith('/') and '../' not in arg
    },
    'wc': {
        'allowed_args': ['-l', '-w', '-c'],
        'allow_positional': True,
        'validate_positional': lambda arg: not arg.startswith('/') and '../' not in arg
    },
    # Network (basic - restricted for security)
    'ping': {
        'allowed_args': ['-c', '-n', '-q', '-i'],
        'allow_positional': True,
        'validate_positional': lambda arg: True  # host
    },
    'curl': {
        'allowed_args': ['-s', '-f', '-o', '-I', '-X'],
        'allow_positional': True,
        'validate_positional': lambda arg: arg.startswith('http://') or arg.startswith('https://')
    },
    'wget': {
        'allowed_args': ['-q', '-O', '-S', '-T'],
        'allow_positional': True,
        'validate_positional': lambda arg: arg.startswith('http://') or arg.startswith('https://')
    },
    # Development tools
    'npm': {
        'allowed_args': ['run', 'test', 'build', 'install', '-g', '--save', '--save-dev', '--global'],
        'allow_positional': True,
        'validate_positional': lambda arg: True  # script name
    },
    'pytest': {
        'allowed_args': ['-x', '-v', '-k', '--cov', '--junitxml', '--help'],
        'allow_positional': True,
        'validate_positional': lambda arg: True  # test files
    },
    'python': {
        'allowed_args': ['-m', '--version'],
        'allow_positional': True,
        'validate_positional': lambda arg: True
    },
    'pip': {
        'allowed_args': ['install', 'freeze', 'list'],
        'allow_positional': True,
        'validate_positional': lambda arg: True
    },
    'node': {
        'allowed_args': ['--version', '-e'],
        'allow_positional': True,
        'validate_positional': lambda arg: True
    },
    'npx': {
        'allowed_args': ['--version', 'test'],
        'allow_positional': True,
        'validate_positional': lambda arg: True
    },
    'git': {
        'allowed_args': ['--version', '-m', 'status', 'pull', 'push', 'clone', 'commit', 'add', 'branch'],
        'allow_positional': True,
        'validate_positional': lambda arg: True
    }
}

def _validate_path(
    path: str,
    allow_directories: bool = False,
    must_exist: bool = True,
    operation: str = "access"
) -> tuple[bool, str]:
    """
    Validate a file/directory path against workspace security restrictions.
    
    Returns:
        (is_valid, error_message)
    """
    # Get workspace root (current working directory)
    workspace = os.path.abspath(os.getcwd())
    
    # Resolve to absolute path
    try:
        abs_path = os.path.abspath(path)
    except Exception:
        return False, f"Invalid path format: {path}"
    
    # Check if path is within workspace (primary validation)
    if not abs_path.startswith(workspace):
        return False, f"{operation}: '{path}' is outside the workspace directory"
    
    # Additional check: ensure path doesn't contain redundant parent directory references
    # This catches cases like "dir/../secret" where the normalized path might be valid
    # but the original input is suspicious
    if path.replace("\\", "/").count("../") > 0:
        # Check if the path tries to go above workspace root
        common = os.path.commonpath([workspace, abs_path])
        if common != workspace:
            return False, f"{operation}: '{path}' contains path traversal attempt"
    
    # Check if path exists
    if must_exist and not os.path.exists(abs_path):
        return False, f"{operation}: Path '{path}' does not exist"
    
    # For directories, ensure it's actually a directory if required
    if allow_directories and must_exist and not os.path.isdir(abs_path):
        return False, f"{operation}: '{path}' is not a directory"
    if not allow_directories and must_exist and os.path.isdir(abs_path):
        return False, f"{operation}: '{path}' is a directory but directories are not allowed"
    
    return True, ""

def validate_and_execute_command(command: str, cwd: str = None) -> tuple[bool, str]:
    """
    Validate and execute a shell command using a whitelist approach.
    
    Returns:
        (success, output) where success is boolean and output is string
    """
    try:
        # Parse command into executable and arguments
        parts = shlex.split(command)
        if not parts:
            return False, "Empty command"
        
        executable = parts[0]
        args = parts[1:]
        
        # Check if executable is in whitelist
        if executable not in ALLOWED_COMMANDS:
            return False, f"Command '{executable}' is not allowed"
        
        cmd_info = ALLOWED_COMMANDS[executable]
        
        # Validate arguments
        positional_args = []
        i = 0
        while i < len(args):
            arg = args[i]
            # Check if it's a positional argument or an option
            if arg.startswith('-') or arg.startswith('+'):
                # Handle combined short options (e.g., -la = -l -a)
                if arg.startswith('--') or arg.startswith('+'):
                    # Long option or +format option, treat as single option
                    # Handle --option=value syntax
                    if '=' in arg and arg.startswith('--'):
                        opt_name = arg.split('=', 1)[0]
                        opt_val = arg.split('=', 1)[1]
                        options_to_check = [opt_name]
                        # Validate the value part for path security
                        if opt_val.startswith('/') and not opt_val.startswith('http'):
                            return False, f"Absolute path '{opt_val}' is not allowed in option value"
                        if '../' in opt_val:
                            return False, f"Path traversal in '{opt_val}' is not allowed in option value"
                    else:
                        options_to_check = [arg]
                elif arg in cmd_info['allowed_args']:
                    # Single-dash multi-char option that's explicitly allowed
                    # (e.g., -name for find, -mtime, etc.)
                    options_to_check = [arg]
                else:
                    # Short options may be combined: -la -> ['-l', '-a']
                    if len(arg) > 2:
                        # Split combined short options into individual ones
                        options_to_check = ['-' + opt for opt in arg[1:]]
                    else:
                        options_to_check = [arg]
                
                # Validate each option
                for opt in options_to_check:
                    if opt not in cmd_info['allowed_args']:
                        return False, f"Option '{opt}' is not allowed for command '{executable}'"
                
                # Check if next argument is required for any of these options
                # Only for dash-prefix options, not +format options like date +%Y-%m-%d
                if arg.startswith('-') and i + 1 < len(args) and not args[i+1].startswith('-'):
                    # Option with argument - validate the argument value
                    opt_arg = args[i + 1]
                    # Apply general path security even to option arguments
                    if opt_arg.startswith('/') and not opt_arg.startswith('http'):
                        return False, f"Absolute path '{opt_arg}' is not allowed"
                    if '../' in opt_arg:
                        return False, f"Path traversal in '{opt_arg}' is not allowed"
                    i += 1  # skip the option argument
                # else: option without argument is fine
            else:
                # Positional argument
                if not cmd_info['allow_positional']:
                    return False, f"Positional arguments are not allowed for command '{executable}'"
                if cmd_info.get('validate_positional') and not cmd_info['validate_positional'](arg):
                    return False, f"Invalid positional argument: '{arg}'"
                # General security check: block absolute paths and path traversal
                # (unless the arg is clearly not a filesystem path, e.g. URLs)
                if arg.startswith('/') and not arg.startswith('http'):
                    return False, f"Absolute path '{arg}' is not allowed"
                if '../' in arg:
                    return False, f"Path traversal in '{arg}' is not allowed"
                positional_args.append(arg)
            i += 1
        
        # Execute the command safely
        # We use shell=True for backward compatibility, but only after validating
        # the command against our whitelist. This maintains compatibility while
        # preventing command injection.
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=cwd or os.getcwd(),
            env=os.environ.copy()  # copy environment to avoid manipulation
        )
        
        output = result.stdout + result.stderr
        if len(output) > 5000:
            output = output[:5000] + "\n... (truncated)"
        return True, output or "(no output)"
    
    except subprocess.TimeoutExpired:
        return False, "Command timed out after 30 seconds"
    except FileNotFoundError:
        return False, f"Command '{executable}' not found"
    except Exception as e:
        return False, f"Error executing command: {str(e)}"

def list_files(
    directory: Annotated[str, "Relative directory path to list"] = ".",
) -> str:
    """List files and folders in a directory."""
    is_valid, error = _validate_path(directory, allow_directories=True, operation="list_files")
    if not is_valid:
        return error
    
    try:
        entries = os.listdir(directory)
        dirs = sorted(e + "/" for e in entries if os.path.isdir(os.path.join(directory, e)))
        files = sorted(e for e in entries if os.path.isfile(os.path.join(directory, e)))
        return "\n".join(dirs + files) or "(empty directory)"
    except Exception as e:
        return f"Error reading directory: {e}"


def read_source_file(
    path: Annotated[str, "Relative path to the file to read"],
) -> str:
    """Read the contents of a source file (max 200 lines)."""
    is_valid, error = _validate_path(path, operation="read_source_file")
    if not is_valid:
        return error
    
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > 200:
            return "".join(lines[:200]) + f"\n... ({len(lines) - 200} more lines)"
        return "".join(lines)
    except Exception as e:
        return f"Error reading file: {e}"


def write_source_file(
    path: Annotated[str, "Relative path to the file to write"],
    content: Annotated[str, "The full file content to write"],
) -> str:
    """Write content to a source file. Creates parent directories if needed."""
    is_valid, error = _validate_path(path, operation="write_source_file", must_exist=False)
    if not is_valid:
        return error
    
    try:
        # Ensure parent directory exists
        parent_dir = os.path.dirname(path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error writing file: {e}"


def run_shell_command(
    command: Annotated[str, "Shell command to execute (e.g. 'pytest')"],
) -> str:
    """Run a shell command and return stdout + stderr (max 5000 chars)."""
    success, output = validate_and_execute_command(command)
    if not success:
        return f"Blocked: {output}"
    return output
