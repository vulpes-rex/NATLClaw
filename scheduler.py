from __future__ import annotations

import asyncio
import errno
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Callable, TypeVar, cast

from agent_setup import create_agent
from config import AppConfig
from error_classification import classify_error_text
from execution_log import set_db_path as _set_log_db_path
from goals import auto_expire_goals, build_goals_block
from learning import build_context_block
from metrics import MetricsStore
from persona_loader import load_persona
from second_brain import (
    build_brain_summary,
    build_brain_summary_from_store,
    decay_stale_notes,
    decay_stale_notes_from_store,
    load_brain,
    save_brain,
)
from state import AgentState, load_state, save_state
from scheduler_control import (
    load_scheduler_control,
    save_scheduler_control,
)

from messaging import (
    append_message,
    build_inbox_summary,
    emit_alert,
    emit_task_timed_out,
    extend_messages,
    load_outbox,
    prune_old_messages,
    save_outbox,
)
from tasks import (
    auto_timeout_tasks,
    find_task,
    load_tasks,
    save_tasks,
)
from workflow import run_heartbeat, run_task_heartbeat

from project_context import (
    detect_and_save_project,
    load_projects,
    Project,
)

T = TypeVar('T')
R = TypeVar('R')

def retry(max_attempts: int = 3, delay: float = 0.5, backoff: float = 2.0):
    """Decorator for retrying functions with exponential backoff.

    Can be used as ``@retry()`` or called inline as ``retry()(func)(args)``.
    The per-module decorated helpers below (``_load_state``, etc.) are the
    preferred way to invoke I/O in the scheduler — they avoid allocating a
    fresh wrapper object on every heartbeat cycle.

    Works with both sync and async functions.
    """
    def decorator(func: Callable[..., R]) -> Callable[..., R]:
        _is_async = asyncio.iscoroutinefunction(func)

        async def wrapper(*args, **kwargs) -> R:
            attempts = 0
            current_delay = delay
            while attempts < max_attempts:
                try:
                    if _is_async:
                        return await func(*args, **kwargs)
                    else:
                        return func(*args, **kwargs)
                except (OSError, IOError, asyncio.TimeoutError) as e:
                    attempts += 1
                    if attempts == max_attempts:
                        # Raise RuntimeError instead of original exception to match test expectations
                        raise RuntimeError(
                            f"All {max_attempts} attempts failed: {str(e)}"
                        ) from e
                    logger.warning("Attempt %d/%d failed: %s, retrying in %.1f seconds...",
                                 attempts, max_attempts, str(e), current_delay)
                    await asyncio.sleep(current_delay)
                    current_delay *= backoff
                except Exception as e:
                    # Don't retry non-transient exceptions
                    logger.error("Non-retryable error: %s", str(e))
                    raise
            # This should never be reached
            raise RuntimeError(f"All {max_attempts} attempts failed")
        return wrapper
    return decorator

logger = logging.getLogger(__name__)
EVENT_WAKE_POLL_SEC = 0.5
DEFAULT_MAX_EVENTS_PER_HEARTBEAT = 50
DEFAULT_QUEUE_DEPTH_WARN_THRESHOLD = 200

_runtime_backpressure_stats: dict[str, int] = {
    "queue_depth_before_decision": 0,
    "events_consumed_for_decision": 0,
    "decision_spillover_events": 0,
    "wake_batch_events": 0,
    "wake_spillover_events": 0,
}


# ── Scheduler singleton lock ────────────────────────────────────────

_LOCK_FILE: str | None = None


def _lock_path(state_file: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(state_file)), "scheduler.lock")


def get_scheduler_lock_info(state_file: str) -> dict[str, object]:
    """Return diagnostic information for the scheduler lock file."""
    path = _lock_path(state_file)
    info: dict[str, object] = {
        "path": path,
        "exists": False,
        "pid": None,
        "pid_alive": None,
        "stale": None,
        "age_sec": None,
        "malformed": False,
    }
    if not os.path.exists(path):
        return info

    info["exists"] = True
    try:
        stat = os.stat(path)
        info["age_sec"] = max(0.0, time.time() - stat.st_mtime)
    except OSError:
        info["age_sec"] = None

    try:
        with open(path, encoding="utf-8") as f:
            pid = int(f.read().strip())
    except (ValueError, OSError):
        info["malformed"] = True
        info["stale"] = True
        return info

    info["pid"] = pid
    alive = _is_pid_alive(pid)
    info["pid_alive"] = alive
    info["stale"] = not alive
    return info


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID exists."""
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def acquire_scheduler_lock(state_file: str) -> bool:
    """Try to acquire the scheduler lock. Returns True if acquired."""
    global _LOCK_FILE
    path = _lock_path(state_file)

    def _write_lock_file() -> bool:
        """Atomically create lock file with the current process PID."""
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except OSError as e:
            if e.errno == errno.EEXIST:
                return False
            logger.error("Failed to create scheduler lock: %s", e)
            return False
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
        except OSError as e:
            logger.error("Failed to write scheduler lock: %s", e)
            try:
                os.unlink(path)
            except OSError:
                pass
            return False
        return True

    # Fast path: try atomic create first.
    if _write_lock_file():
        _LOCK_FILE = path
        return True

    # Existing lock file path. Check if stale and retry atomically.
    try:
        with open(path, encoding="utf-8") as f:
            old_pid = int(f.read().strip())
    except (ValueError, OSError):
        old_pid = None

    if old_pid is not None and _is_pid_alive(old_pid):
        logger.warning("Scheduler already running (pid %d)", old_pid)
        return False

    try:
        if old_pid is not None:
            logger.warning("Removing stale lock file (pid %d dead)", old_pid)
        else:
            logger.warning("Removing malformed scheduler lock file")
        os.unlink(path)
    except OSError as e:
        logger.warning("Could not remove existing scheduler lock %s: %s", path, e)
        return False

    if _write_lock_file():
        _LOCK_FILE = path
        return True
    return False


def release_scheduler_lock() -> None:
    """Release the scheduler lock."""
    global _LOCK_FILE
    if _LOCK_FILE and os.path.exists(_LOCK_FILE):
        try:
            os.unlink(_LOCK_FILE)
        except OSError:
            pass
    _LOCK_FILE = None


def _drain_event_queue_bounded(
    event_queue: asyncio.PriorityQueue[tuple[int, str, dict]],
    *,
    max_items: int,
) -> tuple[list[tuple[int, str, dict]], int]:
    """Drain at most ``max_items`` events and report remaining queue depth."""
    if max_items <= 0:
        return [], event_queue.qsize()
    drained: list[tuple[int, str, dict]] = []
    for _ in range(max_items):
        if event_queue.empty():
            break
        try:
            drained.append(event_queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return drained, event_queue.qsize()


def get_scheduler_runtime_backpressure_stats() -> dict[str, int]:
    """Return latest scheduler backpressure/queue metrics for operator visibility."""
    return dict(_runtime_backpressure_stats)


async def _wait_for_event_or_timeout(
    event_queue: asyncio.PriorityQueue[tuple[int, str, dict]],
    *,
    timeout_sec: float,
    poll_interval_sec: float,
    drain_pending_events_fn: Callable[[asyncio.PriorityQueue[tuple[int, str, dict]]], int],
) -> tuple[int, str, dict] | None:
    """Wait for an event while periodically draining cross-process pending events.

    Returns:
        Event tuple if one arrives before timeout, else ``None``.
    """
    deadline = time.monotonic() + max(0.0, timeout_sec)
    while True:
        drained = drain_pending_events_fn(event_queue)
        if drained:
            logger.info("Drained %d pending events during sleep", drained)

        try:
            return event_queue.get_nowait()
        except asyncio.QueueEmpty:
            pass

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None

        wait_time = min(max(0.01, poll_interval_sec), remaining)
        try:
            return await asyncio.wait_for(event_queue.get(), timeout=wait_time)
        except asyncio.TimeoutError:
            continue


async def run_scheduler(config: AppConfig, *, max_iterations: int = 0, event_queue: asyncio.PriorityQueue[tuple[int, str, dict]] | None = None) -> None:
    """Run the heartbeat loop until interrupted.

    Args:
        config: Application configuration.
        max_iterations: If > 0, stop after this many heartbeats (for testing).
        event_queue: Optional event queue to use. If None, a new one is created.
    """
    if not acquire_scheduler_lock(config.state_file):
        raise RuntimeError("Another scheduler is already running")

    # Create retry-wrapped I/O helpers once per scheduler invocation.
    # This resolves the current module-level references (which tests may
    # have patched) instead of capturing the originals at import time.
    # Fixes §5.1 — avoids both duplicated logic and per-iteration overhead.
    _load_state = retry()(load_state)
    _load_brain = retry()(load_brain)
    _save_state = retry()(save_state)
    _save_brain = retry()(save_brain)
    _load_tasks = retry()(load_tasks)
    _save_tasks = retry()(save_tasks)
    _load_outbox = retry()(load_outbox)
    _save_outbox = retry()(save_outbox)
    _load_projects = retry()(load_projects)
    _load_scheduler_control = retry()(load_scheduler_control)
    _save_scheduler_control = retry()(save_scheduler_control)
    max_events_per_heartbeat = max(
        1,
        int(
            getattr(
                config,
                "max_events_per_heartbeat",
                DEFAULT_MAX_EVENTS_PER_HEARTBEAT,
            )
        ),
    )
    queue_depth_warn_threshold = max(
        1,
        int(
            getattr(
                config,
                "queue_depth_warn_threshold",
                DEFAULT_QUEUE_DEPTH_WARN_THRESHOLD,
            )
        ),
    )

    # Event queue for event-driven scheduling
    # Priority queue: (priority, event_type, payload)
    # Higher priority (lower number) events are processed first.
    if event_queue is None:
        event_queue = asyncio.PriorityQueue[tuple[int, str, dict]]()

    # Point execution log DB next to the state file
    import os as _os
    _set_log_db_path(_os.path.join(_os.path.dirname(config.state_file), "execution_log.db"))

# Create event watcher with the queue
    from event_watcher import EventWatcher, drain_pending_events
    event_watcher = EventWatcher(watch_path=config.watch_path, event_queue=event_queue)
    event_watcher.start()

    persona = load_persona(config.persona)
    logger.info(
        "Starting NATLClaw scheduler (provider=%s, model=%s, persona=%s, interval=%ds)",
        config.provider,
        config.model,
        persona.name,
        config.heartbeat_interval_sec,
    )
    if persona.tools:
        logger.info("Persona tools: %s", [t.__name__ for t in persona.tools])
    if persona.mcp_servers:
        logger.info("Persona MCP servers: %s", list(persona.mcp_servers.keys()))

# Metrics store — sits next to state file
    metrics_dir = os.path.dirname(os.path.abspath(config.state_file))
    metrics_store = MetricsStore(os.path.join(metrics_dir, "metrics.db"))

    # Project context
    projects = await _load_projects(config.state_file)
    current_project = detect_and_save_project(config.state_file, config) if not projects else None

    # Hot-reload tracking
    _mcp_json = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp.json")
    try:
        last_mcp_mtime = os.path.getmtime(_mcp_json) if os.path.exists(_mcp_json) else 0.0
    except OSError:
        last_mcp_mtime = 0.0

    iteration_count = 0
    drain_shutdown_requested = False
    try:
        while True:
            iteration_count += 1
            logger.debug("Scheduler iteration %d", iteration_count)
            if max_iterations > 0 and iteration_count > max_iterations:
                logger.info("Reached max_iterations=%d, stopping scheduler", max_iterations)
                break

            # ── Drain cross-process events (CLI → scheduler) ─────
            drained = drain_pending_events(event_queue)
            if drained:
                logger.info("Drained %d pending events from CLI/hooks", drained)

            # ── Hot reload: re-read persona if mcp.json changed ──
            try:
                cur_mtime = os.path.getmtime(_mcp_json) if os.path.exists(_mcp_json) else 0.0
                if cur_mtime != last_mcp_mtime:
                    logger.info("mcp.json changed, reloading persona '%s'", config.persona)
                    persona = load_persona(config.persona)
                    last_mcp_mtime = cur_mtime
            except OSError:
                pass  # file might be temporarily unavailable

            control = await _load_scheduler_control(config.state_file)
            if control.drain_requested:
                logger.info("Drain requested by operator — stopping scheduler loop gracefully")
                control.drain_in_progress = True
                await _save_scheduler_control(control, config.state_file)
                drain_shutdown_requested = True
                break

            state = await _load_state(config.state_file)

            # Phase 3: decay stale notes directly in the store (no full brain load)
            archived = decay_stale_notes_from_store(config.state_file)
            if archived:
                logger.info("Archived %d stale notes (store-backed)", archived)

            expired_goals = auto_expire_goals(state)
            if expired_goals:
                logger.info("Auto-expired %d overdue goals: %s", len(expired_goals), expired_goals)

            # ── Outbox + task queue management ───────────────────
            outbox = await _load_outbox(config.state_file)
            prune_old_messages(outbox)

            tasks = await _load_tasks(config.state_file)
            timed_out = auto_timeout_tasks(tasks)
            for tid in timed_out:
                t = find_task(tasks, tid)
                if t:
                    append_message(
                        outbox,
                        emit_task_timed_out(
                            t, persona=persona.name, heartbeat=state.execution_count,
                        ),
                    )
            if timed_out:
                logger.info("Auto-timed-out %d task(s): %s", len(timed_out), timed_out)

            # ── Daily digest: first heartbeat of a new day ───────
            from daily_digest import is_first_run_today, build_digest, save_digest
            # Load brain for digest + heartbeat workflow (which mutates it)
            brain = await _load_brain(config.state_file)
            if is_first_run_today(state.last_heartbeat):
                logger.info("First heartbeat of the day — generating daily digest")
                digest = build_digest(brain, state.last_heartbeat, persona_name=persona.name)
                logger.info("\n%s", digest)
                try:
                    save_digest(digest)
                except OSError as e:
                    logger.warning("Failed to save daily digest: %s", e)

            state.execution_count += 1
            state.last_heartbeat = datetime.now(timezone.utc).isoformat()
            cycle_start = time.monotonic()
            elapsed = 0.0

            logger.info("=== Heartbeat #%d starting ===", state.execution_count)

            # Phase 3: build brain summary from store (avoids in-memory rebuild)
            base_instructions = config.agent_instructions or persona.instructions
            context_block = build_context_block(state)
            brain_block = build_brain_summary_from_store(config.state_file, max_notes=5)
            goals_block = build_goals_block(state)

            # Project context block
            project_block = ""
            if current_project:
                project_block = (
                    f"\nProject Context:\n"
                    f"- Name: {current_project.name}\n"
                    f"- Language: {current_project.language}\n"
                    f"- Framework: {current_project.framework}\n"
                    f"- Branch: {current_project.branch or 'unknown'}\n"
                    f"- Active work: {current_project.active_work or 'None'}\n"
                )
            elif projects:
                p = projects[0]
                project_block = (
                    f"\nProject Context (default):\n"
                    f"- Name: {p.name}\n"
                    f"- Language: {p.language}\n"
                    f"- Framework: {p.framework}\n"
                    f"- Branch: {p.branch or 'unknown'}\n"
                    f"- Active work: {p.active_work or 'None'}\n"
                )

            # Governance schemas: HEARTBEAT.md (HOW) + BRAIN.md (WHAT)
            schema_blocks = ""
            if persona.heartbeat_schema:
                schema_blocks += f"\n\n== HEARTBEAT STRATEGY ==\n{persona.heartbeat_schema}"
            if persona.brain_schema:
                schema_blocks += f"\n\n== KNOWLEDGE SCHEMA ==\n{persona.brain_schema}"

            enriched_instructions = (
                f"{base_instructions}{schema_blocks}\n\n{context_block}\n\n{brain_block}"
                + (f"\n\n{goals_block}" if goals_block else "")
                + (f"\n\n{project_block}" if project_block else "")
            )

            # ── Decision engine ─────────────────────────────────────
            from decision_engine import (
                build_decision_context,
                evaluate_heartbeat as evaluate_decision,
                apply_decision,
                record_decision,
                record_decision_outcome,
                update_consecutive_empty,
            )

            # Backpressure: cap queue work per heartbeat and spill excess.
            queue_depth_before_decision = event_queue.qsize()

            pending_events, decision_spillover = _drain_event_queue_bounded(
                event_queue,
                max_items=max_events_per_heartbeat,
            )
            _runtime_backpressure_stats["queue_depth_before_decision"] = queue_depth_before_decision
            _runtime_backpressure_stats["events_consumed_for_decision"] = len(pending_events)
            _runtime_backpressure_stats["decision_spillover_events"] = decision_spillover

            if queue_depth_before_decision >= queue_depth_warn_threshold:
                logger.warning(
                    "Event queue depth elevated before decision phase: depth=%d (threshold=%d)",
                    queue_depth_before_decision,
                    queue_depth_warn_threshold,
                )
            if decision_spillover > 0:
                logger.info(
                    "Backpressure: capped decision events to %d; spilling %d event(s) to next heartbeat",
                    len(pending_events),
                    decision_spillover,
                )

            decision_ctx = build_decision_context(
                state, brain, tasks, outbox, pending_events, persona,
            )
            decision = evaluate_decision(decision_ctx, persona.decision_policy)
            directives = apply_decision(
                decision, state, brain, tasks, outbox, persona, config,
            )
            decision_note_id = record_decision(brain, decision, decision_ctx)

            logger.info(
                "Decision: %s (score=%.1f, reason=%s)",
                decision.chosen.action.value,
                decision.chosen.score,
                decision.chosen.rationale,
            )

            active_task = directives.get("active_task")
            skip_agent = directives.get("skip_agent", False)
            if control.paused or control.maintenance_mode:
                skip_agent = True
                control_mode = "maintenance_mode" if control.maintenance_mode else "paused"
                logger.info(
                    "Scheduler control active (%s) — skipping agent execution this heartbeat",
                    control_mode,
                )
                # During maintenance we do not actively execute task work.
                active_task = None

            if directives.get("extra_context"):
                enriched_instructions += directives["extra_context"]
            if directives.get("outbox_messages"):
                extend_messages(outbox, directives["outbox_messages"])

            if skip_agent:
                update_consecutive_empty(state)
                logger.info("Decision engine: SKIP_CYCLE — skipping agent this heartbeat")

            if not skip_agent:
                agent = create_agent(
                    config,
                    enriched_instructions,
                    tools=persona.tools,
                    mcp_servers=persona.mcp_servers,
                )

            notes_before = len(brain.notes)
            conns_before = len(brain.connections)
            had_error = False

            try:
                if skip_agent:
                    pass  # nothing to run
                elif active_task:
                    logger.info(
                        "Working on task %s (%d/%d heartbeats): %s",
                        active_task.id, active_task.heartbeats_spent + 1,
                        active_task.max_heartbeats, active_task.title,
                    )
                    if config.provider == "copilot":
                        async with agent:
                            task_msgs = await run_task_heartbeat(
                                agent, state, brain, config, persona, active_task,
                            )
                    else:
                        task_msgs = await run_task_heartbeat(
                            agent, state, brain, config, persona, active_task,
                        )
                    if task_msgs:
                        extend_messages(outbox, task_msgs)
                elif config.provider == "copilot":
                    async with agent:
                        await run_heartbeat(agent, state, brain, config, persona)
                else:
                    await run_heartbeat(agent, state, brain, config, persona)

                elapsed = time.monotonic() - cycle_start
                logger.info(
                    "=== Heartbeat #%d completed in %.1fs ===",
                    state.execution_count,
                    elapsed,
                    extra={
                        "heartbeat": state.execution_count,
                        "elapsed_sec": elapsed,
                        "persona": persona.name,
                        "workflow": persona.workflow,
                    },
                )
            except (KeyboardInterrupt, SystemExit):
                logger.info("Scheduler interrupted, exiting gracefully")
                raise
            except asyncio.TimeoutError:
                elapsed = time.monotonic() - cycle_start
                had_error = True
                logger.error("Heartbeat timed out after %.1f seconds [error_type=timeout]", elapsed)
            except Exception as e:
                elapsed = time.monotonic() - cycle_start
                had_error = True
                error_type = classify_error_text(str(e))
                logger.error(
                    "Error during heartbeat #%d: %s [error_type=%s]",
                    state.execution_count,
                    str(e),
                    error_type,
                )
                logger.debug("Detailed error:", exc_info=True)
            finally:
                # Record decision outcome for brain learning
                try:
                    new_notes_count = len(brain.notes) - notes_before
                    if had_error:
                        outcome = "error"
                    elif elapsed > 120:
                        outcome = "slow"
                    elif skip_agent:
                        outcome = "skipped"
                    else:
                        outcome = "success"
                    record_decision_outcome(
                        brain, decision_note_id, outcome,
                        {"elapsed": round(elapsed, 1), "notes_created": new_notes_count},
                    )
                except Exception as rec_err:
                    logger.debug("Failed to record decision outcome: %s", rec_err)

                try:
                    await _save_state(state, config.state_file, config.max_history)
                except Exception as e:
                    logger.error("Failed to save state after retries: %s", str(e))
                try:
                    await _save_brain(brain, config.state_file)
                except Exception as e:
                    logger.error("Failed to save brain after retries: %s", str(e))
                try:
                    await _save_tasks(tasks, config.state_file)
                except Exception as e:
                    logger.error("Failed to save tasks after retries: %s", str(e))
                try:
                    await _save_outbox(outbox, config.state_file)
                except Exception as e:
                    logger.error("Failed to save outbox after retries: %s", str(e))

            # Log inbox summary if there are unread messages
            inbox_line = build_inbox_summary(outbox)
            if inbox_line:
                logger.info(inbox_line)

            # Adaptive interval: score productivity, scale sleep accordingly
            new_notes = len(brain.notes) - notes_before
            new_conns = len(brain.connections) - conns_before
            score = new_notes + 2 * new_conns
            if score <= 0:
                interval = min(config.heartbeat_interval_sec * 1.5, 600)
            else:
                interval = max(config.heartbeat_interval_sec * 0.7, 60)

            logger.info(
                "Sleeping %.0fs until next heartbeat (score=%d, +%d notes, +%d conns)...",
                interval, score, new_notes, new_conns,
                extra={
                    "heartbeat": state.execution_count,
                    "score": score,
                    "notes_created": new_notes,
                    "connections_created": new_conns,
                    "interval": interval,
                    "persona": persona.name,
                    "workflow": persona.workflow,
                },
            )

            # Record metrics to SQLite
            try:
                metrics_store.record_heartbeat(
                    heartbeat_number=int(state.execution_count),
                    persona=str(persona.name),
                    workflow=str(getattr(persona, 'workflow', 'second_brain')),
                    elapsed_sec=float(elapsed),
                    notes_created=int(new_notes),
                    connections_created=int(new_conns),
                    score=int(score),
                    interval_sec=float(interval),
                )
            except Exception as metrics_err:
                logger.warning("Failed to record metrics: %s", metrics_err)

            # ── Event-driven sleep: wait for event OR timeout ────
            try:
                event = await _wait_for_event_or_timeout(
                    event_queue,
                    timeout_sec=float(interval),
                    poll_interval_sec=EVENT_WAKE_POLL_SEC,
                    drain_pending_events_fn=drain_pending_events,
                )
                if event is None:
                    continue
                priority, event_type, payload = event
                logger.info(
                    "Woke on event: %s (priority=%d, payload_keys=%s)",
                    event_type, priority, list(payload.keys()),
                )
                # Drain any additional queued events, bounded per heartbeat.
                batch = [event]
                tail, wake_spillover = _drain_event_queue_bounded(
                    event_queue,
                    max_items=max(0, max_events_per_heartbeat - 1),
                )
                batch.extend(tail)
                _runtime_backpressure_stats["wake_batch_events"] = len(batch)
                _runtime_backpressure_stats["wake_spillover_events"] = wake_spillover
                if len(batch) > 1:
                    logger.info("Drained %d queued events total", len(batch))
                if wake_spillover > 0:
                    logger.info(
                        "Backpressure: wake batch capped at %d event(s); %d event(s) remain queued",
                        len(batch),
                        wake_spillover,
                    )
                # Task events → next heartbeat runs immediately
                has_task_event = any(
                    ev[1] in ("task_created", "task_answered", "task_retried")
                    for ev in batch
                )
                if has_task_event:
                    logger.info("Task event detected — next heartbeat immediate")
            except asyncio.TimeoutError:
                pass  # normal heartbeat interval elapsed
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Error waiting for events: %s", str(e), exc_info=True)

    finally:
        if drain_shutdown_requested:
            try:
                control = await _load_scheduler_control(config.state_file)
                control.drain_requested = False
                control.drain_in_progress = False
                await _save_scheduler_control(control, config.state_file)
            except Exception as drain_err:
                logger.warning("Failed to finalize drain state: %s", drain_err)
        release_scheduler_lock()
        metrics_store.close()
        event_watcher.stop()
