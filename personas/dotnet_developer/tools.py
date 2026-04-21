"""Tools for the dotnet_developer persona — file ops, dotnet shell, ADO PR, TRX parsing."""

from __future__ import annotations

import os
import shlex
import subprocess
from typing import Annotated

ALLOWED_COMMANDS = {
    'ls': {
        'allowed_args': ['-l', '-a', '-h', '-r', '-t', '-R', '-F', '-1'],
        'allow_positional': True,
        'validate_positional': lambda arg: not arg.startswith('/') and '../' not in arg
    },
    'cat': {
        'allowed_args': ['-n', '-b'],
        'allow_positional': True,
        'validate_positional': lambda arg: not arg.startswith('/') and '../' not in arg
    },
    'grep': {
        'allowed_args': ['-i', '-v', '-r', '-n', '-c', '-l', '-H', '-h', '-A', '-B', '-C'],
        'allow_positional': True,
        'validate_positional': lambda arg: True
    },
    'find': {
        'allowed_args': ['-name', '-type', '-mtime'],
        'allow_positional': True,
        'validate_positional': lambda arg: not arg.startswith('/') and '../' not in arg
    },
    'pwd': {'allowed_args': [], 'allow_positional': False},
    'echo': {'allowed_args': [], 'allow_positional': True, 'validate_positional': lambda arg: True},
    # .NET toolchain
    'dotnet': {
        'allowed_args': [
            'build', 'test', 'run', 'publish', 'restore', 'format',
            'add', 'remove', 'list', 'pack', 'clean', 'watch',
            '--logger', '--configuration', '--framework', '--output',
            '--no-restore', '--no-build', '--verbosity',
            '-c', '-f', '-o', '-v',
        ],
        'allow_positional': True,
        'validate_positional': lambda arg: True,
    },
    # git (same as react_developer)
    'git': {
        'allowed_args': ['--version', '-m', '-b', '-u', 'status', 'pull', 'push',
                         'clone', 'commit', 'add', 'branch', 'checkout', 'log', 'diff'],
        'allow_positional': True,
        'validate_positional': lambda arg: True,
    },
    # nuget / general
    'nuget': {
        'allowed_args': ['restore', 'push', 'pack', 'locals'],
        'allow_positional': True,
        'validate_positional': lambda arg: True,
    },
}


def _validate_path(
    path: str,
    allow_directories: bool = False,
    must_exist: bool = True,
    operation: str = "access",
) -> tuple[bool, str]:
    workspace = os.path.abspath(os.getcwd())
    try:
        abs_path = os.path.abspath(path)
    except Exception:
        return False, f"Invalid path format: {path}"
    if not abs_path.startswith(workspace):
        return False, f"{operation}: '{path}' is outside the workspace directory"
    if '../' in path.replace("\\", "/"):
        common = os.path.commonpath([workspace, abs_path])
        if common != workspace:
            return False, f"{operation}: '{path}' contains path traversal attempt"
    if must_exist and not os.path.exists(abs_path):
        return False, f"{operation}: Path '{path}' does not exist"
    if allow_directories and must_exist and not os.path.isdir(abs_path):
        return False, f"{operation}: '{path}' is not a directory"
    if not allow_directories and must_exist and os.path.isdir(abs_path):
        return False, f"{operation}: '{path}' is a directory but directories are not allowed"
    return True, ""


def _validate_and_execute(command: str, cwd: str | None = None) -> tuple[bool, str]:
    try:
        parts = shlex.split(command)
        if not parts:
            return False, "Empty command"
        executable = parts[0]
        if executable not in ALLOWED_COMMANDS:
            return False, f"Command '{executable}' is not allowed"
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=120, cwd=cwd or os.getcwd(), env=os.environ.copy(),
        )
        output = result.stdout + result.stderr
        if len(output) > 5000:
            output = output[:5000] + "\n... (truncated)"
        return True, output or "(no output)"
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except Exception as exc:
        return False, f"Error: {exc}"


# ── File tools ─────────────────────────────────────────────────────────

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
    except Exception as exc:
        return f"Error reading directory: {exc}"


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
    except Exception as exc:
        return f"Error reading file: {exc}"


def write_source_file(
    path: Annotated[str, "Relative path to write"],
    content: Annotated[str, "Full file content to write"],
) -> str:
    """Write content to a source file. Creates parent directories if needed."""
    is_valid, error = _validate_path(path, operation="write_source_file", must_exist=False)
    if not is_valid:
        return error
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as exc:
        return f"Error writing file: {exc}"


def run_shell_command(
    command: Annotated[str, "Shell command to execute (e.g. 'dotnet test')"],
) -> str:
    """Run a shell command and return stdout + stderr (max 5000 chars)."""
    success, output = _validate_and_execute(command)
    if not success:
        return f"Blocked: {output}"
    return output


# ── TRX test result parser ──────────────────────────────────────────────

def get_test_results(
    trx_path: Annotated[str, "Path to the .trx XML file produced by 'dotnet test --logger trx'"],
) -> str:
    """Parse a .trx XML test result file and return a human-readable summary.

    Run 'dotnet test --logger trx' first; the .trx file is written to
    TestResults/ under the project directory.
    """
    import xml.etree.ElementTree as ET

    is_valid, error = _validate_path(trx_path, operation="get_test_results")
    if not is_valid:
        return error

    try:
        tree = ET.parse(trx_path)
        root = tree.getroot()
    except Exception as exc:
        return f"Failed to parse TRX file: {exc}"

    # TRX namespace
    ns = {"t": "http://microsoft.com/schemas/VisualStudio/TeamTest/2010"}

    # Summary counters
    counters = root.find(".//t:Counters", ns)
    if counters is not None:
        total = int(counters.get("total", 0))
        passed = int(counters.get("passed", 0))
        failed = int(counters.get("failed", 0))
        skipped = int(counters.get("notExecuted", 0))
    else:
        total = passed = failed = skipped = 0

    outcome_el = root.find(".//t:TestRun/t:ResultSummary", ns)
    if outcome_el is None:
        outcome_el = root.find("t:ResultSummary", ns)
    outcome = (outcome_el.get("outcome") if outcome_el is not None else "Unknown")

    lines = [
        f".NET test results: {outcome}",
        f"Tests: {passed}/{total} passed, {failed} failed, {skipped} skipped",
    ]

    # Surface failing tests
    failures: list[str] = []
    for result in root.findall(".//t:UnitTestResult", ns):
        if result.get("outcome") in ("Failed", "Error"):
            name = result.get("testName", "unknown")
            msg_el = result.find(".//t:Message", ns)
            trace_el = result.find(".//t:StackTrace", ns)
            msg = (msg_el.text or "")[:200] if msg_el is not None else ""
            trace = (trace_el.text or "")[:300] if trace_el is not None else ""
            failures.append(f"  FAIL: {name}\n       {msg}\n       {trace}".rstrip())

    if failures:
        lines.append("\nFailing tests:")
        lines.extend(failures[:10])
        if len(failures) > 10:
            lines.append(f"  ... and {len(failures) - 10} more")

    return "\n".join(lines)


# ── ADO pull request tools ─────────────────────────────────────────────

def _ado_connector_from_env():
    """Build an AzureDevOpsConnector from environment variables."""
    try:
        from connectors.ado import AzureDevOpsConnector
        url = os.getenv("ADO_URL", "")
        pat = os.getenv("ADO_PAT", "")
        project = os.getenv("ADO_PROJECT", "")
        team = os.getenv("ADO_TEAM", "")
        if not (url and pat and project):
            return None
        return AzureDevOpsConnector(url=url, pat=pat, project=project, team=team)
    except Exception:
        return None


def create_pull_request(
    title: Annotated[str, "PR title (concise, under 70 chars)"],
    source_branch: Annotated[str, "Source branch name, e.g. 'feature/auth-refactor'"],
    description: Annotated[str, "PR description / body (Markdown supported)"] = "",
    work_item_ids: Annotated[str, "Comma-separated ADO work item IDs to link, e.g. '4821'"] = "",
    repository: Annotated[str, "Git repository name in ADO"] = "",
    target_branch: Annotated[str, "Target branch (default: 'main')"] = "main",
) -> str:
    """Open a pull request in Azure DevOps.

    IMPORTANT: You open PRs — you NEVER merge them. Human reviews and merges.
    Call this only after 'dotnet test' passes.
    """
    connector = _ado_connector_from_env()
    if connector is None:
        return "ADO not configured (ADO_URL / ADO_PAT / ADO_PROJECT env vars missing)"

    wi_ids: list[int] = [int(p.strip()) for p in work_item_ids.split(",")
                         if p.strip().isdigit()]
    try:
        pr = connector.create_pull_request(
            repository=repository,
            title=title,
            source_branch=source_branch,
            target_branch=target_branch,
            description=description,
            work_item_ids=wi_ids or None,
        )
        if pr is None:
            return "PR creation failed — check ADO credentials and repository name"
        return (
            f"PR #{pr.id} created: {pr.title}\n"
            f"URL: {pr.url}\n"
            f"Status: {pr.status}\n"
            f"Branch: {pr.source_branch} -> {pr.target_branch}"
        )
    except Exception as exc:
        return f"PR creation error: {exc}"


def get_pull_request_status(
    pr_id: Annotated[int, "ADO pull request ID"],
    repository: Annotated[str, "Git repository name in ADO"],
) -> str:
    """Fetch the status of an existing pull request."""
    connector = _ado_connector_from_env()
    if connector is None:
        return "ADO not configured"
    try:
        pr = connector.get_pull_request(repository=repository, pr_id=pr_id)
        if pr is None:
            return f"PR #{pr_id} not found"
        return (
            f"PR #{pr.id}: {pr.title}\n"
            f"Status: {pr.status}\n"
            f"URL: {pr.url}\n"
            f"Branch: {pr.source_branch} -> {pr.target_branch}"
        )
    except Exception as exc:
        return f"Error fetching PR: {exc}"
