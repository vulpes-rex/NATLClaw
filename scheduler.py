from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from agent_setup import create_agent
from config import AppConfig
from learning import build_context_block
from persona_loader import load_persona
from second_brain import build_brain_summary, load_brain, save_brain
from state import AgentState, load_state, save_state
from workflow import run_heartbeat

logger = logging.getLogger(__name__)


async def run_scheduler(config: AppConfig) -> None:
    """Run the heartbeat loop until interrupted."""
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

    while True:
        state = load_state(config.state_file)
        brain = load_brain(config.state_file)
        state.execution_count += 1
        state.last_heartbeat = datetime.now(timezone.utc).isoformat()
        cycle_start = time.monotonic()

        logger.info("=== Heartbeat #%d starting ===", state.execution_count)

        # Build enriched instructions with memory + brain context
        # Use persona instructions, or fall back to config override
        base_instructions = config.agent_instructions or persona.instructions
        context_block = build_context_block(state)
        brain_block = build_brain_summary(brain, max_notes=5)
        enriched_instructions = (
            f"{base_instructions}\n\n{context_block}\n\n{brain_block}"
        )

        agent = create_agent(
            config,
            enriched_instructions,
            tools=persona.tools,
            mcp_servers=persona.mcp_servers,
        )

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
            )
        except Exception:
            logger.exception("Error during heartbeat #%d", state.execution_count)
        finally:
            save_state(state, config.state_file, config.max_history)
            save_brain(brain, config.state_file)

        logger.info(
            "Sleeping %ds until next heartbeat...", config.heartbeat_interval_sec
        )
        await asyncio.sleep(config.heartbeat_interval_sec)
