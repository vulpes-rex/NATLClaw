"""Tools for the qa_engineer persona.

Covers file reading/writing, test execution, result parsing, and reporting.
Git access is read-only (diff/log/status) — QA never commits or pushes.
"""

from __future__ import annotations

import os
import subprocess
import shlex
from typing import Annotated

WORKSPACE = os.path.abspath(os.getcwd())

# Read-only git + test runners only — no push/commit/checkout
ALLOWED_COMMANDS = {
    'ls': {
        'allowed_args': ['-l', '-a', '-h', '-r', '-t', '-R', '-F', '-1'],
        'allow_positional': True,
        'validate_positional': lambda arg: not arg.startswith('/') and '../' not in arg,
    },
    'cat': {
        'allowed_args': ['-n', '-b'],
        'allow_positional': True,
        'validate_positional': lambda arg: not arg.startswith('/') and '../' not in arg,
    },
    'grep': {
        'allowed_args': ['-i', '-v', '-r', '-n', '-c', '-l', '-H', '-h', '-A', '-B', '-C'],
        'allow_positional': True,
        'validate_positional': lambda arg: True,
    },
    'find': {
        'allowed_args': ['-name', '-type', '-mtime'],
        'allow_positional': True,
        'validate_positional': lambda arg: not arg.startswith('/') and '../' not in arg,
    },
    # Test runners
    'npm': {
        'allowed_args': ['test', 'run', '--', '--coverage', '--json', '--watchAll=false',
                         '--testPathPattern', '--verbose', '--silent'],
        'allow_positional': True,
        'validate_positional': lambda arg: True,
    },
    'dotnet': {
        'allowed_args': ['test', '--logger', '--configuration', '--framework',
                         '--no-build', '--verbosity', '-c', '-f', '-v'],
        'allow_positional': True,
        'validate_positional': lambda arg: True,
    },
    'python': {
        'allowed_args': ['-m', '--version'],
        'allow_positional': True,
        'validate_positional': lambda arg: True,
    },
    'pytest': {
        'allowed_args': ['-x', '-v', '-q', '-k', '--tb', '--cov', '--cov-report',
                         '--junitxml', '-s', '--no-header', '-p'],
        'allow_positional': True,
        'validate_positional': lambda arg: True,
    },
    # Read-only git — positional subcommands are whitelisted
    'git': {
        'allowed_args': ['--stat', '--oneline', '--name-only', '--name-status',
                         '-p', '--format', '-n', '--since', '--until', '--author'],
        'allow_positional': True,
        'validate_positional': lambda arg: arg in (
            'status', 'diff', 'log', 'show',
            # refs / branch names are safe to pass as positionals
        ) or arg.startswith('-') or (
            # allow commit SHAs, branch names, file paths as diff refs
            not arg in ('push', 'commit', 'add', 'checkout', 'merge',
                        'rebase', 'reset', 'clean', 'rm', 'mv', 'pull', 'clone', 'fetch')
        ),
    },
}


_GIT_BLOCKED = frozenset({
    "push", "commit", "add", "checkout", "merge", "rebase",
    "reset", "clean", "rm", "mv", "pull", "clone", "fetch", "stash",
})


def _validate_and_execute(command: str) -> tuple[bool, str]:
    try:
        parts = shlex.split(command)
        if not parts:
            return False, "Empty command"
        executable = parts[0]
        if executable not in ALLOWED_COMMANDS:
            return False, f"Command '{executable}' is not allowed for QA persona"
        # Block destructive git subcommands
        if executable == "git" and len(parts) > 1:
            subcommand = parts[1]
            if subcommand in _GIT_BLOCKED:
                return False, f"'git {subcommand}' is blocked — QA persona has read-only git access"
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=120, cwd=WORKSPACE, env=os.environ.copy(),
        )
        output = result.stdout + result.stderr
        if len(output) > 6000:
            output = output[:6000] + "\n... (truncated)"
        return True, output or "(no output)"
    except subprocess.TimeoutExpired:
        return False, "Command timed out (120s)"
    except Exception as exc:
        return False, f"Error: {exc}"


def _validate_path(
    path: str,
    allow_directories: bool = False,
    must_exist: bool = True,
    operation: str = "access",
) -> tuple[bool, str]:
    try:
        abs_path = os.path.abspath(path)
    except Exception:
        return False, f"Invalid path: {path}"
    if not abs_path.startswith(WORKSPACE):
        return False, f"{operation}: '{path}' is outside the workspace"
    if '../' in path.replace("\\", "/"):
        common = os.path.commonpath([WORKSPACE, abs_path])
        if common != WORKSPACE:
            return False, f"{operation}: path traversal not allowed"
    if must_exist and not os.path.exists(abs_path):
        return False, f"{operation}: '{path}' does not exist"
    if allow_directories and must_exist and not os.path.isdir(abs_path):
        return False, f"{operation}: '{path}' is not a directory"
    if not allow_directories and must_exist and os.path.isdir(abs_path):
        return False, f"{operation}: '{path}' is a directory"
    return True, ""


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
        return f"Error: {exc}"


def read_source_file(
    path: Annotated[str, "Relative path to the file to read"],
) -> str:
    """Read the contents of a source file (max 300 lines)."""
    is_valid, error = _validate_path(path, operation="read_source_file")
    if not is_valid:
        return error
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > 300:
            return "".join(lines[:300]) + f"\n... ({len(lines) - 300} more lines)"
        return "".join(lines)
    except Exception as exc:
        return f"Error reading file: {exc}"


def write_source_file(
    path: Annotated[str, "Relative path to write (test files only)"],
    content: Annotated[str, "Full file content to write"],
) -> str:
    """Write a test file. Creates parent directories if needed."""
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


# ── Git read tools ─────────────────────────────────────────────────────

def read_git_diff(
    ref: Annotated[str, "Git ref to diff against (e.g. HEAD~1, main, a1b2c3)"] = "HEAD~1",
) -> str:
    """Read a git diff to understand what changed."""
    safe_ref = "".join(c for c in ref if c.isalnum() or c in "~^.-_/")
    if not safe_ref:
        return "Invalid git ref"
    try:
        result = subprocess.run(
            ["git", "diff", safe_ref, "--stat", "-p"],
            capture_output=True, cwd=WORKSPACE, timeout=10,
        )
        stdout = result.stdout.decode("utf-8", errors="replace")
        return stdout[:6000] or "(no diff)"
    except Exception as exc:
        return f"git diff failed: {exc}"


def read_git_log(
    count: Annotated[int, "Number of recent commits to show"] = 10,
) -> str:
    """Read recent git commits."""
    try:
        result = subprocess.run(
            ["git", "log", f"-{min(count, 50)}", "--oneline", "--stat"],
            capture_output=True, cwd=WORKSPACE, timeout=10,
        )
        stdout = result.stdout.decode("utf-8", errors="replace")
        return stdout[:4000] or "(no commits)"
    except Exception as exc:
        return f"git log failed: {exc}"


def run_shell_command(
    command: Annotated[str, "Test command to run (e.g. 'pytest tests/ -q --tb=short')"],
) -> str:
    """Run a test command. Only test runners and read-only git are allowed."""
    success, output = _validate_and_execute(command)
    if not success:
        return f"Blocked: {output}"
    return output


# ── Test result parsers ────────────────────────────────────────────────

def parse_test_results(
    output: Annotated[str, "Raw test output text or JSON string"],
    framework: Annotated[str, "Test framework: 'pytest', 'jest_json', 'trx', or 'auto'"] = "auto",
) -> str:
    """Parse test output and return a human-readable summary.

    - ``pytest`` — plain text pytest output (``-q --tb=short``)
    - ``jest_json`` — JSON from ``npm test -- --json``
    - ``trx`` — file path to a ``.trx`` XML file from ``dotnet test --logger trx``
    - ``auto`` — detect from content
    """
    if framework == "auto":
        if output.strip().startswith("{"):
            framework = "jest_json"
        elif output.strip().endswith(".trx") or output.strip().startswith("<?xml"):
            framework = "trx"
        else:
            framework = "pytest"

    if framework == "jest_json":
        return _parse_jest(output)
    if framework == "trx":
        return _parse_trx(output)  # output is the file path OR raw XML
    return _parse_pytest(output)


def _parse_pytest(output: str) -> str:
    """Parse pytest -q --tb=short output."""
    lines = output.splitlines()
    summary_line = ""
    failures: list[str] = []
    in_failure = False
    current_failure: list[str] = []

    for line in lines:
        # Summary: "5 passed, 1 failed in 0.12s"
        if "passed" in line or "failed" in line or "error" in line.lower():
            if line.startswith("=") or ("passed" in line and "in" in line):
                summary_line = line.strip("= \n")
        # Failure block markers
        if line.startswith("FAILED ") or line.startswith("ERROR "):
            if current_failure:
                failures.append("\n".join(current_failure[:8]))
            current_failure = [line.strip()]
            in_failure = True
        elif in_failure and line.strip():
            current_failure.append("  " + line.strip())

    if current_failure:
        failures.append("\n".join(current_failure[:8]))

    result_lines = [f"pytest results: {summary_line or '(see output)'}"]
    if failures:
        result_lines.append("\nFailing tests:")
        result_lines.extend(failures[:10])
        if len(failures) > 10:
            result_lines.append(f"  ... and {len(failures) - 10} more")
    return "\n".join(result_lines)


def _parse_jest(json_output: str) -> str:
    """Parse Jest --json output."""
    import json as _json
    try:
        data = _json.loads(json_output)
    except _json.JSONDecodeError as exc:
        return f"Invalid JSON: {exc}"

    passed = data.get("numPassedTests", 0)
    failed = data.get("numFailedTests", 0)
    total = data.get("numTotalTests", 0)
    success = data.get("success", False)

    lines = [
        f"Jest results: {'PASS' if success else 'FAIL'}",
        f"Tests: {passed}/{total} passed, {failed} failed",
    ]
    for suite in data.get("testResults", []):
        for result in suite.get("testResults", []):
            if result.get("status") == "failed":
                name = result.get("fullName") or result.get("title", "unknown")
                msg = (result.get("failureMessages") or [""])[0][:200]
                lines.append(f"  FAIL: {name}\n       {msg}")
                if len(lines) > 20:
                    lines.append("  ...")
                    break
    return "\n".join(lines)


def _parse_trx(path_or_xml: str) -> str:
    """Parse a .trx file path or raw XML string."""
    import xml.etree.ElementTree as ET

    if path_or_xml.strip().endswith(".trx"):
        try:
            tree = ET.parse(path_or_xml.strip())
            root = tree.getroot()
        except Exception as exc:
            return f"Failed to parse TRX file: {exc}"
    else:
        try:
            root = ET.fromstring(path_or_xml)
        except Exception as exc:
            return f"Failed to parse TRX XML: {exc}"

    ns = {"t": "http://microsoft.com/schemas/VisualStudio/TeamTest/2010"}
    counters = root.find(".//t:Counters", ns)
    if counters is not None:
        total = int(counters.get("total", 0))
        passed = int(counters.get("passed", 0))
        failed = int(counters.get("failed", 0))
    else:
        total = passed = failed = 0

    summary_el = root.find("t:ResultSummary", ns)
    outcome = summary_el.get("outcome", "Unknown") if summary_el is not None else "Unknown"

    lines = [
        f".NET test results: {outcome}",
        f"Tests: {passed}/{total} passed, {failed} failed",
    ]
    for result in root.findall(".//t:UnitTestResult", ns):
        if result.get("outcome") in ("Failed", "Error"):
            name = result.get("testName", "unknown")
            msg_el = result.find(".//t:Message", ns)
            msg = (msg_el.text or "")[:200] if msg_el is not None else ""
            lines.append(f"  FAIL: {name}\n       {msg}")
            if len(lines) > 20:
                lines.append("  ...")
                break
    return "\n".join(lines)


# ── Reporting tools ────────────────────────────────────────────────────

def post_test_report(
    summary: Annotated[str, "One-line test result summary (e.g. '12/14 passed, 2 failed')"],
    details: Annotated[str, "Full test output or parsed results to include in the report"],
    work_item_id: Annotated[int, "ADO work item ID to post the report to (0 = inbox only)"] = 0,
    persona_name: Annotated[str, "Your persona name (default: qa_engineer)"] = "qa_engineer",
) -> str:
    """Post a test report to the inbox and optionally to an ADO work item comment.

    Always posts to the inbox. When ``work_item_id`` is non-zero and ADO is
    configured, also posts a comment on the work item.
    """
    body = f"## Test Report\n\n**{summary}**\n\n```\n{details[:3000]}\n```"
    if len(details) > 3000:
        body += "\n\n*(output truncated — see full run for details)*"

    # Post to inbox
    inbox_result = "inbox: failed"
    try:
        from messaging import create_message, append_and_save_inbox
        import asyncio

        msg = create_message(
            "fyi",
            title=f"QA Report: {summary}",
            body=body,
            urgency="normal" if "failed" not in summary.lower() else "high",
            persona=persona_name,
            payload={"work_item_id": work_item_id, "summary": summary},
        )
        msg.sender = persona_name
        msg.conversation_type = "escalation" if "failed" in summary.lower() else ""

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    pool.submit(asyncio.run, append_and_save_inbox(msg, None)).result(timeout=5)
            else:
                loop.run_until_complete(append_and_save_inbox(msg, None))
        except RuntimeError:
            asyncio.run(append_and_save_inbox(msg, None))
        inbox_result = f"inbox: delivered (id={msg.id})"
    except Exception as exc:
        inbox_result = f"inbox: error — {exc}"

    # Post to ADO if configured and work_item_id given
    ado_result = ""
    if work_item_id:
        try:
            from connectors.ado import AzureDevOpsConnector
            url = os.getenv("ADO_URL", "")
            pat = os.getenv("ADO_PAT", "")
            project = os.getenv("ADO_PROJECT", "")
            team = os.getenv("ADO_TEAM", "")
            if url and pat and project:
                connector = AzureDevOpsConnector(url=url, pat=pat, project=project, team=team)
                comment_text = f"**QA Report**\n\n{summary}\n\n{details[:1500]}"
                ok = connector.add_comment(work_item_id, comment_text)
                ado_result = f", ADO #{work_item_id}: {'posted' if ok else 'failed'}"
            else:
                ado_result = ", ADO: not configured"
        except Exception as exc:
            ado_result = f", ADO: error — {exc}"

    return f"Report posted: {inbox_result}{ado_result}"


def get_work_item_details(
    work_item_id: Annotated[int, "ADO work item ID"],
) -> str:
    """Fetch a work item's title, description, and acceptance criteria from ADO."""
    try:
        from connectors.ado import AzureDevOpsConnector
        url = os.getenv("ADO_URL", "")
        pat = os.getenv("ADO_PAT", "")
        project = os.getenv("ADO_PROJECT", "")
        team = os.getenv("ADO_TEAM", "")
        if not (url and pat and project):
            return "ADO not configured (ADO_URL / ADO_PAT / ADO_PROJECT env vars missing)"
        connector = AzureDevOpsConnector(url=url, pat=pat, project=project, team=team)
        wi = connector.get_work_item(work_item_id)
        if wi is None:
            return f"Work item #{work_item_id} not found"
        lines = [
            f"ADO #{wi.id}: {wi.title}",
            f"Type: {wi.type} | State: {wi.state} | Priority: {wi.priority}",
        ]
        if wi.description:
            lines.append(f"\nDescription:\n{wi.description[:800]}")
        if wi.acceptance_criteria:
            lines.append(f"\nAcceptance Criteria:\n{wi.acceptance_criteria[:800]}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Error fetching work item: {exc}"
