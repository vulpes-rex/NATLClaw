from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from config import AppConfig
from execution_log import append_entry as _log_entry
from goals import build_goals_block
from learning import extract_lessons
from prompts import load_prompt
from second_brain import (
    BrainState,
    add_note,
    add_page,
    archive_consolidated_notes,
    assign_note_to_topic,
    build_brain_summary,
    build_lint_block,
    build_wiki_summary,
    connect_notes,
    find_duplicate,
    get_recent_notes,
    get_unconsolidated_notes,
    relate_topics,
    should_consolidate,
    should_lint_wiki,
    update_page,
)
from persona_loader import Persona, load_persona
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
    *,
    seen_fps: set[str] | None = None,
) -> str:
    """Run a single workflow step: call agent, record history, extract lessons.

    Parameters
    ----------
    seen_fps:
        Shared fingerprint set for within-heartbeat dedup.  When multiple
        steps run in the same heartbeat, passing the same set prevents
        duplicate lessons across steps.
    """
    start = time.monotonic()
    try:
        response = await agent.run(prompt)
        text = response.text if hasattr(response, "text") else str(response)
    except Exception as e:
        logger.error("[%s] Failed to execute step: %s", step_name, str(e))
        logger.debug("Step execution error details:", exc_info=True)
        text = f"ERROR: Step failed - {str(e)}"
        # Still record the error in execution log (full text)
        _log_entry(step_name, prompt, text)
        raise  # Re-raise after logging for higher-level handling

    elapsed = time.monotonic() - start

    logger.info("[%s] completed in %.1fs", step_name, elapsed)
    logger.info("[%s] response: %s", step_name, text[:200])

    _log_entry(step_name, prompt, text)

    try:
        lessons = extract_lessons(
            step_name, prompt, text,
            state=state, _seen_fps=seen_fps,
        )
        state.lessons_learned.extend(lessons)
        for lesson in lessons:
            logger.info("[%s] lesson: %s", step_name, lesson["description"][:120])
    except Exception as e:
        logger.warning("[%s] Failed to extract lessons: %s", step_name, str(e))

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

    coordinator
        Multi-persona orchestration. Runs one or all personas from the
        roster each heartbeat and synthesises their outputs.
    """
    mode = persona.workflow
    if mode == "freeform":
        await _run_freeform_heartbeat(agent, state, brain, config, persona)
    elif mode == "steps":
        await _run_steps_heartbeat(agent, state, brain, config, persona)
    elif mode == "coordinator":
        await _run_coordinator_heartbeat(agent, state, brain, config, persona)
    else:
        await _run_second_brain_heartbeat(agent, state, brain, config, persona)


# ──────────────────────────────────────────────────────────────────────
# Task heartbeat (runs INSTEAD of normal heartbeat when a task is active)
# ──────────────────────────────────────────────────────────────────────

async def run_task_heartbeat(
    agent,
    state: AgentState,
    brain: BrainState,
    config: AppConfig,
    persona: Persona,
    task,
) -> None:
    """One heartbeat cycle dedicated to a task.

    Flow: plan → execute → check verdict → capture insight to brain.

    The *task* object is mutated in place (status, heartbeats_spent, etc.)
    and the caller is responsible for persisting it.
    """
    from tasks import (
        advance_task, block_task, build_task_context,
        complete_task, fail_task, start_task,
    )

    try:
        seen_fps: set[str] = set()
        start_task(task)

        task_ctx = build_task_context(task)
        brain_summary = build_brain_summary(brain, max_notes=5)

        # Step 1: Plan — what should I do this cycle?
        plan_prompt = load_prompt(
            "task", "plan",
            task_context=task_ctx,
            brain_summary=brain_summary,
            heartbeats_spent=task.heartbeats_spent + 1,
            max_heartbeats=task.max_heartbeats,
        )
        if not plan_prompt:
            plan_prompt = (
                f"You are working on: {task.title}\n{task.description}\n\n"
                f"Progress so far: {task.progress_notes[-3:]}\n"
                f"Heartbeat {task.heartbeats_spent + 1}/{task.max_heartbeats}\n\n"
                f"What is the single most important thing to do this cycle?"
            )
        plan = await _run_step(agent, "task_plan", plan_prompt, state, seen_fps=seen_fps)

        # Step 2: Execute — do the work
        execute_prompt = load_prompt(
            "task", "execute",
            plan=plan[:500],
            task_context=task_ctx,
        )
        if not execute_prompt:
            execute_prompt = (
                f"Execute this plan:\n{plan[:500]}\n\n"
                f"{task_ctx}\n\n"
                f"Use your tools to do the actual work. "
                f"If you need information from the developer, say BLOCKED: <question>."
            )
        execute_result = await _run_step(
            agent, "task_execute", execute_prompt, state, seen_fps=seen_fps,
        )

        # Step 3: Check — verdict on status
        check_prompt = load_prompt(
            "task", "check",
            task_context=task_ctx,
            execute_result=execute_result[:600],
        )
        if not check_prompt:
            check_prompt = (
                f"Task: {task.title}\n"
                f"Work done: {execute_result[:600]}\n\n"
                f"Is this task DONE, should it CONTINUE, is it BLOCKED, or has it FAILED?"
            )
        verdict = await _run_step(
            agent, "task_check", check_prompt, state, seen_fps=seen_fps,
        )

        # Step 4: Apply verdict
        verdict_upper = verdict.strip().upper()
        if verdict_upper.startswith("DONE"):
            # Extract deliverables from the verdict text
            deliverables = _extract_deliverables(verdict)
            complete_task(task, deliverables)
            logger.info("[task] Completed: %s (%d heartbeats)", task.title, task.heartbeats_spent + 1)
        elif verdict_upper.startswith("BLOCKED:"):
            question = verdict.split(":", 1)[1].strip() if ":" in verdict else verdict
            block_task(task, question, state.execution_count)
            logger.info("[task] Blocked: %s — %s", task.title, question[:100])
        elif verdict_upper.startswith("FAILED:"):
            reason = verdict.split(":", 1)[1].strip() if ":" in verdict else verdict
            fail_task(task, reason)
            logger.info("[task] Failed: %s — %s", task.title, reason[:100])
        else:
            # CONTINUE or unrecognized → advance
            advance_task(task, execute_result[:300])
            logger.info(
                "[task] Continuing: %s (%d/%d heartbeats)",
                task.title, task.heartbeats_spent, task.max_heartbeats,
            )

        # Step 5: Capture — store what was learned in the brain
        capture_prompt = load_prompt(
            "task", "capture",
            task_title=task.title,
            execute_result=execute_result[:400],
        )
        if not capture_prompt:
            capture_prompt = (
                f"You just worked on task \"{task.title}\":\n{execute_result[:400]}\n\n"
                f"Distil ONE key insight into the second brain.\n"
                f'Return JSON: {{"topic": "...", "content": "...", '
                f'"tags": [...], "category": "resources"}}\n'
                f"Return ONLY the JSON object."
            )
        capture_result = await _run_step(
            agent, "task_capture", capture_prompt, state, seen_fps=seen_fps,
        )
        note_id = _store_capture(
            brain, capture_result,
            persona_name=persona.name,
            heartbeat_number=state.execution_count,
            step=f"task_{task.id}",
        )
        if note_id:
            task.deliverables.append(f"note:{note_id}")
            logger.info("[task] captured insight as %s", note_id)

    except Exception as e:
        logger.error("Error in task heartbeat for '%s': %s", task.title, e)
        logger.debug("Task heartbeat error:", exc_info=True)
        advance_task(task, f"ERROR: {e}")


def _extract_deliverables(verdict: str) -> list[str]:
    """Pull file paths and note IDs from a DONE verdict."""
    deliverables = []
    for line in verdict.splitlines():
        stripped = line.strip().lstrip("-•*")
        stripped = stripped.strip()
        # Look for file-like paths or note IDs
        if stripped and (
            "/" in stripped
            or "\\" in stripped
            or stripped.startswith("n0")
            or stripped.startswith("note:")
            or stripped.endswith((".py", ".ts", ".js", ".tsx", ".md", ".json"))
        ):
            deliverables.append(stripped[:200])
    return deliverables[:20]


# ──────────────────────────────────────────────────────────────────────
# Mode 1: second_brain
# ──────────────────────────────────────────────────────────────────────

async def _run_second_brain_heartbeat(
    agent, state: AgentState, brain: BrainState, config: AppConfig, persona: Persona
) -> None:
    """Original 4-step knowledge-capture workflow."""
    try:
        seen_fps: set[str] = set()  # dedup fingerprints across steps
        brain_summary = build_brain_summary(brain, max_notes=8)
        goals_block = build_goals_block(state)
        lint_block = build_lint_block(brain) if state.execution_count % 10 == 0 else ""

        goals_suffix = (
            "\nAlso note progress toward any active goals." if goals_block else ""
        )

        # Step 1: Status Check
        status_prompt = load_prompt(
            "second_brain", "status_check",
            agent_name=config.agent_name,
            execution_count=state.execution_count,
            last_heartbeat=state.last_heartbeat or "never",
            past_executions=state.execution_count - 1,
            brain_summary=brain_summary,
            goals_block=goals_block,
            lint_block=lint_block,
            goals_suffix=goals_suffix,
        )
        if not status_prompt:
            # Fallback to inline prompt if template missing
            status_prompt = (
                f"You are {config.agent_name}, a knowledge-management assistant.\n"
                f"Heartbeat #{state.execution_count}.\n"
                f"Last heartbeat: {state.last_heartbeat or 'never'}.\n"
                f"Past executions: {state.execution_count - 1}.\n"
                f"\n{brain_summary}\n"
                f"{goals_block}\n"
                f"{lint_block}\n\n"
                f"Give a brief (2-3 sentence) status assessment of the system and knowledge base."
                + goals_suffix
            )
        status_result = await _run_step(agent, "status_check", status_prompt, state, seen_fps=seen_fps)

        # Step 2: Capture — structured JSON note
        capture_prompt = load_prompt(
            "second_brain", "capture",
            status_result=status_result[:200],
            note_count=len(brain.notes),
            brain_summary=brain_summary,
            heartbeat_task=persona.heartbeat_task,
        )
        if not capture_prompt:
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
        capture_result = await _run_step(agent, "capture", capture_prompt, state, seen_fps=seen_fps)
        note_id = _store_capture(
            brain, capture_result,
            persona_name=persona.name,
            heartbeat_number=state.execution_count,
        )
        if note_id:
            logger.info("[capture] stored as note %s", note_id)

        # Step 3: Connect — find relationships
        recent = get_recent_notes(brain, 6)
        if len(recent) >= 2:
            notes_text = "\n".join(
                f"  {n['id']}: {n.get('summary') or n.get('content','')[:100]}"
                for n in recent
            )
            connect_prompt = load_prompt(
                "second_brain", "connect",
                notes_text=notes_text,
            )
            if not connect_prompt:
                connect_prompt = (
                    f"Here are recent notes in the second brain:\n{notes_text}\n\n"
                    f"Identify ONE meaningful connection between any two of these notes. "
                    f"Return as JSON: {{\"from\": \"<id>\", \"to\": \"<id>\", \"reason\": \"...\"}}\n"
                    f"Return ONLY the JSON object, no extra text."
                )
            connect_result = await _run_step(agent, "connect", connect_prompt, state, seen_fps=seen_fps)
            _store_connection(brain, connect_result)

        # Step 3b (conditional): Consolidate — promote notes to wiki pages
        try:
            _ci = getattr(persona, 'consolidation_interval', None)
            _ct = getattr(persona, 'consolidation_threshold', None)
            _cons_interval = int(_ci) if _ci is not None else 5
            _cons_threshold = int(_ct) if _ct is not None else 10
        except (TypeError, ValueError):
            _cons_interval, _cons_threshold = 5, 10
        if should_consolidate(brain, _cons_interval, _cons_threshold, state.execution_count):
            await _run_consolidation_step(agent, state, brain, config, persona, seen_fps=seen_fps)

        # Step 3c (conditional): Wiki lint
        try:
            _li = getattr(persona, 'lint_wiki_interval', None)
            _lint_interval = int(_li) if _li is not None else 20
        except (TypeError, ValueError):
            _lint_interval = 20
        if should_lint_wiki(brain, _lint_interval, state.execution_count):
            await _run_wiki_lint_step(agent, state, brain, config, seen_fps=seen_fps)

        # Step 4: Review
        review_prompt = load_prompt(
            "second_brain", "review",
            status_result=status_result[:150],
            capture_result=capture_result[:150],
            note_count=len(brain.notes),
            connection_count=len(brain.connections),
            goals_block=goals_block,
            goals_suffix=(
                "\nEvaluate progress on active goals — should any be advanced, completed, or abandoned?"
                if goals_block else ""
            ),
        )
        if not review_prompt:
            review_prompt = (
                f"Summarize this heartbeat cycle for the second brain:\n"
                f"- Status: {status_result[:150]}\n"
                f"- New capture: {capture_result[:150]}\n"
                f"- Brain size: {len(brain.notes)} notes, {len(brain.connections)} connections\n"
                f"{goals_block}\n\n"
                f"Write a 2-3 sentence synthesis. Note any knowledge gaps or next areas to explore."
                + ("\nEvaluate progress on active goals — should any be advanced, completed, or abandoned?"
                   if goals_block else "")
            )
        review_result = await _run_step(agent, "review", review_prompt, state, seen_fps=seen_fps)
        brain.last_review = datetime.now(timezone.utc).isoformat()
        brain.review_log.append({
            "timestamp": brain.last_review,
            "summary": review_result[:500],
        })
    except Exception as e:
        logger.error("Error in second brain workflow: %s", str(e))
        logger.debug("Workflow error details:", exc_info=True)


# ──────────────────────────────────────────────────────────────────────
# Consolidation — promotes atomic notes to wiki pages
# ──────────────────────────────────────────────────────────────────────

async def _run_consolidation_step(
    agent, state: AgentState, brain: BrainState, config: AppConfig, persona: Persona,
    *, seen_fps: set[str] | None = None,
) -> None:
    """Gather unconsolidated notes, ask the LLM to cluster and synthesise,
    then create/update wiki pages and archive consumed notes."""
    try:
        pending = get_unconsolidated_notes(brain)
        if not pending:
            logger.info("[consolidate] No unconsolidated notes — skipping")
            return

        notes_json = json.dumps(
            [{"id": n["id"], "content": n.get("content", ""),
              "tags": n.get("tags", []), "summary": n.get("summary", "")}
             for n in pending],
            indent=2,
        )

        pages_summary = build_wiki_summary(brain) or "(no wiki pages yet)"

        consolidate_prompt = load_prompt(
            "second_brain", "consolidate",
            agent_name=config.agent_name,
            heartbeat_number=state.execution_count,
            notes_json=notes_json,
            pages_summary=pages_summary,
        )
        if not consolidate_prompt:
            consolidate_prompt = (
                f"You are maintaining a knowledge wiki for {config.agent_name}.\n\n"
                f"Here are recent atomic notes that need consolidation:\n{notes_json}\n\n"
                f"Existing wiki pages:\n{pages_summary}\n\n"
                f"For each cluster, UPDATE an existing page or CREATE a new one.\n"
                f'Return JSON: {{"updates": [...], "creates": [...], "archived_notes": [...]}}\n'
                f"Return ONLY the JSON object."
            )

        raw = await _run_step(agent, "consolidate", consolidate_prompt, state, seen_fps=seen_fps)
        _apply_consolidation(brain, raw)
        brain.last_consolidation = datetime.now(timezone.utc).isoformat()
        logger.info(
            "[consolidate] Wiki pages: %d, pending notes consumed",
            len(brain.pages),
        )
    except Exception as e:
        logger.error("Error in consolidation step: %s", str(e))
        logger.debug("Consolidation error details:", exc_info=True)


def _apply_consolidation(brain: BrainState, raw: str) -> None:
    """Parse the LLM's consolidation JSON and apply changes to the brain."""
    try:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0]
        data = json.loads(text)

        # Process page updates
        for upd in data.get("updates", []):
            page_id = upd.get("page_id", "")
            new_content = upd.get("new_content", "")
            sources_added = upd.get("sources_added", [])
            if page_id and new_content:
                update_page(brain, page_id, new_content, sources_added)
                logger.info("[consolidate] Updated page '%s' (+%d sources)", page_id, len(sources_added))

        # Process new pages
        for create in data.get("creates", []):
            title = create.get("title", "")
            content = create.get("content", "")
            sources = create.get("sources", [])
            tags = create.get("tags", [])
            if title and content:
                page_id = add_page(brain, title, content, sources, tags)
                logger.info("[consolidate] Created page '%s' (%d sources)", page_id, len(sources))

        # Archive consumed notes
        archived_ids = data.get("archived_notes", [])
        if archived_ids:
            count = archive_consolidated_notes(brain, archived_ids)
            logger.info("[consolidate] Archived %d notes", count)

    except (json.JSONDecodeError, AttributeError, UnicodeDecodeError) as e:
        logger.warning("[consolidate] Failed to parse consolidation JSON: %s", str(e))


async def _run_wiki_lint_step(
    agent, state: AgentState, brain: BrainState, config: AppConfig,
    *, seen_fps: set[str] | None = None,
) -> None:
    """Ask the LLM to audit wiki pages for quality issues."""
    try:
        if not brain.pages:
            logger.info("[wiki_lint] No wiki pages — skipping")
            return

        pages_json = json.dumps(
            [{"id": p["id"], "title": p.get("title", ""),
              "content": p.get("content", "")[:500],
              "sources": p.get("sources", []),
              "updated_at": p.get("updated_at", "")}
             for p in brain.pages.values()],
            indent=2,
        )

        lint_prompt = load_prompt(
            "second_brain", "lint_wiki",
            pages_json=pages_json,
        )
        if not lint_prompt:
            lint_prompt = (
                f"You are auditing a knowledge wiki for quality.\n\n"
                f"WIKI PAGES:\n{pages_json}\n\n"
                f"Check for stale pages, contradictions, missing citations, "
                f"duplicate content, and suspect claims.\n"
                f'Return JSON: {{"issues": [...]}}\n'
                f"Return ONLY the JSON object."
            )

        raw = await _run_step(agent, "wiki_lint", lint_prompt, state, seen_fps=seen_fps)
        _apply_wiki_lint(brain, raw)
        brain.last_lint = datetime.now(timezone.utc).isoformat()
    except Exception as e:
        logger.error("Error in wiki lint step: %s", str(e))
        logger.debug("Wiki lint error details:", exc_info=True)


def _apply_wiki_lint(brain: BrainState, raw: str) -> None:
    """Parse wiki lint JSON and store issues in the brain's lint log."""
    try:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0]
        data = json.loads(text)
        issues = data.get("issues", [])
        if issues:
            brain.lint_log.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "issues": issues,
            })
            logger.info("[wiki_lint] Found %d issues", len(issues))
        else:
            logger.info("[wiki_lint] No issues found")
    except (json.JSONDecodeError, AttributeError, UnicodeDecodeError) as e:
        logger.warning("[wiki_lint] Failed to parse lint JSON: %s", str(e))


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
    try:
        seen_fps: set[str] = set()
        brain_summary = build_brain_summary(brain, max_notes=5)
        goals_block = build_goals_block(state)
        lint_block = build_lint_block(brain) if state.execution_count % 10 == 0 else ""

        goals_suffix = (
            "\nAlso note progress toward any active goals." if goals_block else ""
        )

        # Step 1: Status Check — scoped to the persona's domain
        status_prompt = load_prompt(
            "freeform", "status_check",
            agent_name=config.agent_name,
            persona_description=persona.description,
            execution_count=state.execution_count,
            last_heartbeat=state.last_heartbeat or "never",
            brain_summary=brain_summary,
            goals_block=goals_block,
            lint_block=lint_block,
            goals_suffix=goals_suffix,
        )
        if not status_prompt:
            status_prompt = (
                f"You are {config.agent_name} acting as a {persona.description}.\n"
                f"Heartbeat #{state.execution_count}.\n"
                f"Last heartbeat: {state.last_heartbeat or 'never'}.\n"
                f"{brain_summary}\n"
                f"{goals_block}\n"
                f"{lint_block}\n\n"
                f"Give a brief (2-3 sentence) status assessment relevant to your role."
                + goals_suffix
            )
        status_result = await _run_step(agent, "status_check", status_prompt, state, seen_fps=seen_fps)

        # Step 2: Main task — fully freeform, tools available
        task_prompt = load_prompt(
            "freeform", "task",
            status_result=status_result[:200],
            heartbeat_task=persona.heartbeat_task,
        )
        if not task_prompt:
            task_prompt = (
                f"Status: {status_result[:200]}\n\n"
                f"{persona.heartbeat_task}"
            )
        task_result = await _run_step(agent, "task", task_prompt, state, seen_fps=seen_fps)

        # Step 3: Capture — save a plain-text insight to the brain
        capture_prompt = load_prompt(
            "freeform", "capture",
            task_result=task_result[:400],
        )
        if not capture_prompt:
            capture_prompt = (
                f"You just completed this task:\n{task_result[:400]}\n\n"
                f"Distil ONE key insight or finding worth remembering in the second brain.\n"
                f"Return as JSON with these exact keys:\n"
                f'{{"topic": "short title", "content": "1-2 sentence insight", '
                f'"tags": ["tag1", "tag2"], "category": "resources"}}\n'
                f"Return ONLY the JSON object, no extra text."
            )
        capture_result = await _run_step(agent, "capture", capture_prompt, state, seen_fps=seen_fps)
        note_id = _store_capture(
            brain, capture_result,
            persona_name=persona.name,
            heartbeat_number=state.execution_count,
        )
        if note_id:
            logger.info("[capture] stored as note %s", note_id)

        # Step 4: Review
        review_prompt = load_prompt(
            "freeform", "review",
            execution_count=state.execution_count,
            persona_description=persona.description,
            task_result=task_result[:200],
            note_count=len(brain.notes),
            goals_block=goals_block,
            goals_suffix=(
                "\nEvaluate progress on active goals — should any be advanced, completed, or abandoned?"
                if goals_block else ""
            ),
        )
        if not review_prompt:
            review_prompt = (
                f"Summarize heartbeat #{state.execution_count} as {persona.description}:\n"
                f"- Task outcome: {task_result[:200]}\n"
                f"- Brain size: {len(brain.notes)} notes\n"
                f"{goals_block}\n\n"
                f"Write a 2-3 sentence synthesis and note what to do next cycle."
                + ("\nEvaluate progress on active goals — should any be advanced, completed, or abandoned?"
                   if goals_block else "")
            )
        review_result = await _run_step(agent, "review", review_prompt, state, seen_fps=seen_fps)
        brain.last_review = datetime.now(timezone.utc).isoformat()
        brain.review_log.append({
            "timestamp": brain.last_review,
            "summary": review_result[:500],
        })
    except Exception as e:
        logger.error("Error in freeform workflow: %s", str(e))
        logger.debug("Workflow error details:", exc_info=True)


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
    try:
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

        seen_fps: set[str] = set()
        if persona.stepwise:
            await _run_one_step(agent, state, brain, config, persona, brain_summary, default_context, seen_fps=seen_fps)
        else:
            await _run_all_steps(agent, state, brain, persona, brain_summary, default_context, seen_fps=seen_fps)

        brain.last_review = datetime.now(timezone.utc).isoformat()
    except Exception as e:
        logger.error("Error in steps workflow: %s", str(e))
        logger.debug("Workflow error details:", exc_info=True)


async def _run_all_steps(
    agent, state: AgentState, brain: BrainState, persona: Persona,
    brain_summary: str, initial_prev: str,
    *, seen_fps: set[str] | None = None,
) -> None:
    """Run all persona steps in a single heartbeat cycle."""
    try:
        prev = initial_prev
        for step_def in persona.steps:
            name = step_def.get("name", "step")
            prompt = step_def.get("prompt", "").replace("{prev}", prev).replace("{brain}", brain_summary)
            store = step_def.get("storeToBrain", False)

            result = await _run_step(agent, name, prompt, state, seen_fps=seen_fps)
            prev = result

            if store:
                await _distil_to_brain(agent, state, brain, name, result, persona_name=persona.name)
    except Exception as e:
        logger.error("Error in _run_all_steps: %s", str(e))
        logger.debug("Error details:", exc_info=True)


async def _run_one_step(
    agent, state: AgentState, brain: BrainState, config: AppConfig, persona: Persona,
    brain_summary: str, default_context: str,
    *, seen_fps: set[str] | None = None,
) -> None:
    """Run exactly one step this heartbeat, advancing the stepwise pointer."""
    try:
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
        result = await _run_step(agent, name, prompt, state, seen_fps=seen_fps)

        # Persist progress for the next heartbeat
        state.context[idx_key] = idx + 1
        state.context[prev_key] = result[:2000]  # cap stored context size

        if store:
            await _distil_to_brain(agent, state, brain, name, result, persona_name=persona.name)
    except Exception as e:
        logger.error("Error in _run_one_step: %s", str(e))
        logger.debug("Error details:", exc_info=True)


async def _distil_to_brain(
    agent, state: AgentState, brain: BrainState, step_name: str, result: str,
    *, persona_name: str = "",
) -> None:
    """Ask the agent to extract an insight and store it as a brain note."""
    try:
        distil_prompt = load_prompt(
            "steps", "distil",
            result=result[:400],
        )
        if not distil_prompt:
            distil_prompt = (
                f"Distil ONE key insight from this output into the second brain.\n\n"
                f"Output:\n{result[:400]}\n\n"
                f"Return JSON: {{\"topic\": \"...\", \"content\": \"...\", "
                f"\"tags\": [...], \"category\": \"resources\"}}\n"
                f"Return ONLY the JSON object."
            )
        distil_result = await _run_step(agent, f"{step_name}_capture", distil_prompt, state)
        note_id = _store_capture(
            brain, distil_result,
            persona_name=persona_name,
            heartbeat_number=state.execution_count,
            step=step_name,
        )
        if note_id:
            logger.info("[%s] stored as note %s", step_name, note_id)
    except Exception as e:
        logger.error("Error in _distil_to_brain: %s", str(e))
        logger.debug("Error details:", exc_info=True)


# ──────────────────────────────────────────────────────────────────────
# Mode 4: coordinator (multi-persona orchestration)
# ──────────────────────────────────────────────────────────────────────

async def _run_coordinator_heartbeat(
    agent, state: AgentState, brain: BrainState, config: AppConfig, persona: Persona
) -> None:
    """Run one or all personas from the roster and synthesise outputs.

    Schedule modes
    --------------
    round_robin  Run the next persona in the roster (cycles).
    all          Run every persona in sequence, then synthesise.
    """
    try:
        roster = persona.roster
        if not roster:
            logger.warning(
                "Coordinator persona '%s' has an empty roster; falling back to freeform",
                persona.name,
            )
            await _run_freeform_heartbeat(agent, state, brain, config, persona)
            return

        schedule = persona.schedule or "round_robin"
        idx_key = f"coordinator_{persona.name}_idx"

        if schedule == "round_robin":
            idx = state.context.get(idx_key, 0) % len(roster)
            selected = [roster[idx]]
            state.context[idx_key] = idx + 1
        else:  # "all"
            selected = list(roster)

        outputs: list[str] = []
        for pname in selected:
            try:
                sub_persona = load_persona(pname)
                logger.info("[coordinator] Running sub-persona '%s'", pname)
                # Delegate to the sub-persona's own workflow
                await run_heartbeat(agent, state, brain, config, sub_persona)
                outputs.append(f"{pname}: completed heartbeat")
            except Exception as sub_err:
                logger.error("[coordinator] Sub-persona '%s' failed: %s", pname, sub_err)
                outputs.append(f"{pname}: FAILED — {sub_err}")

        # Synthesis step — summarise across all persona outputs
        goals_block = build_goals_block(state)
        synth_prompt = load_prompt(
            "coordinator", "synthesis",
            agent_name=config.agent_name,
            execution_count=state.execution_count,
            selected_personas=", ".join(selected),
            outputs="\n".join(f"  - {o}" for o in outputs),
            note_count=len(brain.notes),
            connection_count=len(brain.connections),
            goals_block=goals_block,
        )
        if not synth_prompt:
            synth_prompt = (
                f"You are {config.agent_name} coordinating multiple personas.\n"
                f"Heartbeat #{state.execution_count}.\n"
                f"Personas run this cycle: {', '.join(selected)}\n"
                f"Results:\n" + "\n".join(f"  - {o}" for o in outputs) + "\n\n"
                f"Brain has {len(brain.notes)} notes, {len(brain.connections)} connections.\n"
                f"{goals_block}\n\n"
                f"Write a 2-3 sentence synthesis of what was accomplished and what to focus next."
            )
        seen_fps: set[str] = set()
        synth_result = await _run_step(agent, "coordinator_synthesis", synth_prompt, state, seen_fps=seen_fps)
        brain.last_review = datetime.now(timezone.utc).isoformat()
        brain.review_log.append({
            "timestamp": brain.last_review,
            "summary": synth_result[:500],
        })
    except Exception as e:
        logger.error("Error in coordinator workflow: %s", str(e))
        logger.debug("Workflow error details:", exc_info=True)


# ──────────────────────────────────────────────────────────────────────
# Brain helpers (shared)
# ──────────────────────────────────────────────────────────────────────

def _extract_json(raw: str, required_key: str = "content") -> dict | None:
    """Find and parse a JSON object from *raw*, tolerating preamble text.

    Strategy:
    1. Strip code fences (```json ... ```)
    2. Try json.loads on the whole text.
    3. If that fails, scan for every '{' and try to parse from that offset;
       accept the first object that contains *required_key*.
    """
    text = raw.strip()
    # Strip code fences
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        text = text.rsplit("```", 1)[0].strip()
    # Fast path: whole text is valid JSON
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    # Slow path: find embedded JSON objects
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            data = json.loads(text[i:])
            if isinstance(data, dict) and (not required_key or required_key in data):
                return data
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def _store_capture(
    brain: BrainState,
    raw: str,
    *,
    persona_name: str = "",
    heartbeat_number: int = 0,
    step: str = "capture",
) -> str | None:
    """Parse agent JSON output and store as a note. Returns note ID or None.

    If a near-duplicate note already exists, the existing note is updated
    instead of creating a new one.
    """
    try:
        data = _extract_json(raw, required_key="content")
        if data is None:
            logger.warning("_store_capture: no JSON object with 'content' key found")
            return add_note(brain, content=raw[:300], source="heartbeat")
        content = data.get("content", raw[:300])

        tags = data.get("tags", [])

        # Dedup check — merge into existing note if near-duplicate
        dup_id = find_duplicate(brain, content)
        if dup_id:
            existing = brain.notes[dup_id]
            existing["content"] = content
            existing["updated_at"] = datetime.now(timezone.utc).isoformat()
            for tag in tags:
                if tag not in existing.get("tags", []):
                    existing.setdefault("tags", []).append(tag)
            # Still assign topics so graph stays current
            for tag in tags:
                assign_note_to_topic(brain, dup_id, tag)
            _relate_cooccurring_tags(brain, tags)
            logger.info("[capture] merged into existing note %s (dedup)", dup_id)
            return dup_id

        source_meta: dict | str = {
            "type": "heartbeat",
            "persona": persona_name,
            "heartbeat_number": heartbeat_number,
            "step": step,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        } if persona_name else "heartbeat"

        note_id = add_note(
            brain,
            content=content,
            summary=data.get("topic", ""),
            tags=tags,
            category=data.get("category", "resources"),
            source=source_meta,
            note_type=data.get("note_type", "general"),
            status=data.get("status", "active"),
            confidence=data.get("confidence"),
            evidence=data.get("evidence"),
        )

        # Wire note into the topic graph
        if note_id:
            for tag in tags:
                assign_note_to_topic(brain, note_id, tag)
            _relate_cooccurring_tags(brain, tags)

        return note_id
    except (json.JSONDecodeError, AttributeError, UnicodeDecodeError) as e:
        logger.warning("_store_capture failed to parse JSON: %s", str(e))
        logger.debug("JSON parsing error details:", exc_info=True)
        try:
            return add_note(brain, content=raw[:300], source="heartbeat")
        except Exception as add_note_error:
            logger.error("Failed to add fallback note: %s", str(add_note_error))
            logger.debug("add_note error details:", exc_info=True)
            return None


def _relate_cooccurring_tags(brain: BrainState, tags: list[str]) -> None:
    """Relate all tags that appear together on the same note.

    If a capture has tags ["React", "state", "hooks"], this creates
    bidirectional topic relationships: React<->state, React<->hooks,
    state<->hooks — so traversing any one topic reaches the others.
    """
    for i, a in enumerate(tags):
        for b in tags[i + 1:]:
            relate_topics(brain, a, b)


def _store_connection(brain: BrainState, raw: str) -> None:
    """Parse agent JSON and create a connection between notes."""
    try:
        data = _extract_json(raw, required_key="from")
        if data is None:
            logger.warning("_store_connection: no JSON object with 'from' key found")
            return
        from_id = data.get("from", "")
        to_id = data.get("to", "")
        reason = data.get("reason", "")
        if from_id and to_id:
            connect_notes(brain, from_id, to_id, reason)
            logger.info("[connect] linked %s <-> %s: %s", from_id, to_id, reason[:80])
    except (json.JSONDecodeError, AttributeError, UnicodeDecodeError) as e:
        logger.warning("_store_connection failed to parse JSON: %s", str(e))
        logger.debug("JSON parsing error details:", exc_info=True)
