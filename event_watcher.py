"""Lightweight workspace event watcher.

Watches a directory for file changes and appends NDJSON events to
``data/event_queue.json``.  This queue is drained by persona tools
(e.g. ``workspace_observer.drain_events()``).

Usage via CLI::

    natl watch start          # start background watcher
    natl watch stop           # stop background watcher
    natl watch status         # show running / stopped

The watcher can also be started and stopped programmatically::

    watcher = EventWatcher(".")
    watcher.start()
    ...
    watcher.stop()
"""

from __future__ import annotations

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

EVENT_QUEUE_PATH = Path("data") / "event_queue.json"
PID_FILE = Path("data") / "watcher.pid"

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

def _append_event(event: dict[str, Any]) -> None:
    """Append a single NDJSON event to the queue file (thread-safe enough)."""
    EVENT_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, separators=(",", ":")) + "\n"
    with open(EVENT_QUEUE_PATH, "a", encoding="utf-8") as f:
        f.write(line)


def _should_ignore(path: str) -> bool:
    """Return True if this path should be silently ignored."""
    p = Path(path)
    # Ignore by directory component
    for part in p.parts:
        if part in _IGNORE_DIRS:
            return True
    # Ignore by suffix
    if p.suffix in _IGNORE_SUFFIXES:
        return True
    return False


def _prune_old_events(max_age_hours: int = 24) -> None:
    """Remove events older than *max_age_hours* from the queue."""
    if not EVENT_QUEUE_PATH.exists():
        return
    cutoff = datetime.now(timezone.utc).timestamp() - (max_age_hours * 3600)
    kept: list[str] = []
    try:
        with open(EVENT_QUEUE_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                    ts = evt.get("ts", "")
                    if ts:
                        evt_time = datetime.fromisoformat(ts).timestamp()
                        if evt_time < cutoff:
                            continue
                except (json.JSONDecodeError, ValueError):
                    continue
                kept.append(line)
        with open(EVENT_QUEUE_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(kept) + "\n" if kept else "")
    except OSError:
        pass


# ──────────────────────────────────────────────────────────────────────
# File watcher — uses watchdog if available, else polling fallback
# ──────────────────────────────────────────────────────────────────────

class EventWatcher:
    """Watch a directory tree and queue file-change events."""

    def __init__(self, watch_path: str = ".") -> None:
        self.watch_path = os.path.abspath(watch_path)
        self._observer: Any = None
        self._polling = False
        self._stop = False

    def start(self) -> None:
        """Start the watcher (tries watchdog, falls back to polling)."""
        try:
            self._start_watchdog()
        except ImportError:
            logger.info("watchdog not installed — using polling fallback (30s interval)")
            self._start_polling()

    def stop(self) -> None:
        """Stop the watcher."""
        self._stop = True
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
        logger.info("Event watcher stopped.")

    # ── watchdog backend ──

    def _start_watchdog(self) -> None:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler, FileSystemEvent

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
                _append_event({
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
        """Compare current state to snapshot, emit events, return count."""
        if not self._polling:
            return 0
        new_snap = self._take_snapshot()
        count = 0
        now = datetime.now(timezone.utc).isoformat()

        # New or modified files
        for path, mtime in new_snap.items():
            old_mtime = self._snapshot.get(path)
            if old_mtime is None:
                _append_event({"type": "created", "path": path, "ts": now})
                count += 1
            elif mtime > old_mtime:
                _append_event({"type": "modified", "path": path, "ts": now})
                count += 1

        # Deleted files
        for path in self._snapshot:
            if path not in new_snap:
                _append_event({"type": "deleted", "path": path, "ts": now})
                count += 1

        self._snapshot = new_snap
        return count


# ──────────────────────────────────────────────────────────────────────
# PID-based background process management
# ──────────────────────────────────────────────────────────────────────

def _write_pid() -> None:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")


def _read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def _clear_pid() -> None:
    try:
        PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def is_watcher_running() -> bool:
    """Check if a watcher process is alive."""
    pid = _read_pid()
    if pid is None:
        return False
    try:
        # On Windows, os.kill(pid, 0) checks if process exists
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        _clear_pid()
        return False


def start_background_watcher(watch_path: str = ".") -> None:
    """Launch the watcher as a background process."""
    if is_watcher_running():
        print("Watcher is already running (PID %s)." % _read_pid())
        return

    # Launch a detached subprocess running this module
    cmd = [sys.executable, "-m", "event_watcher", "--daemon", watch_path]
    if sys.platform == "win32":
        # DETACHED_PROCESS on Windows
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        DETACHED_PROCESS = 0x00000008
        subprocess.Popen(
            cmd,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        subprocess.Popen(
            cmd,
            start_new_session=True,
            close_fds=True,
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


def stop_background_watcher() -> None:
    """Stop the background watcher process."""
    pid = _read_pid()
    if pid is None:
        print("No watcher is running.")
        return
    try:
        if sys.platform == "win32":
            os.kill(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
        print(f"Watcher stopped (PID {pid}).")
    except (OSError, ProcessLookupError):
        print(f"Watcher process {pid} not found (may have already exited).")
    _clear_pid()


# ──────────────────────────────────────────────────────────────────────
# Git hook helpers
# ──────────────────────────────────────────────────────────────────────

def append_git_commit_event(
    commit_hash: str,
    message: str,
    files_changed: list[str] | None = None,
) -> None:
    """Append a git commit event to the queue.

    Called from a git post-commit hook or manually.
    """
    _append_event({
        "type": "git_commit",
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

    # On Windows, also create a .ps1 variant
    hook_script_win = """#!/bin/sh
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
# Daemon entry point (python -m event_watcher --daemon <path>)
# ──────────────────────────────────────────────────────────────────────

def _daemon_main(watch_path: str) -> None:
    """Run the watcher in the foreground (called as a background process)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
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
            # Prune old events every 60 iterations (~1 hour with 60s sleep)
            _prune_old_events(max_age_hours=24)
            if watcher._polling:
                count = watcher.poll_once()
                if count:
                    logger.debug("Poll: %d events queued.", count)
            time.sleep(60)
    except KeyboardInterrupt:
        pass
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
