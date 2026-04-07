"""Tools for the python_developer skill — reuses dev file ops + shell."""
from __future__ import annotations

import os
import subprocess
from typing import Annotated


def list_files(
    directory: Annotated[str, "Relative directory path to list"] = ".",
) -> str:
    """List files and folders in a directory."""
    try:
        entries = os.listdir(directory)
        dirs = sorted(e + "/" for e in entries if os.path.isdir(os.path.join(directory, e)))
        files = sorted(e for e in entries if os.path.isfile(os.path.join(directory, e)))
        return "\n".join(dirs + files) or "(empty directory)"
    except FileNotFoundError:
        return f"Directory not found: {directory}"


def read_source_file(
    path: Annotated[str, "Relative path to the file to read"],
) -> str:
    """Read the contents of a source file (max 200 lines)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > 200:
            return "".join(lines[:200]) + f"\n... ({len(lines) - 200} more lines)"
        return "".join(lines)
    except FileNotFoundError:
        return f"File not found: {path}"
    except UnicodeDecodeError:
        return f"Cannot read binary file: {path}"


def write_source_file(
    path: Annotated[str, "Relative path to the file to write"],
    content: Annotated[str, "The full file content to write"],
) -> str:
    """Write content to a source file. Creates parent directories if needed."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"Wrote {len(content)} bytes to {path}"


def run_shell_command(
    command: Annotated[str, "Shell command to execute (e.g. 'pytest')"],
) -> str:
    """Run a shell command and return stdout + stderr (max 5000 chars)."""
    blocked = ("rm -rf /", "format ", "del /s /q", "rmdir /s")
    if any(b in command.lower() for b in blocked):
        return "Blocked: this command is not allowed."
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=30, cwd=os.getcwd(),
        )
        output = result.stdout + result.stderr
        if len(output) > 5000:
            output = output[:5000] + "\n... (truncated)"
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        return "Command timed out after 30 seconds."
