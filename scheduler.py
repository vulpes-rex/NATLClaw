from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Callable, TypeVar, cast

from agent_setup import create_agent
from config import AppConfig
from execution_log import set_db_path as _set_log_db_path
from goals import auto_expire_goals, build_goals_block
from learning import build_context_block
from metrics import MetricsStore
from persona_loader import load_persona
from second_brain import build_brain_summary, decay_stale_notes, load_brain, save_brain
from state import AgentState, load_state, save_state
from workflow import run_heartbeat

T = TypeVar('T')
R = TypeVar('R')

def retry(max_attempts: int = 3, delay: float = 0.5, backoff: float = 2.0):
    """Decorator for retrying functions with exponential backoff.

    Can be used as ``@retry()`` or called inline as ``retry()(func)(args)``.
    The per-module decorated helpers below (``_load_state``, etc.) are the
    preferred way to invoke I/O in the scheduler — they avoid allocating a
    fresh wrapper object on every heartbeat cycle.
    """
    def decorator(func: Callable[..., R]) -> Callable[..., R]:
        async def wrapper(*args, **kwargs) -> R:
            attempts = 0
            current_delay = delay
            while attempts < max_attempts:
                try:
                    return await func(*args, **kwargs)
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


async def run_scheduler(config: AppConfig, *, max_iterations: int = 0) -> None:
    """Run the heartbeat loop until interrupted.

    Args:
        config: Application configuration.
        max_iterations: If > 0, stop after this many heartbeats (for testing).
    """
    # Create retry-wrapped I/O helpers once per scheduler invocation.
    # This resolves the current module-level references (which tests may
    # have patched) instead of capturing the originals at import time.
    # Fixes §5.1 — avoids both duplicated logic and per-iteration overhead.
    _load_state = retry()(load_state)
    _load_brain = retry()(load_brain)
    _save_state = retry()(save_state)
    _save_brain = retry()(save_brain)

    # Point execution log DB next to the state file
    import os as _os
    _set_log_db_path(_os.path.join(_os.path.dirname(config.state_file), "execution_log.db"))

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
    import os
    metrics_dir = os.path.dirname(os.path.abspath(config.state_file))
    metrics_store = MetricsStore(os.path.join(metrics_dir, "metrics.db"))

    # Hot-reload tracking
    _mcp_json = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp.json")
    try:
        last_mcp_mtime = os.path.getmtime(_mcp_json) if os.path.exists(_mcp_json) else 0.0
    except OSError:
        last_mcp_mtime = 0.0

    iteration_count = 0
    try:
      while True:
        iteration_count += 1
        if max_iterations > 0 and iteration_count > max_iterations:
            logger.info("Reached max_iterations=%d, stopping scheduler", max_iterations)
            break

        # ── Hot reload: re-read persona if mcp.json changed ──────────
        try:
            cur_mtime = os.path.getmtime(_mcp_json) if os.path.exists(_mcp_json) else 0.0
            if cur_mtime != last_mcp_mtime:
                logger.info("mcp.json changed, reloading persona '%s'", config.persona)
                persona = load_persona(config.persona)
                last_mcp_mtime = cur_mtime
        except OSError:
            pass  # file might be temporarily unavailable

        state = await _load_state(config.state_file)
        brain = await _load_brain(config.state_file)
        archived = decay_stale_notes(brain)
        if archived:
            logger.info("Archived %d stale notes", archived)
        expired_goals = auto_expire_goals(state)
        if expired_goals:
            logger.info("Auto-expired %d overdue goals: %s", len(expired_goals), expired_goals)

        # ── Daily digest: first heartbeat of a new day ────────────
        from daily_digest import is_first_run_today, build_digest, save_digest
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

        # Build enriched instructions with memory + brain context
        # Use persona instructions, or fall back to config override
        base_instructions = config.agent_instructions or persona.instructions
        context_block = build_context_block(state)
        brain_block = build_brain_summary(brain, max_notes=5)
        goals_block = build_goals_block(state)

        # Governance schemas: HEARTBEAT.md (HOW) + BRAIN.md (WHAT)
        schema_blocks = ""
        if persona.heartbeat_schema:
            schema_blocks += f"\n\n== HEARTBEAT STRATEGY ==\n{persona.heartbeat_schema}"
        if persona.brain_schema:
            schema_blocks += f"\n\n== KNOWLEDGE SCHEMA ==\n{persona.brain_schema}"

        enriched_instructions = (
            f"{base_instructions}{schema_blocks}\n\n{context_block}\n\n{brain_block}"
            + (f"\n\n{goals_block}" if goals_block else "")
        )

        agent = create_agent(
            config,
            enriched_instructions,
            tools=persona.tools,
            mcp_servers=persona.mcp_servers,
        )

        notes_before = len(brain.notes)
        conns_before = len(brain.connections)

        try:
            # GitHubCopilotAgent requires async context manager for start/stop
            if config.provider == "copilot":
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
            raise  # Re-raise to allow proper shutdown
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - cycle_start
            logger.error("Heartbeat timed out after %.1f seconds", elapsed)
        except Exception as e:
            elapsed = time.monotonic() - cycle_start
            # Log the error with context
            logger.error("Error during heartbeat #%d: %s", state.execution_count, str(e))
            logger.debug("Detailed error:", exc_info=True)
        finally:
            try:
                await _save_state(state, config.state_file, config.max_history)
            except Exception as e:
                logger.error("Failed to save state after retries: %s", str(e))
            try:
                await _save_brain(brain, config.state_file)
            except Exception as e:
                logger.error("Failed to save brain after retries: %s", str(e))

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

        await asyncio.sleep(interval)
    finally:
        metrics_store.close()
