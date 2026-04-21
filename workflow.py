from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from config import AppConfig
from capture_policy import (
    DEFAULT_CAPTURE_POLICY,
    CapturePolicy,
    has_substantive_evidence,
    run_after_capture_hook,
)
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


def _effective_capture_policy(persona: Any) -> CapturePolicy:
    """Return the persona's capture policy, or defaults (for tests using bare mocks)."""
    pol = getattr(persona, "capture_policy", None)
    if isinstance(pol, CapturePolicy):
        return pol
    return DEFAULT_CAPTURE_POLICY


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
    _text_sink: list[str] | None = None,
) -> str:
    """Run a single workflow step: call agent, record history, extract lessons.

    Parameters
    ----------
    seen_fps:
        Shared fingerprint set for within-heartbeat dedup.  When multiple
        steps run in the same heartbeat, passing the same set prevents
        duplicate lessons across steps.
    _text_sink:
        Optional mutable list.  When provided, the raw agent response text
        is appended so callers can scan for structured output (e.g. REPLY TO
        blocks) after all steps complete.
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

    if _text_sink is not None:
        _text_sink.append(text)

    return text


# ──────────────────────────────────────────────────────────────────────
# Dispatcher
# ──────────────────────────────────────────────────────────────────────

async def run_heartbeat(
    agent,
    state: AgentState,
    brain: BrainState,
    config: AppConfig,
    persona: Persona,
    *,
    inbox: list | None = None,
    outbox: list | None = None,
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

    Move A: inbox / outbox
        When *inbox* and *outbox* are supplied the workflow will scan agent
        responses for ``REPLY TO <msg_id>: text`` blocks and convert them
        into outbound messages (appended to *outbox*) while marking the
        originals in *inbox* as read.  Callers are responsible for
        persisting both after this function returns.
    """
    # Collect raw step texts so we can scan for REPLY TO blocks after the workflow.
    _text_sink: list[str] | None = [] if (inbox is not None and outbox is not None) else None

    mode = persona.workflow
    if mode == "freeform":
        await _run_freeform_heartbeat(agent, state, brain, config, persona, _text_sink=_text_sink)
    elif mode == "steps":
        await _run_steps_heartbeat(agent, state, brain, config, persona, _text_sink=_text_sink)
    elif mode == "coordinator":
        await _run_coordinator_heartbeat(agent, state, brain, config, persona, _text_sink=_text_sink)
    else:
        await _run_second_brain_heartbeat(agent, state, brain, config, persona, _text_sink=_text_sink)

    # Move A: process any REPLY TO blocks the agent emitted during this heartbeat
    if _text_sink and inbox is not None and outbox is not None:
        combined = "\n".join(_text_sink)
        replies = _extract_replies(combined)
        if replies:
            await _process_agent_replies(
                replies, inbox, outbox,
                persona_name=persona.name,
                state_file=config.state_file,
            )


# ──────────────────────────────────────────────────────────────────────
# Task negotiation helper (Move B)
# ──────────────────────────────────────────────────────────────────────

async def _run_negotiation_step(
    agent,
    state: AgentState,
    brain: BrainState,
    task,
    persona: Persona,
    *,
    seen_fps: set | None = None,
) -> dict:
    """Run a single negotiation step asking the agent whether to accept/redirect/clarify.

    Returns a dict with keys:
        action: "accept" | "redirect" | "blocked"
        to_persona: str  (for redirect)
        reason: str      (for redirect / blocked)
    """
    from tasks import build_task_context
    seen_fps = seen_fps or set()
    task_ctx = build_task_context(task)
    brain_summary = build_brain_summary(brain, max_notes=3)

    prompt = load_prompt("task", "negotiate", task_context=task_ctx, brain_summary=brain_summary)
    if not prompt:
        prompt = (
            f"You have been offered a new task:\n\n{task_ctx}\n\n"
            f"Brain context:\n{brain_summary}\n\n"
            f"Before accepting, review the task and respond with ONE of:\n"
            f"  ACCEPT TASK {task.id} — you can do this work\n"
            f"  REDIRECT TASK {task.id} TO @<persona>: <reason> — another persona is better suited\n"
            f"  CLARIFY TASK {task.id}: <question> — you need more information first\n\n"
            f"Respond with just one of those lines."
        )

    raw = await _run_step(agent, "task_negotiate", prompt, state, seen_fps=seen_fps)
    return _parse_negotiation_response(raw, task.id)


def _parse_negotiation_response(text: str, task_id: str) -> dict:
    """Parse agent negotiation output into an action dict."""
    import re

    # ACCEPT TASK <id>
    if re.search(rf"ACCEPT\s+TASK\s+{re.escape(task_id)}", text, re.IGNORECASE):
        return {"action": "accept", "to_persona": "", "reason": ""}

    # REDIRECT TASK <id> TO @<persona>: <reason>
    redirect_m = re.search(
        rf"REDIRECT\s+TASK\s+{re.escape(task_id)}\s+TO\s+@([\w_-]+)\s*:?\s*(.*)",
        text, re.IGNORECASE,
    )
    if redirect_m:
        return {
            "action": "redirect",
            "to_persona": redirect_m.group(1).strip(),
            "reason": redirect_m.group(2).strip(),
        }

    # CLARIFY TASK <id>: <question>
    clarify_m = re.search(
        rf"CLARIFY\s+TASK\s+{re.escape(task_id)}\s*:?\s*(.*)",
        text, re.IGNORECASE,
    )
    if clarify_m:
        return {
            "action": "blocked",
            "to_persona": "",
            "reason": clarify_m.group(1).strip(),
        }

    # Default: accept (if agent didn't give a recognized response, proceed)
    return {"action": "accept", "to_persona": "", "reason": ""}


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
) -> list:
    """One heartbeat cycle dedicated to a task.

    Flow: plan → execute → check verdict → capture insight to brain.

    The *task* object is mutated in place (status, heartbeats_spent, etc.)
    and the caller is responsible for persisting it.

    Returns a list of :class:`messaging.Message` objects generated during
    this heartbeat (task completed, blocked, failed).  The caller is
    responsible for appending them to the outbox and persisting.
    """
    from tasks import (
        advance_task, assign_task, accept_negotiated_task, block_task,
        build_task_context, complete_task, fail_task, negotiate_task,
        redirect_task, start_task,
    )

    _outbox: list = []  # collects Message objects for the caller
    try:
        seen_fps: set[str] = set()

        # Negotiation gate (Move B): if enabled, run a pre-work negotiation step
        # so the agent can accept, redirect, or clarify before committing.
        if task.status == "pending" and getattr(config, "task_negotiation_enabled", False):
            negotiate_task(task, persona.name)
            negotiation_result = await _run_negotiation_step(
                agent, state, brain, task, persona, seen_fps=seen_fps,
            )
            from messaging import emit_task_blocked, emit_task_redirected
            action = negotiation_result.get("action", "accept")
            if action == "redirect":
                to_persona = negotiation_result.get("to_persona", "")
                reason = negotiation_result.get("reason", "")
                redirect_task(task, to_persona, reason)
                _outbox.append(emit_task_redirected(
                    task, to_persona, reason, persona=persona.name, heartbeat=state.execution_count,
                ))
                logger.info("[task] Redirected '%s' to @%s", task.title, to_persona)
                return _outbox
            elif action == "blocked":
                question = negotiation_result.get("reason", "Agent needs clarification")
                block_task(task, question, state.execution_count)
                _outbox.append(emit_task_blocked(
                    task, question, persona=persona.name, heartbeat=state.execution_count,
                ))
                logger.info("[task] Blocked at negotiation: '%s' — %s", task.title, question[:80])
                return _outbox
            else:
                # accept (or unknown): move forward
                accept_negotiated_task(task)

        elif task.status == "pending":
            assign_task(task, persona.name)

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
                f"Is this task DONE, should it CONTINUE, is it BLOCKED, or has it FAILED?\n"
                f"If requirements are unclear and need PM + Dev + QA alignment before you can start, "
                f"say THREE_AMIGOS: <open question>."
            )
        verdict = await _run_step(
            agent, "task_check", check_prompt, state, seen_fps=seen_fps,
        )

        # Step 4: Apply verdict and emit messages
        from messaging import (
            emit_task_blocked, emit_task_completed, emit_task_failed,
            emit_three_amigos,
        )

        verdict_upper = verdict.strip().upper()
        if verdict_upper.startswith("DONE"):
            # Extract deliverables from the verdict text
            deliverables = _extract_deliverables(verdict)
            complete_task(task, deliverables)
            logger.info("[task] Completed: %s (%d heartbeats)", task.title, task.heartbeats_spent + 1)
            _outbox.append(emit_task_completed(
                task, persona=persona.name, heartbeat=state.execution_count,
            ))
        elif verdict_upper.startswith("THREE_AMIGOS:"):
            question = verdict.split(":", 1)[1].strip() if ":" in verdict else verdict
            block_task(task, question, state.execution_count)
            logger.info("[task] Three amigos needed: %s -- %s", task.title, question[:100])
            _outbox.append(emit_three_amigos(
                task, question, persona=persona.name, heartbeat=state.execution_count,
            ))
        elif verdict_upper.startswith("BLOCKED:"):
            question = verdict.split(":", 1)[1].strip() if ":" in verdict else verdict
            block_task(task, question, state.execution_count)
            logger.info("[task] Blocked: %s -- %s", task.title, question[:100])
            _outbox.append(emit_task_blocked(
                task, question, persona=persona.name, heartbeat=state.execution_count,
            ))
        elif verdict_upper.startswith("FAILED:"):
            reason = verdict.split(":", 1)[1].strip() if ":" in verdict else verdict
            fail_task(task, reason)
            logger.info("[task] Failed: %s — %s", task.title, reason[:100])
            _outbox.append(emit_task_failed(
                task, reason, persona=persona.name, heartbeat=state.execution_count,
            ))
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
                f"Distil ONE key insight from this work.\n"
                f"Do NOT call any tools or functions. The system will store the note automatically.\n"
                f'Just respond with a JSON object: {{"topic": "...", "content": "...", '
                f'"tags": [...], "category": "resources"}}\n'
                f"Return ONLY the JSON object, no extra text."
            )
        capture_result = await _run_step(
            agent, "task_capture", capture_prompt, state, seen_fps=seen_fps,
        )
        note_id = _store_capture(
            brain, capture_result,
            persona_name=persona.name,
            capture_policy=_effective_capture_policy(persona),
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

    return _outbox


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


def _extract_replies(text: str) -> list[dict]:
    """Parse ``REPLY TO <msg_id>: body`` blocks from agent output.

    The agent is instructed (via the inbound message block) to use this
    format to reply to specific messages.  Multiple REPLY TO blocks in a
    single response are supported.

    Returns a list of ``{"reply_to": str, "body": str}`` dicts.
    """
    import re

    pattern = re.compile(
        r"REPLY\s+TO\s+([a-zA-Z0-9]+)\s*:\s*(.*?)(?=REPLY\s+TO\s+[a-zA-Z0-9]+\s*:|$)",
        re.DOTALL | re.IGNORECASE,
    )
    replies = []
    for match in pattern.finditer(text):
        msg_id = match.group(1).strip()
        body = match.group(2).strip()
        if msg_id and body:
            replies.append({"reply_to": msg_id, "body": body[:2000]})
    return replies


async def _process_agent_replies(
    replies: list[dict],
    inbox_messages: list,
    outbox: list,
    persona_name: str,
    state_file: str,
) -> None:
    """Convert extracted reply dicts into outbound messages and mark originals read.

    *inbox_messages* and *outbox* are mutated in-place; the caller is
    responsible for persisting both after this function returns.
    """
    from messaging import (
        Message,
        append_message,
        create_message,
        find_message,
        mark_read,
    )

    for r in replies:
        original = find_message(inbox_messages, r["reply_to"])
        thread_id = original.thread_id if original else r["reply_to"]
        addressed_to = original.sender if original else "developer"

        reply_msg = create_message(
            "fyi",
            title=f"Reply from @{persona_name} to {r['reply_to']}",
            body=r["body"],
            persona=persona_name,
        )
        reply_msg.sender = persona_name
        reply_msg.addressed_to = addressed_to
        reply_msg.reply_to = r["reply_to"]
        reply_msg.thread_id = thread_id

        append_message(outbox, reply_msg)

        if original:
            mark_read(original)
            logger.info(
                "[replies] Agent @%s replied to %s (thread=%s)",
                persona_name, r["reply_to"], thread_id,
            )
        else:
            logger.warning(
                "[replies] Agent @%s wrote REPLY TO %s but that message was not found in inbox",
                persona_name, r["reply_to"],
            )


# ──────────────────────────────────────────────────────────────────────
# Mode 1: second_brain
# ──────────────────────────────────────────────────────────────────────

async def _run_second_brain_heartbeat(
    agent, state: AgentState, brain: BrainState, config: AppConfig, persona: Persona,
    *, _text_sink: list[str] | None = None,
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
        status_result = await _run_step(agent, "status_check", status_prompt, state, seen_fps=seen_fps, _text_sink=_text_sink)

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
        capture_result = await _run_step(agent, "capture", capture_prompt, state, seen_fps=seen_fps, _text_sink=_text_sink)
        note_id = _store_capture(
            brain, capture_result,
            persona_name=persona.name,
            capture_policy=_effective_capture_policy(persona),
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
            connect_result = await _run_step(agent, "connect", connect_prompt, state, seen_fps=seen_fps, _text_sink=_text_sink)
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
        review_result = await _run_step(agent, "review", review_prompt, state, seen_fps=seen_fps, _text_sink=_text_sink)
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
    agent, state: AgentState, brain: BrainState, config: AppConfig, persona: Persona,
    *, _text_sink: list[str] | None = None,
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
        status_result = await _run_step(agent, "status_check", status_prompt, state, seen_fps=seen_fps, _text_sink=_text_sink)

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
        task_result = await _run_step(agent, "task", task_prompt, state, seen_fps=seen_fps, _text_sink=_text_sink)

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
        capture_result = await _run_step(agent, "capture", capture_prompt, state, seen_fps=seen_fps, _text_sink=_text_sink)
        note_id = _store_capture(
            brain, capture_result,
            persona_name=persona.name,
            capture_policy=_effective_capture_policy(persona),
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
        review_result = await _run_step(agent, "review", review_prompt, state, seen_fps=seen_fps, _text_sink=_text_sink)
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
    agent, state: AgentState, brain: BrainState, config: AppConfig, persona: Persona,
    *, _text_sink: list[str] | None = None,
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
            await _run_freeform_heartbeat(agent, state, brain, config, persona, _text_sink=_text_sink)
            return

        brain_summary = build_brain_summary(brain, max_notes=5)
        default_context = (
            f"You are {config.agent_name} acting as a {persona.description}. "
            f"Heartbeat #{state.execution_count}. "
            f"Brain has {len(brain.notes)} notes.\n{brain_summary}"
        )

        seen_fps: set[str] = set()
        if persona.stepwise:
            await _run_one_step(agent, state, brain, config, persona, brain_summary, default_context, seen_fps=seen_fps, _text_sink=_text_sink)
        else:
            await _run_all_steps(agent, state, brain, persona, brain_summary, default_context, seen_fps=seen_fps, _text_sink=_text_sink)

        brain.last_review = datetime.now(timezone.utc).isoformat()
    except Exception as e:
        logger.error("Error in steps workflow: %s", str(e))
        logger.debug("Workflow error details:", exc_info=True)


async def _run_all_steps(
    agent, state: AgentState, brain: BrainState, persona: Persona,
    brain_summary: str, initial_prev: str,
    *, seen_fps: set[str] | None = None, _text_sink: list[str] | None = None,
) -> None:
    """Run all persona steps in a single heartbeat cycle."""
    try:
        prev = initial_prev
        for step_def in persona.steps:
            name = step_def.get("name", "step")
            prompt = step_def.get("prompt", "").replace("{prev}", prev).replace("{brain}", brain_summary)
            store = step_def.get("storeToBrain", False)

            result = await _run_step(agent, name, prompt, state, seen_fps=seen_fps, _text_sink=_text_sink)
            prev = result

            if store:
                await _distil_to_brain(agent, state, brain, name, result, persona=persona)
    except Exception as e:
        logger.error("Error in _run_all_steps: %s", str(e))
        logger.debug("Error details:", exc_info=True)


async def _run_one_step(
    agent, state: AgentState, brain: BrainState, config: AppConfig, persona: Persona,
    brain_summary: str, default_context: str,
    *, seen_fps: set[str] | None = None, _text_sink: list[str] | None = None,
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
        result = await _run_step(agent, name, prompt, state, seen_fps=seen_fps, _text_sink=_text_sink)

        # Persist progress for the next heartbeat
        state.context[idx_key] = idx + 1
        state.context[prev_key] = result[:2000]  # cap stored context size

        if store:
            await _distil_to_brain(agent, state, brain, name, result, persona=persona)
    except Exception as e:
        logger.error("Error in _run_one_step: %s", str(e))
        logger.debug("Error details:", exc_info=True)


async def _distil_to_brain(
    agent, state: AgentState, brain: BrainState, step_name: str, result: str,
    *, persona: Persona,
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
                f"\"tags\": [...], \"category\": \"resources\", "
                f"\"evidence\": [\"commit:<sha> or file:path\"], \"confidence\": 0-100}}\n"
                f"Return ONLY the JSON object."
            )
        distil_result = await _run_step(agent, f"{step_name}_capture", distil_prompt, state)
        note_id = _store_capture(
            brain, distil_result,
            persona_name=persona.name,
            heartbeat_number=state.execution_count,
            step=step_name,
            capture_policy=_effective_capture_policy(persona),
        )
        if note_id:
            logger.info("[%s] stored as note %s", step_name, note_id)
    except Exception as e:
        logger.error("Error in _distil_to_brain: %s", str(e))
        logger.debug("Error details:", exc_info=True)


# ──────────────────────────────────────────────────────────────────────
# Mode 4: coordinator (multi-persona orchestration)
# ──────────────────────────────────────────────────────────────────────


def _build_task_board_block(tasks: list, roster: list[str]) -> str:
    """Format a task-board summary for the coordinator synthesis prompt."""
    from tasks import (
        Task,
        active_file_locks,
        get_all_pending_tasks,
        get_active_task,
        get_blocked_tasks,
        unmet_dependencies,
    )

    lines: list[str] = ["== TASK BOARD =="]

    # Per-persona active work
    for pname in roster:
        active = get_active_task(tasks, pname)
        if active:
            locks = ", ".join(active.file_locks[:5]) if active.file_locks else "(none)"
            lines.append(
                f"  @{pname}: [{active.id}] {active.title} "
                f"({active.heartbeats_spent}/{active.max_heartbeats} hb, locks: {locks})"
            )
        else:
            lines.append(f"  @{pname}: idle")

    # Pending queue
    pending = get_all_pending_tasks(tasks)
    if pending:
        lines.append(f"\nPending ({len(pending)}):")
        for t in pending[:8]:
            target = f" ->@{t.target_persona}" if t.target_persona else ""
            deps = ""
            unmet = unmet_dependencies(t, tasks)
            if unmet:
                deps = f" [waiting: {','.join(unmet)}]"
            lines.append(f"  [{t.id}] {t.title}{target}{deps}")

    # Blocked
    blocked = get_blocked_tasks(tasks)
    if blocked:
        lines.append(f"\nBlocked ({len(blocked)}):")
        for t in blocked[:5]:
            q = t.questions[-1].get("question", "")[:60] if t.questions else "?"
            lines.append(f"  [{t.id}] {t.title} — Q: {q}")

    # File lock summary
    locks = active_file_locks(tasks)
    if locks:
        lines.append(f"\nFile locks ({len(locks)}):")
        for path, tid in sorted(locks.items())[:10]:
            lines.append(f"  {path} <- {tid}")

    return "\n".join(lines)


async def _run_coordinator_heartbeat(
    agent, state: AgentState, brain: BrainState, config: AppConfig, persona: Persona,
    *, _text_sink: list[str] | None = None,
) -> None:
    """Run one or all personas from the roster and synthesise outputs.

    Schedule modes
    --------------
    round_robin  Run the next persona in the roster (cycles).
    all          Run every persona in sequence, then synthesise.
    task_routed  Only run personas that have routed tasks waiting.
    """
    from tasks import (
        check_file_conflicts,
        get_active_task,
        get_pending_tasks as get_pending_tasks_fn,
        load_tasks,
    )

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

        tasks = await load_tasks(config.state_file)

        if schedule == "task_routed":
            # Only run personas that have routed pending tasks or active work
            selected = []
            for pname in roster:
                has_active = get_active_task(tasks, pname) is not None
                has_pending = len(get_pending_tasks_fn(tasks, pname)) > 0
                if has_active or has_pending:
                    selected.append(pname)
            if not selected:
                # Nothing routed; fall back to round_robin for general work
                idx = state.context.get(idx_key, 0) % len(roster)
                selected = [roster[idx]]
                state.context[idx_key] = idx + 1
        elif schedule == "round_robin":
            idx = state.context.get(idx_key, 0) % len(roster)
            selected = [roster[idx]]
            state.context[idx_key] = idx + 1
        else:  # "all"
            selected = list(roster)

        outputs: list[str] = []
        for pname in selected:
            try:
                sub_persona = load_persona(pname)

                # Check for file lock conflicts before running
                active = get_active_task(tasks, pname)
                if active and active.file_locks:
                    conflicts = check_file_conflicts(active, tasks)
                    if conflicts:
                        conflict_detail = ", ".join(
                            f"{p} (held by {tid})" for p, tid in conflicts
                        )
                        logger.warning(
                            "[coordinator] Skipping '%s': file conflicts — %s",
                            pname, conflict_detail,
                        )
                        outputs.append(
                            f"{pname}: SKIPPED — file lock conflict ({conflict_detail})"
                        )
                        continue

                logger.info("[coordinator] Running sub-persona '%s'", pname)
                await run_heartbeat(agent, state, brain, config, sub_persona)
                outputs.append(f"{pname}: completed heartbeat")
            except Exception as sub_err:
                logger.error("[coordinator] Sub-persona '%s' failed: %s", pname, sub_err)
                outputs.append(f"{pname}: FAILED — {sub_err}")

        # Synthesis step — summarise across all persona outputs
        goals_block = build_goals_block(state)
        task_board = _build_task_board_block(tasks, roster)

        synth_prompt = load_prompt(
            "coordinator", "synthesis",
            agent_name=config.agent_name,
            execution_count=state.execution_count,
            selected_personas=", ".join(selected),
            outputs="\n".join(f"  - {o}" for o in outputs),
            note_count=len(brain.notes),
            connection_count=len(brain.connections),
            goals_block=goals_block,
            task_board=task_board,
        )
        if not synth_prompt:
            synth_prompt = (
                f"You are {config.agent_name} coordinating multiple personas.\n"
                f"Heartbeat #{state.execution_count}.\n"
                f"Personas run this cycle: {', '.join(selected)}\n"
                f"Results:\n" + "\n".join(f"  - {o}" for o in outputs) + "\n\n"
                f"{task_board}\n\n"
                f"Brain has {len(brain.notes)} notes, {len(brain.connections)} connections.\n"
                f"{goals_block}\n\n"
                "Synthesise what was accomplished. If you want to delegate work to a "
                "specific persona, return JSON: {\"delegate\": [{\"persona\": \"name\", "
                "\"task\": \"description\", \"files\": [\"path\", ...]}]}\n"
                "Otherwise write a 2-3 sentence synthesis of progress and next focus."
            )
        seen_fps: set[str] = set()
        synth_result = await _run_step(agent, "coordinator_synthesis", synth_prompt, state, seen_fps=seen_fps, _text_sink=_text_sink)

        # Parse delegation instructions from synthesis output
        await _process_coordinator_delegations(synth_result, config, roster)

        brain.last_review = datetime.now(timezone.utc).isoformat()
        brain.review_log.append({
            "timestamp": brain.last_review,
            "summary": synth_result[:500],
        })
    except Exception as e:
        logger.error("Error in coordinator workflow: %s", str(e))
        logger.debug("Workflow error details:", exc_info=True)


async def _process_coordinator_delegations(
    synth_result: str, config: AppConfig, roster: list[str],
) -> None:
    """If the synthesis output contains delegation JSON, create routed tasks."""
    from tasks import create_task, load_tasks, save_tasks

    try:
        delegations = _extract_delegations(synth_result, roster)
    except Exception:
        return
    if not delegations:
        return

    from handoff import build_handoff_from_delegation
    tasks = await load_tasks(config.state_file)
    created = 0
    for d in delegations:
        task = create_task(
            title=d["task"],
            description=d["task"],
            target_persona=d["persona"],
        )
        if d.get("files"):
            task.file_locks = list(d["files"])
        # Move B: attach structured handoff context when coordinator provides it
        hc = build_handoff_from_delegation(d)
        if hc:
            task.handoff_context = hc.to_dict()
        tasks.append(task)
        created += 1
        logger.info(
            "[coordinator] Delegated task %s to @%s: %s",
            task.id, d["persona"], d["task"][:80],
        )
    if created:
        await save_tasks(tasks, config.state_file)


def _extract_delegations(text: str, roster: list[str]) -> list[dict]:
    """Parse delegation JSON from coordinator synthesis output."""
    # Try each '{' as a potential JSON object start
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        # Find matching closing brace via depth counting
        depth = 0
        for j in range(i, len(text)):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
            if depth == 0:
                candidate = text[i : j + 1]
                try:
                    data = json.loads(candidate)
                except (json.JSONDecodeError, TypeError):
                    break
                raw = data.get("delegate")
                if not isinstance(raw, list):
                    break
                valid = []
                for item in raw:
                    if not isinstance(item, dict):
                        continue
                    pname = str(item.get("persona", "")).strip()
                    task_text = str(item.get("task", "")).strip()
                    if pname and task_text and pname in roster:
                        files = item.get("files", [])
                        if not isinstance(files, list):
                            files = []
                        valid.append({
                            "persona": pname,
                            "task": task_text,
                            "files": [str(f) for f in files[:20] if f],
                            # Move B: pass through handoff context fields
                            "context": str(item.get("context", "")),
                            "findings": item.get("findings", []),
                            "brain_note_ids": item.get("brain_note_ids", []),
                            "open_questions": item.get("open_questions", []),
                            "recommendations": item.get("recommendations", []),
                        })
                return valid
    return []


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


def _coerce_confidence(value: object) -> int | None:
    """Normalize confidence to an integer between 0 and 100."""
    try:
        if value in (None, ""):
            return None
        score = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, min(100, score))


def _parse_iso_utc(value: object) -> datetime | None:
    """Parse an ISO timestamp to timezone-aware UTC datetime."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _find_recent_note_by_evidence_overlap(
    brain: BrainState,
    evidence: list[str],
    *,
    persona_name: str,
    window_minutes: int,
) -> str | None:
    """Find a recent note from *persona_name* whose evidence overlaps *evidence*."""
    if not evidence or not persona_name or window_minutes <= 0:
        return None
    now = datetime.now(timezone.utc)
    evidence_set = {item.lower() for item in evidence}
    for note_id, note in reversed(list(brain.notes.items())):
        source = note.get("source")
        if not isinstance(source, dict) or source.get("persona") != persona_name:
            continue
        created_at = _parse_iso_utc(note.get("created_at"))
        if created_at is None:
            continue
        age_min = (now - created_at).total_seconds() / 60.0
        if age_min > window_minutes:
            continue
        existing_evidence = note.get("evidence", [])
        if not isinstance(existing_evidence, list):
            existing_evidence = []
        existing_set = {str(item).lower() for item in existing_evidence}
        if evidence_set & existing_set:
            return note_id
    return None


def _store_capture(
    brain: BrainState,
    raw: str,
    *,
    persona_name: str = "",
    capture_policy: CapturePolicy | None = None,
    heartbeat_number: int = 0,
    step: str = "capture",
) -> str | None:
    """Parse agent JSON output and store as a note. Returns note ID or None.

    If a near-duplicate note already exists, the existing note is updated
    instead of creating a new one.
    """
    cap = capture_policy or DEFAULT_CAPTURE_POLICY
    try:
        data = _extract_json(raw, required_key="content")
        if data is None:
            logger.warning("_store_capture: no JSON object with 'content' key found")
            if cap.reject_if_no_json:
                logger.warning("_store_capture: reject_if_no_json — dropping capture without JSON")
                return None
            return add_note(brain, content=raw[:300], source="heartbeat")
        content = data.get("content", raw[:300])

        tags = data.get("tags", [])
        evidence = data.get("evidence")
        if isinstance(evidence, list):
            evidence_list = [str(item).strip() for item in evidence if str(item).strip()]
        elif evidence in (None, ""):
            evidence_list = []
        else:
            text = str(evidence).strip()
            evidence_list = [text] if text else []

        confidence = _coerce_confidence(data.get("confidence"))
        has_required_evidence = has_substantive_evidence(evidence_list)
        low_quality = not has_required_evidence
        if cap.reject_if_missing_evidence and low_quality:
            logger.warning(
                "_store_capture: reject_if_missing_evidence — dropping capture without substantive evidence"
            )
            return None
        if low_quality and "low_quality" not in tags:
            tags = [*tags, "low_quality"]
        if low_quality and "missing_evidence" not in tags:
            tags = [*tags, "missing_evidence"]
        status = data.get("status", "active")
        if low_quality:
            status = "invalid"
            if confidence is None:
                confidence = 20
        elif confidence is None:
            confidence = 70

        if cap.evidence_burst_merge_window_minutes > 0 and evidence_list and persona_name:
            recent_dup_id = _find_recent_note_by_evidence_overlap(
                brain,
                evidence_list,
                persona_name=persona_name,
                window_minutes=cap.evidence_burst_merge_window_minutes,
            )
            if recent_dup_id:
                existing = brain.notes[recent_dup_id]
                existing["content"] = content
                existing["updated_at"] = datetime.now(timezone.utc).isoformat()
                merged_evidence = existing.get("evidence", [])
                if not isinstance(merged_evidence, list):
                    merged_evidence = []
                for item in evidence_list:
                    if item not in merged_evidence:
                        merged_evidence.append(item)
                existing["evidence"] = merged_evidence
                for tag in tags:
                    if tag not in existing.get("tags", []):
                        existing.setdefault("tags", []).append(tag)
                existing["status"] = status
                existing["confidence"] = confidence
                for tag in tags:
                    assign_note_to_topic(brain, recent_dup_id, tag)
                _relate_cooccurring_tags(brain, tags)
                logger.info(
                    "[capture] merged burst note into %s (evidence overlap)",
                    recent_dup_id,
                )
                run_after_capture_hook(cap.after_capture, brain, recent_dup_id)
                return recent_dup_id

        # Dedup check — merge into existing note if near-duplicate
        dup_id = find_duplicate(brain, content)
        if dup_id:
            existing = brain.notes[dup_id]
            existing["content"] = content
            existing["updated_at"] = datetime.now(timezone.utc).isoformat()
            for tag in tags:
                if tag not in existing.get("tags", []):
                    existing.setdefault("tags", []).append(tag)
            if evidence_list:
                merged_evidence = existing.get("evidence", [])
                if not isinstance(merged_evidence, list):
                    merged_evidence = []
                for item in evidence_list:
                    if item not in merged_evidence:
                        merged_evidence.append(item)
                existing["evidence"] = merged_evidence
            existing["status"] = status or existing.get("status", "active")
            existing["confidence"] = confidence
            # Still assign topics so graph stays current
            for tag in tags:
                assign_note_to_topic(brain, dup_id, tag)
            _relate_cooccurring_tags(brain, tags)
            logger.info("[capture] merged into existing note %s (dedup)", dup_id)
            # Re-embed updated content for semantic search
            try:
                from brain_index import index_note
                index_note(dup_id, brain.notes[dup_id])
            except Exception:
                pass
            run_after_capture_hook(cap.after_capture, brain, dup_id)
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
            status=status,
            confidence=confidence,
            evidence=evidence_list,
        )

        # Wire note into the topic graph
        if note_id:
            for tag in tags:
                assign_note_to_topic(brain, note_id, tag)
            _relate_cooccurring_tags(brain, tags)
            run_after_capture_hook(cap.after_capture, brain, note_id)

        return note_id
    except (json.JSONDecodeError, AttributeError, UnicodeDecodeError) as e:
        logger.warning("_store_capture failed to parse JSON: %s", str(e))
        logger.debug("JSON parsing error details:", exc_info=True)
        if cap.reject_on_parse_failure:
            logger.warning("_store_capture: reject_on_parse_failure — dropping capture on parse failure")
            return None
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
