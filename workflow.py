from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from config import AppConfig
from learning import extract_lessons
from second_brain import (
    BrainState,
    add_note,
    build_brain_summary,
    connect_notes,
    get_recent_notes,
)
from persona_loader import Persona
from state import AgentState

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Core step runner
# ──────────────────────────────────────────────────────────────────────

async def _run_step(
    agent,
    step_name: str,
    prompt: str,
    state: AgentState,
) -> str:
    """Run a single workflow step: call agent, record history, extract lessons."""
    start = time.monotonic()
    response = await agent.run(prompt)
    text = response.text if hasattr(response, "text") else str(response)
    elapsed = time.monotonic() - start

    logger.info("[%s] completed in %.1fs", step_name, elapsed)
    logger.info("[%s] response: %s", step_name, text[:200])

    now = datetime.now(timezone.utc).isoformat()
    state.execution_history.append({
        "timestamp": now,
        "step": step_name,
        "prompt": prompt[:300],
        "response": text[:500],
    })

    lessons = extract_lessons(step_name, prompt, text)
    state.lessons_learned.extend(lessons)
    for lesson in lessons:
        logger.info("[%s] lesson: %s", step_name, lesson["description"][:120])

    return text


# ──────────────────────────────────────────────────────────────────────
# Dispatcher
# ──────────────────────────────────────────────────────────────────────

async def run_heartbeat(
    agent, state: AgentState, brain: BrainState, config: AppConfig, persona: Persona
) -> None:
    """Dispatch to the correct workflow based on persona.workflow.

    Modes
    -----
    second_brain (default)
        Four fixed steps optimised for knowledge capture: status check →
        structured JSON capture → connection discovery → review.

    freeform
        Three loose steps for action-oriented personas: status check →
        run heartbeat task freely (tools allowed, no JSON constraint) →
        save a plain-text summary to the brain → review.

    steps
        Fully persona-defined steps declared in mcp.json under ``steps``.
        Each step may optionally store its output as a brain note.
    """
    mode = persona.workflow
    if mode == "freeform":
        await _run_freeform_heartbeat(agent, state, brain, config, persona)
    elif mode == "steps":
        await _run_steps_heartbeat(agent, state, brain, config, persona)
    else:
        await _run_second_brain_heartbeat(agent, state, brain, config, persona)


# ──────────────────────────────────────────────────────────────────────
# Mode 1: second_brain
# ──────────────────────────────────────────────────────────────────────

async def _run_second_brain_heartbeat(
    agent, state: AgentState, brain: BrainState, config: AppConfig, persona: Persona
) -> None:
    """Original 4-step knowledge-capture workflow."""
    brain_summary = build_brain_summary(brain, max_notes=8)

    # Step 1: Status Check
    status_prompt = (
        f"You are {config.agent_name}, an autonomous second-brain agent.\n"
        f"Heartbeat #{state.execution_count}.\n"
        f"Last heartbeat: {state.last_heartbeat or 'never'}.\n"
        f"Past executions: {state.execution_count - 1}.\n"
        f"\n{brain_summary}\n\n"
        f"Give a brief (2-3 sentence) status assessment of the system and knowledge base."
    )
    status_result = await _run_step(agent, "status_check", status_prompt, state)

    # Step 2: Capture — structured JSON note
    capture_prompt = (
        f"Status: {status_result[:200]}\n\n"
        f"Your second brain currently has {len(brain.notes)} notes.\n"
        f"{brain_summary}\n\n"
        f"{persona.heartbeat_task}\n"
        f"Return your answer as JSON with these exact keys:\n"
        f'{{"topic": "...", "content": "2-3 sentence insight", '
        f'"tags": ["tag1", "tag2"], "category": "resources"}}\n'
        f"Return ONLY the JSON object, no extra text."
    )
    capture_result = await _run_step(agent, "capture", capture_prompt, state)
    note_id = _store_capture(brain, capture_result)
    if note_id:
        logger.info("[capture] stored as note %s", note_id)

    # Step 3: Connect — find relationships
    recent = get_recent_notes(brain, 6)
    if len(recent) >= 2:
        notes_text = "\n".join(
            f"  {n['id']}: {n.get('summary') or n.get('content','')[:100]}"
            for n in recent
        )
        connect_prompt = (
            f"Here are recent notes in the second brain:\n{notes_text}\n\n"
            f"Identify ONE meaningful connection between any two of these notes. "
            f"Return as JSON: {{\"from\": \"<id>\", \"to\": \"<id>\", \"reason\": \"...\"}}\n"
            f"Return ONLY the JSON object, no extra text."
        )
        connect_result = await _run_step(agent, "connect", connect_prompt, state)
        _store_connection(brain, connect_result)

    # Step 4: Review
    review_prompt = (
        f"Summarize this heartbeat cycle for the second brain:\n"
        f"- Status: {status_result[:150]}\n"
        f"- New capture: {capture_result[:150]}\n"
        f"- Brain size: {len(brain.notes)} notes, {len(brain.connections)} connections\n\n"
        f"Write a 2-3 sentence synthesis. Note any knowledge gaps or next areas to explore."
    )
    review_result = await _run_step(agent, "review", review_prompt, state)
    brain.last_review = datetime.now(timezone.utc).isoformat()
    brain.review_log.append({
        "timestamp": brain.last_review,
        "summary": review_result[:500],
    })


# ──────────────────────────────────────────────────────────────────────
# Mode 2: freeform
# ──────────────────────────────────────────────────────────────────────

async def _run_freeform_heartbeat(
    agent, state: AgentState, brain: BrainState, config: AppConfig, persona: Persona
) -> None:
    """Three-step free-form workflow for action-oriented personas.

    The agent runs the heartbeat task without any JSON format constraint.
    Any tools attached to the persona are available throughout. The result
    is stored to the second brain as a plain-text note so knowledge
    accumulates across cycles.
    """
    brain_summary = build_brain_summary(brain, max_notes=5)

    # Step 1: Status Check — scoped to the persona's domain
    status_prompt = (
        f"You are {config.agent_name} acting as a {persona.description}.\n"
        f"Heartbeat #{state.execution_count}.\n"
        f"Last heartbeat: {state.last_heartbeat or 'never'}.\n"
        f"{brain_summary}\n\n"
        f"Give a brief (2-3 sentence) status assessment relevant to your role."
    )
    status_result = await _run_step(agent, "status_check", status_prompt, state)

    # Step 2: Main task — fully freeform, tools available
    task_prompt = (
        f"Status: {status_result[:200]}\n\n"
        f"{persona.heartbeat_task}"
    )
    task_result = await _run_step(agent, "task", task_prompt, state)

    # Step 3: Capture — save a plain-text insight to the brain
    capture_prompt = (
        f"You just completed this task:\n{task_result[:400]}\n\n"
        f"Distil ONE key insight or finding worth remembering in the second brain.\n"
        f"Return as JSON with these exact keys:\n"
        f'{{"topic": "short title", "content": "1-2 sentence insight", '
        f'"tags": ["tag1", "tag2"], "category": "resources"}}\n'
        f"Return ONLY the JSON object, no extra text."
    )
    capture_result = await _run_step(agent, "capture", capture_prompt, state)
    note_id = _store_capture(brain, capture_result)
    if note_id:
        logger.info("[capture] stored as note %s", note_id)

    # Step 4: Review
    review_prompt = (
        f"Summarize heartbeat #{state.execution_count} as {persona.description}:\n"
        f"- Task outcome: {task_result[:200]}\n"
        f"- Brain size: {len(brain.notes)} notes\n\n"
        f"Write a 2-3 sentence synthesis and note what to do next cycle."
    )
    review_result = await _run_step(agent, "review", review_prompt, state)
    brain.last_review = datetime.now(timezone.utc).isoformat()
    brain.review_log.append({
        "timestamp": brain.last_review,
        "summary": review_result[:500],
    })


# ──────────────────────────────────────────────────────────────────────
# Mode 3: steps
# ──────────────────────────────────────────────────────────────────────

async def _run_steps_heartbeat(
    agent, state: AgentState, brain: BrainState, config: AppConfig, persona: Persona
) -> None:
    """Execute persona-defined steps from mcp.json.

    Each step in persona.steps is a dict:
        {
            "name":         "step label",
            "prompt":       "prompt text — use {prev} and {brain} as placeholders",
            "storeToBrain": true | false (optional, default false)
        }

    Modes
    -----
    stepwise=False (default)
        All steps run in a single heartbeat.

    stepwise=True
        One step per heartbeat. Progress is tracked in ``state.context``
        so each heartbeat picks up where the last left off.
        Keys used: ``steps_<name>_idx`` (int), ``steps_<name>_prev`` (str).
    """
    if not persona.steps:
        logger.warning(
            "Persona '%s' has workflow=steps but no steps defined; falling back to freeform",
            persona.name,
        )
        await _run_freeform_heartbeat(agent, state, brain, config, persona)
        return

    brain_summary = build_brain_summary(brain, max_notes=5)
    default_context = (
        f"You are {config.agent_name} acting as a {persona.description}. "
        f"Heartbeat #{state.execution_count}. "
        f"Brain has {len(brain.notes)} notes.\n{brain_summary}"
    )

    if persona.stepwise:
        await _run_one_step(agent, state, brain, config, persona, brain_summary, default_context)
    else:
        await _run_all_steps(agent, state, brain, persona, brain_summary, default_context)

    brain.last_review = datetime.now(timezone.utc).isoformat()


async def _run_all_steps(
    agent, state: AgentState, brain: BrainState, persona: Persona,
    brain_summary: str, initial_prev: str,
) -> None:
    """Run all persona steps in a single heartbeat cycle."""
    prev = initial_prev
    for step_def in persona.steps:
        name = step_def.get("name", "step")
        prompt = step_def.get("prompt", "").replace("{prev}", prev).replace("{brain}", brain_summary)
        store = step_def.get("storeToBrain", False)

        result = await _run_step(agent, name, prompt, state)
        prev = result

        if store:
            await _distil_to_brain(agent, state, brain, name, result)


async def _run_one_step(
    agent, state: AgentState, brain: BrainState, config: AppConfig, persona: Persona,
    brain_summary: str, default_context: str,
) -> None:
    """Run exactly one step this heartbeat, advancing the stepwise pointer."""
    idx_key = f"steps_{persona.name}_idx"
    prev_key = f"steps_{persona.name}_prev"

    idx = state.context.get(idx_key, 0)
    total = len(persona.steps)

    if idx >= total:
        logger.info(
            "[steps] Persona '%s': all %d steps complete — resetting for next run",
            persona.name, total,
        )
        # Reset so the next run starts fresh
        state.context[idx_key] = 0
        state.context.pop(prev_key, None)
        return

    step_def = persona.steps[idx]
    name = step_def.get("name", f"step_{idx}")
    store = step_def.get("storeToBrain", False)
    prev = state.context.get(prev_key, default_context)

    logger.info("[steps] Persona '%s': step %d/%d — %s", persona.name, idx + 1, total, name)

    prompt = step_def.get("prompt", "").replace("{prev}", prev).replace("{brain}", brain_summary)
    result = await _run_step(agent, name, prompt, state)

    # Persist progress for the next heartbeat
    state.context[idx_key] = idx + 1
    state.context[prev_key] = result[:2000]  # cap stored context size

    if store:
        await _distil_to_brain(agent, state, brain, name, result)


async def _distil_to_brain(
    agent, state: AgentState, brain: BrainState, step_name: str, result: str
) -> None:
    """Ask the agent to extract an insight and store it as a brain note."""
    distil_prompt = (
        f"Distil ONE key insight from this output into the second brain.\n\n"
        f"Output:\n{result[:400]}\n\n"
        f"Return JSON: {{\"topic\": \"...\", \"content\": \"...\", "
        f"\"tags\": [...], \"category\": \"resources\"}}\n"
        f"Return ONLY the JSON object."
    )
    distil_result = await _run_step(agent, f"{step_name}_capture", distil_prompt, state)
    note_id = _store_capture(brain, distil_result)
    if note_id:
        logger.info("[%s] stored as note %s", step_name, note_id)


# ──────────────────────────────────────────────────────────────────────
# Brain helpers (shared)
# ──────────────────────────────────────────────────────────────────────

def _store_capture(brain: BrainState, raw: str) -> str | None:
    """Parse agent JSON output and store as a note. Returns note ID or None."""
    try:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0]
        data = json.loads(text)
        return add_note(
            brain,
            content=data.get("content", raw[:300]),
            summary=data.get("topic", ""),
            tags=data.get("tags", []),
            category=data.get("category", "resources"),
            source="heartbeat",
        )
    except (json.JSONDecodeError, AttributeError):
        return add_note(brain, content=raw[:300], source="heartbeat")


def _store_connection(brain: BrainState, raw: str) -> None:
    """Parse agent JSON and create a connection between notes."""
    try:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0]
        data = json.loads(text)
        from_id = data.get("from", "")
        to_id = data.get("to", "")
        reason = data.get("reason", "")
        if from_id and to_id:
            connect_notes(brain, from_id, to_id, reason)
            logger.info("[connect] linked %s <-> %s: %s", from_id, to_id, reason[:80])
    except (json.JSONDecodeError, AttributeError):
        logger.debug("[connect] could not parse connection JSON")
