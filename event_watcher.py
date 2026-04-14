"""Lightweight workspace event watcher.

Watches a directory tree and pushes file-change events to an event queue.
The queue is consumed by the scheduler to trigger immediate heartbeats.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Global event queue (set by scheduler)
_event_queue: asyncio.PriorityQueue[tuple[int, str, dict]] | None = None

# Directories to ignore while watching
_IGNORE_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", "venv", ".venv",
    "dist", "build", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "data",  # don't watch our own event queue
})

# File patterns to ignore
_IGNORE_SUFFIXES = frozenset({
    ".pyc", ".pyo", ".tmp", ".swp", ".swo", ".log",
})

# ──────────────────────────────────────────────────────────────────────
# Event queue writer
# ──────────────────────────────────────────────────────────────────────

def _should_ignore(filepath: str) -> bool:
    """Return True if a file path should be ignored by the watcher."""
    p = Path(filepath)
    # Check directory components
    for part in p.parts:
        if part in _IGNORE_DIRS:
            return True
    # Check file suffix
    if p.suffix in _IGNORE_SUFFIXES:
        return True
    return False


# Priority levels for events
from event_config import EVENT_PRIORITY

def _push_event(event_type: str, payload: dict | None = None) -> None:
    """Push an event to the global event queue (thread-safe)."""
    if _event_queue is None:
        logger.warning("Event queue not initialized: %s", event_type)
        return
    # Determine priority; default to low (3) if not specified
    priority = EVENT_PRIORITY.get(event_type, 3)
    # Ensure payload is a dict (convert None to empty dict)
    payload_dict = payload if payload is not None else {}
    try:
        _event_queue.put_nowait((priority, event_type, payload_dict))
    except asyncio.QueueFull:
        logger.warning("Event queue full, dropping event: %s", event_type)


def _push_event_nowait(event_type: str, payload: dict | None = None) -> None:
    """Push an event without blocking (may raise QueueFull)."""
    if _event_queue is None:
        raise ValueError("Event queue not initialized")
    priority = EVENT_PRIORITY.get(event_type, 3)
    payload_dict = payload if payload is not None else {}
    _event_queue.put_nowait((priority, event_type, payload_dict))


# ──────────────────────────────────────────────────────────────────────
# File watcher — uses watchdog if available, else polling fallback
# ──────────────────────────────────────────────────────────────────────

class EventWatcher:
    """Watch a directory tree and push file-change events to an event queue."""

    def __init__(self, watch_path: str = ".", event_queue: asyncio.PriorityQueue[tuple[int, str, dict]] | None = None):
        self.watch_path = os.path.abspath(watch_path)
        self._observer: Any = None
        self._polling = False
        self._stop = False
        global _event_queue
        _event_queue = event_queue  # type: ignore

    def _push_event(self, event_type: str, payload: dict | None = None) -> None:
        """Push an event to the event queue (thread-safe)."""
        if _event_queue is None:
            logger.warning("Event queue not initialized: %s", event_type)
            return
        # Determine priority; default to low (3) if not specified
        priority = EVENT_PRIORITY.get(event_type, 3)
        # Ensure payload is a dict (convert None to empty dict)
        payload_dict = payload if payload is not None else {}
        try:
            _event_queue.put_nowait((priority, event_type, payload_dict))
        except asyncio.QueueFull:
            logger.warning("Event queue full, dropping event: %s", event_type)

    def start(self) -> None:
        """Start watching — uses watchdog if available, else polling."""
        self._stop = False
        self._start_watchdog()

    def stop(self) -> None:
        """Stop the watcher."""
        self._stop = True
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
        self._polling = False

    # ── watchdog backend ──

    def _start_watchdog(self) -> None:
        """Start the watcher using watchdog (if available)."""
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler, FileSystemEvent
        except ImportError:
            logger.info("watchdog not installed — using polling fallback (30s interval)")
            self._start_polling()
            return

        watcher = self  # capture for inner class

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event: FileSystemEvent) -> None:
                if event.is_directory:
                    return
                src = event.src_path
                if _should_ignore(src):
                    return
                try:
                    rel = os.path.relpath(src, watcher.watch_path)
                except ValueError:
                    rel = src
                watcher._push_event("file_change", {
                    "type": event.event_type,  # created, modified, deleted, moved
                    "path": rel.replace("\\", "/"),
                    "ts": datetime.now(timezone.utc).isoformat(),
                })

        observer = Observer()
        observer.schedule(_Handler(), self.watch_path, recursive=True)
        observer.daemon = True
        observer.start()
        self._observer = observer
        logger.info("Event watcher started (watchdog) — watching %s", self.watch_path)

    # ── polling fallback ──

    def _start_polling(self) -> None:
        """Simple mtime-based polling for when watchdog is not installed."""
        self._polling = True
        self._snapshot: dict[str, float] = self._take_snapshot()

    def _take_snapshot(self) -> dict[str, float]:
        """Return {relative_path: mtime} for all tracked files."""
        snap: dict[str, float] = {}
        for root, dirs, files in os.walk(self.watch_path):
            dirs[:] = [d for d in dirs if d not in _IGNORE_DIRS]
            for fname in files:
                fp = Path(root) / fname
                if _should_ignore(str(fp)):
                    continue
                try:
                    rel = fp.relative_to(self.watch_path)
                    snap[str(rel).replace("\\", "/")] = fp.stat().st_mtime
                except (OSError, ValueError):
                    continue
        return snap

    def poll_once(self) -> int:
        """Compare current state to snapshot, push events, return count."""
        if not self._polling:
            return 0
        new_snap = self._take_snapshot()
        count = 0
        now = datetime.now(timezone.utc).isoformat()

        # New or modified files
        for path, mtime in new_snap.items():
            old_mtime = self._snapshot.get(path)
            if old_mtime is None:
                self._push_event("file_created", {"path": path})
                count += 1
            elif mtime > old_mtime:
                self._push_event("file_modified", {"path": path})
                count += 1

        # Deleted files
        for path in self._snapshot:
            if path not in new_snap:
                self._push_event("file_deleted", {"path": path})
                count += 1

        self._snapshot = new_snap
        return count


# ──────────────────────────────────────────────────────────────────────
# Cross-process event signaling (CLI → scheduler)
# ──────────────────────────────────────────────────────────────────────

_PENDING_EVENTS_FILE = Path("data") / "pending_events.ndjson"


def enqueue_event(event_type: str, payload: dict | None = None) -> None:
    """Write an event to the pending-events file for the scheduler to pick up.

    This is the cross-process entry point — CLI commands call this to
    signal the running scheduler without needing shared memory.
    """
    _PENDING_EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    priority = EVENT_PRIORITY.get(event_type, 3)
    record = {
        "priority": priority,
        "event_type": event_type,
        "payload": payload or {},
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    with open(_PENDING_EVENTS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def drain_pending_events(
    event_queue: asyncio.PriorityQueue[tuple[int, str, dict]],
) -> int:
    """Read and clear pending events from the file, pushing them to the queue.

    Returns the number of events enqueued (after in-batch dedupe).
    """
    if not _PENDING_EVENTS_FILE.exists():
        return 0
    count = 0
    seen: set[tuple[int, str, str]] = set()
    try:
        lines = _PENDING_EVENTS_FILE.read_text(encoding="utf-8").splitlines()
        # Clear the file immediately to avoid double-processing
        _PENDING_EVENTS_FILE.write_text("", encoding="utf-8")
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                priority = int(record["priority"])
                event_type = str(record["event_type"])
                payload = record.get("payload", {})
                if not isinstance(payload, dict):
                    payload = {}

                # Idempotency hardening: replayed identical records in one drain
                # cycle are treated as one logical event.
                payload_key = json.dumps(payload, sort_keys=True, ensure_ascii=False)
                fingerprint = (priority, event_type, payload_key)
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)

                event_queue.put_nowait((priority, event_type, payload))
                count += 1
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
                logger.warning("Skipping malformed pending event: %s", e)
    except OSError as e:
        logger.warning("Failed to drain pending events: %s", e)
    return count


# ──────────────────────────────────────────────────────────────────────
# Git hook helpers
# ──────────────────────────────────────────────────────────────────────

def append_git_commit_event(
    commit_hash: str,
    message: str,
    files_changed: list[str] | None = None,
) -> None:
    """Push a git commit event to the queue.

    Called from a git post-commit hook or manually.
    """
    _push_event("git_commit", {
        "hash": commit_hash[:12],
        "message": message[:200],
        "files": (files_changed or [])[:20],
        "ts": datetime.now(timezone.utc).isoformat(),
    })


def install_git_hook(repo_path: str = ".") -> str:
    """Install or update the post-commit hook to queue events.

    Returns a status message.
    """
    hooks_dir = Path(repo_path) / ".git" / "hooks"
    if not hooks_dir.parent.exists():
        return f"Not a git repository: {repo_path}"

    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "post-commit"

    hook_script = """#!/bin/sh
# NATLClaw post-commit hook — queues commit events for the workspace observer.
# Auto-generated by: natl watch install-hook

HASH=$(git rev-parse --short HEAD)
MSG=$(git log -1 --pretty=%s HEAD)
FILES=$(git diff-tree --no-commit-id --name-only -r HEAD | head -20 | tr '\\n' ',')

python -c "
import sys; sys.path.insert(0, '.')
from event_watcher import append_git_commit_event
append_git_commit_event('$HASH', '$MSG', '$FILES'.rstrip(',').split(','))
" 2>/dev/null || true
"""

    # Check if hook already exists
    if hook_path.exists():
        content = hook_path.read_text(encoding="utf-8", errors="ignore")
        if "NATLClaw" in content:
            return "Git hook already installed."
        # Append to existing hook
        with open(hook_path, "a", encoding="utf-8") as f:
            f.write("\n" + hook_script)
        return f"Appended NATLClaw hook to existing {hook_path}"

    hook_path.write_text(hook_script, encoding="utf-8")
    # Make executable on Unix
    if sys.platform != "win32":
        hook_path.chmod(0o755)
    return f"Installed git hook: {hook_path}"


# ──────────────────────────────────────────────────────────────────────
# PID-based background process management
# ──────────────────────────────────────────────────────────────────────

def _write_pid() -> None:
    """Write the current PID to the PID file."""
    PID_DIR = Path("data")
    PID_DIR.mkdir(parents=True, exist_ok=True)
    (PID_DIR / "watcher.pid").write_text(str(os.getpid()), encoding="utf-8")


def _read_pid() -> int | None:
    """Read the PID from the PID file, or None if not found/invalid."""
    try:
        return int((Path("data") / "watcher.pid").read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


def _clear_pid() -> None:
    """Remove the PID file."""
    try:
        (Path("data") / "watcher.pid").unlink(missing_ok=True)
    except OSError:
        pass


def _is_pid_alive(pid: int) -> bool:
    """Check whether a process with the given PID exists.

    On Windows, ``os.kill(pid, 0)`` calls ``OpenProcess`` with
    ``PROCESS_ALL_ACCESS`` which fails under Microsoft Store Python's
    AppContainer sandbox (WinError 87).  We use ``ctypes`` to call
    ``OpenProcess`` with the minimal ``PROCESS_QUERY_LIMITED_INFORMATION``.
    """
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    # Unix — os.kill(pid, 0) is safe and reliable
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def is_watcher_running() -> bool:
    """Check if a watcher process is alive."""
    pid = _read_pid()
    if pid is None:
        return False
    if _is_pid_alive(pid):
        return True
    _clear_pid()
    return False


def start_background_watcher(watch_path: str = ".") -> None:
    """Launch the watcher as a background process."""
    if is_watcher_running():
        print("Watcher is already running (PID %s)." % _read_pid())
        return

    # Launch a detached subprocess running this module
    if sys.platform == "win32":
        # Prefer pythonw.exe (no console window) if available
        exe = sys.executable
        pythonw = exe.replace("python.exe", "pythonw.exe")
        if os.path.isfile(pythonw):
            exe = pythonw
        cmd = [exe, "-m", "event_watcher", "--daemon", watch_path]
        CREATE_NO_WINDOW = 0x08000000
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        subprocess.Popen(
            cmd,
            creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        cmd = [sys.executable, "-m", "event_watcher", "--daemon", watch_path]
        subprocess.Popen(
            cmd,
            start_new_session=True,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    # Give the child a moment to write its PID
    time.sleep(0.5)
    pid = _read_pid()
    if pid:
        print(f"Watcher started (PID {pid}).")
    else:
        print("Watcher started.")


def _terminate_pid(pid: int) -> bool:
    """Terminate a process by PID, returning True on success.

    Uses ``ctypes`` on Windows to avoid ``os.kill`` AppContainer issues.
    """
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        PROCESS_TERMINATE = 0x0001
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if handle:
            result = kernel32.TerminateProcess(handle, 1)
            kernel32.CloseHandle(handle)
            return bool(result)
        return False
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except (OSError, ProcessLookupError):
        return False


def stop_background_watcher() -> None:
    """Stop the background watcher process."""
    pid = _read_pid()
    if pid is None:
        print("No watcher is running.")
        return
    if _terminate_pid(pid):
        print(f"Watcher stopped (PID {pid}).")
    else:
        print(f"Watcher process {pid} not found (may have already exited).")
    _clear_pid()


# ──────────────────────────────────────────────────────────────────────
# Daemon entry point (python -m event_watcher --daemon <path>)
# ──────────────────────────────────────────────────────────────────────

def _daemon_main(watch_path: str) -> None:
    """Run the watcher in the foreground (called as a background process)."""
    # Redirect stderr/stdout to a log file so crashes are visible
    _log_path = Path("data") / "watcher.log"
    _log_path.parent.mkdir(parents=True, exist_ok=True)
    _log_fh = open(_log_path, "a", encoding="utf-8")
    sys.stdout = _log_fh
    sys.stderr = _log_fh

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=_log_fh,
    )
    _write_pid()
    logger.info("Watcher daemon started (PID %d) — watching %s", os.getpid(), watch_path)

    watcher = EventWatcher(watch_path)
    watcher.start()

    def _shutdown(signum, frame):
        logger.info("Received signal %d, shutting down.", signum)
        watcher.stop()
        _clear_pid()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    if sys.platform != "win32":
        signal.signal(signal.SIGHUP, _shutdown)

    try:
        while True:
            if watcher._polling:
                count = watcher.poll_once()
                if count:
                    logger.debug("Poll: %d events queued.", count)
            _log_fh.flush()
            time.sleep(60)
    except KeyboardInterrupt:
        pass
    except BaseException as exc:
        logger.exception("Daemon crashed: %s", exc)
    finally:
        watcher.stop()
        _clear_pid()


if __name__ == "__main__":
    import argparse as _ap
    p = _ap.ArgumentParser()
    p.add_argument("--daemon", action="store_true")
    p.add_argument("path", nargs="?", default=".")
    a = p.parse_args()
    if a.daemon:
        _daemon_main(a.path)
    else:
        print("Use 'natl watch start' to run the watcher.")