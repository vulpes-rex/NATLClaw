"""OpenAI-compatible API server for NATLClaw.

Exposes ``/v1/chat/completions`` and ``/v1/models`` so OpenWebUI (or any
OpenAI-compatible client) can talk to NATLClaw's agent framework with
full brain context, learning calibration, and persona support.

Also exposes management endpoints:
  /api/tasks/*       -- task queue CRUD
  /api/brain/*       -- brain search & stats
  /api/personas      -- list / switch personas
  /api/heartbeat/*   -- heartbeat status & log
  /api/scheduler/*   -- start / stop scheduler
  /api/reports/*     -- workspace audit reports

Run::

    python api_server.py                     # defaults to port 8000
    python api_server.py --port 9000         # custom port
    python cli.py serve                      # via CLI subcommand
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
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dataclasses import asdict

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from agent_setup import create_agent
from config import AppConfig, load_config
from execution_log import recent_entries as _recent_log_entries, set_db_path as _set_log_db_path
from goals import build_goals_block
from learning import build_context_block
from metrics import MetricsStore
from persona_loader import list_personas, load_persona
from event_watcher import is_watcher_running, start_background_watcher, stop_background_watcher
from messaging import load_outbox, save_outbox
from second_brain import (
    add_note,
    apply_relevance_feedback,
    build_brain_stats_from_store,
    build_brain_summary,
    describe_note_from_store,
    get_topic_map_from_store,
    lint_brain,
    load_brain,
    record_contradiction,
    run_dream_cycle,
    save_brain,
    search_notes_from_store,
    trace_topic_from_store,
)
from state import AgentState, load_state, save_state
from tasks import (
    Task,
    TaskTransitionError,
    answer_task,
    assign_task,
    auto_timeout_tasks,
    block_task,
    cancel_task,
    complete_task,
    create_task,
    fail_task,
    find_task,
    format_task_detail,
    format_task_list,
    get_active_task,
    get_blocked_tasks,
    get_pending_tasks,
    load_tasks,
    retry_task,
    save_tasks,
    start_task,
)
from operator_status import build_operator_status
from scheduler_control import load_scheduler_control, update_scheduler_control
from telemetry import init_sentry
from surface_ingress import (
    SurfaceAdapterNotAllowedError,
    SurfaceIdempotencyConflictError,
    SurfaceIngressDisabledError,
    SurfaceIngressError,
    get_surface_health,
    get_surface_session,
    list_recent_surface_routes,
    list_surface_sessions,
    process_surface_event,
    validate_surface_event,
)

logger = logging.getLogger(__name__)

# ── Pydantic models (OpenAI-compatible) ──────────────────────────────

class ChatMessage(BaseModel):
    role: str = "user"
    content: str = ""

class ChatCompletionRequest(BaseModel):
    model: str = "default"
    messages: list[ChatMessage] = Field(default_factory=list)
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None

class TaskCreateRequest(BaseModel):
    title: str
    description: str = ""
    priority: str = "medium"
    max_heartbeats: int = 10

class TaskAnswerRequest(BaseModel):
    answer: str

class TaskCancelRequest(BaseModel):
    reason: str = ""

class BrainSearchRequest(BaseModel):
    query: str
    max_results: int = 10
    semantic: bool = True

class BrainAddRequest(BaseModel):
    content: str
    summary: str = ""
    source: str = "api"
    note_type: str = "general"
    status: str = "active"
    confidence: int | None = None
    evidence: list[str] | None = None
    tags: list[str] = Field(default_factory=list)
    category: str = "resources"

class BrainFeedbackRequest(BaseModel):
    relevant: bool
    reason: str = ""

class BrainContradictionRequest(BaseModel):
    contradicting_note_id: str
    reason: str = ""
    supersede: bool | None = None


class BrainDreamRunRequest(BaseModel):
    apply: bool = False
    heartbeat: int | None = Field(default=None, ge=1)
    max_age_days: int | None = Field(default=None, ge=1)


class SchedulerControlRequest(BaseModel):
    reason: str = ""


# ── App factory ──────────────────────────────────────────────────────

_default_config: AppConfig | None = None

def create_app(config: AppConfig | None = None) -> FastAPI:
    """Build the FastAPI application wired to NATLClaw."""
    from contextlib import asynccontextmanager

    global _default_config
    if config is None:
        config = _default_config or load_config()
    _default_config = config

    # Telemetry initialization is optional and no-ops without SENTRY_DSN.
    init_sentry(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # ── Build semantic search index on startup ────────────────
        try:
            from brain_index import rebuild_index, _HAS_VECTOR_DEPS
            if not _HAS_VECTOR_DEPS:
                logger.info("Semantic search unavailable (missing sentence-transformers or faiss-cpu)")
            else:
                brain = await load_brain(config.state_file)
                loop = asyncio.get_event_loop()
                count = await loop.run_in_executor(None, rebuild_index, brain.notes)
                logger.info("Brain vector index built: %d notes indexed", count)
        except Exception:
            logger.warning("Failed to build brain vector index on startup", exc_info=True)

        # ── Auto-start scheduler as background asyncio task ──────
        from scheduler import run_scheduler
        scheduler_task = asyncio.create_task(
            run_scheduler(config), name="natl-scheduler",
        )
        app.state.scheduler_task = scheduler_task
        logger.info("Scheduler auto-started as background task")

        yield

        # ── Shutdown: cancel scheduler ───────────────────────────
        if not scheduler_task.done():
            scheduler_task.cancel()
            try:
                await scheduler_task
            except (asyncio.CancelledError, Exception):
                pass
        logger.info("Scheduler stopped")

    app = FastAPI(
        title="NATLClaw API",
        description="OpenAI-compatible API for the NATLClaw agent framework",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── API key authentication (optional) ─────────────────────────
    if config.api_key:
        @app.middleware("http")
        async def _auth_middleware(request: Request, call_next):
            # Dashboard and health are public
            path = request.url.path
            if path in ("/", "/api/health") or path.startswith("/static"):
                return await call_next(request)
            # Check Bearer token
            auth = request.headers.get("authorization", "")
            if not auth.startswith("Bearer ") or auth[7:] != config.api_key:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or missing API key"},
                )
            return await call_next(request)

    # Share state across requests via app.state
    app.state.config = config
    app.state.sessions: dict[str, Any] = {}  # model -> AgentSession
    app.state.scheduler_task: asyncio.Task | None = None
    app.state.report_proc: subprocess.Popen | None = None

    # Initialise execution log DB path
    _set_log_db_path(os.path.join(os.path.dirname(config.state_file), "execution_log.db"))

    # Metrics store (read-only from the API -- the scheduler writes)
    metrics_db = os.path.join(
        os.path.dirname(os.path.abspath(config.state_file)), "metrics.db",
    )

    STALE_THRESHOLD_SEC = 300  # 5 minutes



    # ── Helpers ───────────────────────────────────────────────────

    async def _build_agent_async(persona_name: str):
        """Async version -- build agent with full context."""
        persona = load_persona(persona_name)
        state = await load_state(config.state_file)
        brain = await load_brain(config.state_file)

        base_instructions = config.agent_instructions or persona.instructions
        context_block = build_context_block(state)
        brain_block = build_brain_summary(brain, max_notes=5)
        goals_block = build_goals_block(state)

        schema_blocks = ""
        if persona.heartbeat_schema:
            schema_blocks += f"\n\n== HEARTBEAT STRATEGY ==\n{persona.heartbeat_schema}"
        if persona.brain_schema:
            schema_blocks += f"\n\n== KNOWLEDGE SCHEMA ==\n{persona.brain_schema}"

        enriched = (
            f"{base_instructions}{schema_blocks}\n\n{context_block}\n\n{brain_block}"
            + (f"\n\n{goals_block}" if goals_block else "")
        )

        agent = create_agent(
            config, enriched,
            tools=persona.tools,
            mcp_servers=persona.mcp_servers,
        )
        return agent, persona, state, brain

    def _make_completion_response(
        text: str, model: str, completion_id: str | None = None,
    ) -> dict:
        """Build an OpenAI-compatible chat completion response."""
        return {
            "id": completion_id or f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }

    def _make_chunk(
        text: str, model: str, completion_id: str, finish: bool = False,
    ) -> str:
        """Build an SSE chunk for streaming."""
        chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {} if finish else {"content": text},
                "finish_reason": "stop" if finish else None,
            }],
        }
        return f"data: {json.dumps(chunk)}\n\n"

    # ── OpenAI-compatible endpoints ──────────────────────────────

    @app.get("/v1/models")
    async def list_models():
        """List available personas as models."""
        names = list_personas()
        models = []
        for name in sorted(names):
            try:
                p = load_persona(name)
                models.append({
                    "id": name,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "natlclaw",
                    "permission": [],
                    "root": name,
                    "parent": None,
                    "description": p.description or "",
                    "workflow": p.workflow,
                })
            except Exception:
                pass
        return {"object": "list", "data": models}

    @app.post("/v1/chat/completions")
    async def chat_completions(req: ChatCompletionRequest):
        """OpenAI-compatible chat completion endpoint."""
        persona_name = req.model if req.model in list_personas() else config.persona

        user_messages = [m for m in req.messages if m.role == "user"]
        if not user_messages:
            raise HTTPException(400, "No user message in request")
        prompt = user_messages[-1].content

        agent, persona, state, brain = await _build_agent_async(persona_name)
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

        from agent_framework import AgentSession
        session_key = f"{persona_name}-api"
        if session_key not in app.state.sessions:
            app.state.sessions[session_key] = AgentSession()
        session = app.state.sessions[session_key]

        if req.stream:
            async def _stream():
                try:
                    response = await agent.run(prompt, session=session)
                    text = response.text if hasattr(response, "text") else str(response)
                    chunk_size = 20
                    for i in range(0, len(text), chunk_size):
                        yield _make_chunk(text[i:i+chunk_size], persona_name, completion_id)
                        await asyncio.sleep(0.02)
                    yield _make_chunk("", persona_name, completion_id, finish=True)
                    yield "data: [DONE]\n\n"
                except Exception as e:
                    yield _make_chunk(f"Error: {str(e)}", persona_name, completion_id)
                    yield _make_chunk("", persona_name, completion_id, finish=True)
                    yield "data: [DONE]\n\n"
                finally:
                    state.execution_count += 1
                    state.last_heartbeat = datetime.now(timezone.utc).isoformat()
                    await save_state(state, config.state_file, config.max_history)
            return EventSourceResponse(_stream(), media_type="text/event-stream")

        try:
            response = await agent.run(prompt, session=session)
            text = response.text if hasattr(response, "text") else str(response)
        except Exception as e:
            logger.error("Agent error: %s", e)
            raise HTTPException(500, f"Agent error: {str(e)}")
        finally:
            state.execution_count += 1
            state.last_heartbeat = datetime.now(timezone.utc).isoformat()
            await save_state(state, config.state_file, config.max_history)

        return JSONResponse(_make_completion_response(text, persona_name, completion_id))

    @app.post("/api/surface/events")
    async def api_surface_ingress(event: dict[str, Any]):
        """Ingress bridge for normalized surface events (S18 MVP)."""
        try:
            normalized = validate_surface_event(event)
        except SurfaceIngressError as exc:
            raise HTTPException(400, str(exc)) from exc

        try:
            result = await process_surface_event(
                normalized,
                state_file=config.state_file,
                ingress_enabled=bool(config.surface_ingress_enabled),
                allowed_channels={c.strip() for c in config.surface_channels_enabled if c.strip()},
                default_persona=config.persona,
                allowed_personas=set(list_personas()),
            )
        except SurfaceIngressDisabledError as exc:
            raise HTTPException(503, str(exc)) from exc
        except SurfaceAdapterNotAllowedError as exc:
            raise HTTPException(400, str(exc)) from exc
        except SurfaceIdempotencyConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        except SurfaceIngressError as exc:
            raise HTTPException(400, str(exc)) from exc

        return JSONResponse(status_code=202, content=result)

    @app.get("/api/surface/sessions")
    async def api_surface_sessions():
        """List known surface sessions for operator observability."""
        return list_surface_sessions(config.state_file)

    @app.get("/api/surface/sessions/{session_id}")
    async def api_surface_session_detail(session_id: str):
        """Get one surface session state."""
        session = get_surface_session(config.state_file, session_id)
        if session is None:
            raise HTTPException(404, f"Surface session '{session_id}' not found")
        return session

    @app.get("/api/surface/routes/recent")
    async def api_surface_routes_recent(
        limit: int = Query(50, ge=1, le=500),
        session_id: str | None = Query(None),
        event_id: str | None = Query(None),
    ):
        """List recent route decisions for event->outcome tracing."""
        return list_recent_surface_routes(
            config.state_file,
            limit=limit,
            session_id=session_id,
            event_id=event_id,
        )

    @app.get("/api/surface/health")
    async def api_surface_health():
        """Return rollout-oriented surface health and canary status details."""
        allowed_channels = {c.strip() for c in config.surface_channels_enabled if c.strip()}
        return get_surface_health(
            config.state_file,
            ingress_enabled=bool(config.surface_ingress_enabled),
            allowed_channels=allowed_channels,
        )

    # ── Task management endpoints ────────────────────────────────

    @app.get("/api/tasks")
    async def api_list_tasks(status: str = "all"):
        tasks = await load_tasks(config.state_file)
        filtered = tasks if status == "all" else [t for t in tasks if t.status == status]
        return [{
            "id": t.id, "title": t.title, "description": t.description,
            "priority": t.priority, "status": t.status,
            "assigned_to": t.assigned_to, "created_at": t.created_at,
            "started_at": t.started_at, "completed_at": t.completed_at,
            "heartbeats_spent": t.heartbeats_spent,
            "max_heartbeats": t.max_heartbeats,
            "progress_notes": t.progress_notes, "deliverables": t.deliverables,
            "questions": t.questions, "answers": t.answers,
        } for t in filtered]

    @app.post("/api/tasks")
    async def api_create_task(req: TaskCreateRequest):
        tasks = await load_tasks(config.state_file)
        task = create_task(
            title=req.title, description=req.description or req.title,
            priority=req.priority, max_heartbeats=req.max_heartbeats,
        )
        tasks.append(task)
        await save_tasks(tasks, config.state_file)
        return {"id": task.id, "title": task.title, "priority": task.priority, "status": task.status}

    @app.get("/api/tasks/{task_id}")
    async def api_get_task(task_id: str):
        tasks = await load_tasks(config.state_file)
        task = find_task(tasks, task_id)
        if task is None:
            raise HTTPException(404, f"Task '{task_id}' not found")
        return {
            "id": task.id, "title": task.title, "description": task.description,
            "priority": task.priority, "status": task.status,
            "assigned_to": task.assigned_to, "created_at": task.created_at,
            "started_at": task.started_at, "completed_at": task.completed_at,
            "heartbeats_spent": task.heartbeats_spent,
            "max_heartbeats": task.max_heartbeats,
            "progress_notes": task.progress_notes, "deliverables": task.deliverables,
            "questions": task.questions, "answers": task.answers,
        }

    @app.post("/api/tasks/{task_id}/answer")
    async def api_answer_task(task_id: str, req: TaskAnswerRequest):
        tasks = await load_tasks(config.state_file)
        task = find_task(tasks, task_id)
        if task is None:
            raise HTTPException(404, f"Task '{task_id}' not found")
        if (
            task.status in ("assigned", "in_progress")
            and task.answers
            and task.answers[-1].get("answer", "") == req.answer
        ):
            return {"id": task.id, "status": task.status, "idempotent": True}
        try:
            answer_task(task, req.answer)
        except TaskTransitionError as exc:
            raise HTTPException(409, str(exc)) from exc
        await save_tasks(tasks, config.state_file)
        return {"id": task.id, "status": task.status, "idempotent": False}

    @app.post("/api/tasks/{task_id}/cancel")
    async def api_cancel_task(task_id: str, req: TaskCancelRequest | None = None):
        tasks = await load_tasks(config.state_file)
        task = find_task(tasks, task_id)
        if task is None:
            raise HTTPException(404, f"Task '{task_id}' not found")
        if task.status == "failed" and any(
            note.startswith("CANCELLED") for note in task.progress_notes
        ):
            return {"id": task.id, "status": task.status, "idempotent": True}
        try:
            cancel_task(task, reason=req.reason if req else "")
        except TaskTransitionError as exc:
            raise HTTPException(409, str(exc)) from exc
        await save_tasks(tasks, config.state_file)
        return {"id": task.id, "status": task.status, "idempotent": False}

    @app.post("/api/tasks/{task_id}/retry")
    async def api_retry_task(task_id: str):
        tasks = await load_tasks(config.state_file)
        task = find_task(tasks, task_id)
        if task is None:
            raise HTTPException(404, f"Task '{task_id}' not found")
        if task.status == "pending" and any(
            note == "RETRIED by developer" for note in task.progress_notes
        ):
            return {"id": task.id, "status": task.status, "idempotent": True}
        try:
            retry_task(task)
        except TaskTransitionError as exc:
            raise HTTPException(409, str(exc)) from exc
        await save_tasks(tasks, config.state_file)
        return {"id": task.id, "status": task.status, "idempotent": False}

    # ── Brain endpoints ──────────────────────────────────────────

    @app.get("/api/brain/stats")
    async def api_brain_stats():
        stats = build_brain_stats_from_store(config.state_file)
        try:
            from brain_index import get_brain_index
            idx = get_brain_index()
            stats["semantic_index"] = {
                "available": idx is not None and idx._index is not None,
                "indexed_notes": (
                    len(idx._note_ids) - len(idx._removed)
                    if idx and idx._index else 0
                ),
            }
        except Exception:
            stats["semantic_index"] = {"available": False, "indexed_notes": 0}
        return stats

    @app.post("/api/brain/search")
    async def api_brain_search(req: BrainSearchRequest):
        results = search_notes_from_store(
            config.state_file, req.query,
            max_results=req.max_results, record_access=True,
            semantic=req.semantic,
        )
        return {"query": req.query, "results": results, "semantic": req.semantic}

    @app.post("/api/brain/add")
    async def api_brain_add(req: BrainAddRequest):
        brain = await load_brain(config.state_file)
        nid = add_note(
            brain, content=req.content,
            summary=req.summary or req.content[:80],
            source=req.source,
            note_type=req.note_type,
            status=req.status,
            confidence=req.confidence,
            evidence=req.evidence,
            tags=req.tags, category=req.category,
        )
        await save_brain(brain, config.state_file)
        return {"note_id": nid, "content": req.content[:100]}

    @app.get("/api/brain/topics")
    async def api_brain_topics():
        return get_topic_map_from_store(config.state_file)

    @app.get("/api/brain/topics/{topic_name}")
    async def api_brain_trace_topic(
        topic_name: str,
        depth: int = Query(1, ge=1, le=5),
        limit: int = Query(10, ge=1, le=100),
    ):
        result = trace_topic_from_store(
            config.state_file, topic_name,
            depth=depth, limit=limit, include_connected=True, record_access=True,
        )
        if result is None:
            raise HTTPException(404, f"Topic '{topic_name}' not found")
        return result

    @app.get("/api/brain/notes/{note_id}")
    async def api_brain_describe_note(note_id: str):
        result = describe_note_from_store(config.state_file, note_id, record_access=True)
        if result is None:
            raise HTTPException(404, f"Note '{note_id}' not found")
        return result

    @app.post("/api/brain/notes/{note_id}/feedback")
    async def api_brain_feedback(note_id: str, body: BrainFeedbackRequest):
        brain = await load_brain(config.state_file)
        ok = apply_relevance_feedback(brain, note_id, relevant=body.relevant, reason=body.reason)
        if not ok:
            raise HTTPException(404, f"Note '{note_id}' not found")
        await save_brain(brain, config.state_file)
        return {"note_id": note_id, "relevant": body.relevant}

    @app.post("/api/brain/notes/{note_id}/contradict")
    async def api_brain_contradict(note_id: str, body: BrainContradictionRequest):
        brain = await load_brain(config.state_file)
        ok = record_contradiction(
            brain, note_id, body.contradicting_note_id,
            reason=body.reason, supersede=body.supersede,
        )
        if not ok:
            raise HTTPException(404, f"Note '{note_id}' or contradicting note not found")
        await save_brain(brain, config.state_file)
        return {"note_id": note_id, "contradicting_note_id": body.contradicting_note_id}

    @app.post("/api/brain/lint")
    async def api_brain_lint():
        brain = await load_brain(config.state_file)
        return lint_brain(brain)

    @app.post("/api/brain/reindex")
    async def api_brain_reindex():
        """Rebuild the semantic search index from current brain notes."""
        try:
            from brain_index import rebuild_index, get_brain_index
        except ImportError:
            return {"status": "unavailable", "reason": "brain_index module not found", "count": 0}
        idx = get_brain_index()
        if idx is None:
            return {"status": "unavailable", "reason": "sentence-transformers or faiss-cpu not installed", "count": 0}
        brain = await load_brain(config.state_file)
        loop = asyncio.get_event_loop()
        count = await loop.run_in_executor(None, rebuild_index, brain.notes)
        return {"status": "ok", "indexed": count}

    @app.get("/api/brain/dream/policy")
    async def api_brain_dream_policy():
        persona = load_persona(config.persona)
        return {
            "persona": persona.name,
            "dream": {
                "enabled": bool(getattr(persona, "dream_enabled", True)),
                "idle_streak_min": int(getattr(persona, "dream_idle_streak_min", 3)),
                "max_age_days": int(getattr(persona, "dream_max_age_days", 30)),
            },
        }

    @app.post("/api/brain/dream/run")
    async def api_brain_dream_run(req: BrainDreamRunRequest):
        persona = load_persona(config.persona)
        brain = await load_brain(config.state_file)
        report = run_dream_cycle(
            brain,
            heartbeat_number=req.heartbeat,
            apply=req.apply,
            max_age_days=req.max_age_days or int(getattr(persona, "dream_max_age_days", 30)),
            trigger="api_apply" if req.apply else "api_dry_run",
        )
        if req.apply:
            await save_brain(brain, config.state_file)
        return report

    # ── Inbox (outbox messages) ─────────────────────────────────

    @app.get("/api/inbox")
    async def api_list_inbox(
        status: str = Query("all", description="Filter: all, unread, read, dismissed"),
        msg_type: str = Query("all", alias="type", description="Filter by message type"),
    ):
        messages = await load_outbox(config.state_file)
        if status != "all":
            messages = [m for m in messages if m.status == status]
        if msg_type != "all":
            messages = [m for m in messages if m.type == msg_type]
        return [asdict(m) for m in messages]

    @app.get("/api/inbox/{message_id}")
    async def api_get_inbox_message(message_id: str):
        messages = await load_outbox(config.state_file)
        for m in messages:
            if m.id == message_id:
                return asdict(m)
        raise HTTPException(404, f"Message '{message_id}' not found")

    @app.post("/api/inbox/{message_id}/dismiss")
    async def api_dismiss_message(message_id: str):
        messages = await load_outbox(config.state_file)
        for m in messages:
            if m.id == message_id:
                m.status = "dismissed"
                m.dismissed_at = datetime.now(timezone.utc).isoformat()
                await save_outbox(messages, config.state_file)
                return asdict(m)
        raise HTTPException(404, f"Message '{message_id}' not found")

    @app.post("/api/inbox/clear")
    async def api_clear_inbox():
        messages = await load_outbox(config.state_file)
        now = datetime.now(timezone.utc).isoformat()
        count = 0
        for m in messages:
            if m.status != "dismissed":
                m.status = "dismissed"
                m.dismissed_at = now
                count += 1
        await save_outbox(messages, config.state_file)
        return {"dismissed": count}

    # ── Watcher control ─────────────────────────────────────────

    @app.get("/api/watch/status")
    async def api_watch_status():
        return {"running": is_watcher_running()}

    @app.post("/api/watch/start")
    async def api_watch_start():
        if is_watcher_running():
            return {"status": "already_running"}
        start_background_watcher()
        return {"status": "started"}

    @app.post("/api/watch/stop")
    async def api_watch_stop():
        if not is_watcher_running():
            return {"status": "not_running"}
        stop_background_watcher()
        return {"status": "stopped"}

    # ── Config (sanitised) ──────────────────────────────────────

    _SECRET_FIELDS = frozenset({
        "openai_api_key", "github_pat", "openrouter_api_key", "azure_openai_api_key", "api_key",
    })

    @app.get("/api/config")
    async def api_get_config():
        data = asdict(config)
        for key in _SECRET_FIELDS:
            if key in data and data[key]:
                data[key] = "***"
        return data

    # ── Heartbeat trigger ───────────────────────────────────────

    @app.post("/api/heartbeat/trigger")
    async def api_trigger_heartbeat():
        """Run a single scheduler heartbeat and return."""
        from scheduler import run_scheduler
        try:
            await run_scheduler(config, max_iterations=1)
        except RuntimeError as exc:
            if "already running" in str(exc).lower():
                raise HTTPException(409, "Scheduler is already running") from exc
            raise HTTPException(500, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(500, str(exc)) from exc
        return {"status": "completed", "iterations": 1}

    # ── Persona endpoints ────────────────────────────────────────

    @app.get("/api/personas")
    async def api_list_personas():
        names = list_personas()
        result = []
        for name in sorted(names):
            try:
                p = load_persona(name)
                result.append({
                    "name": name, "description": p.description or "",
                    "workflow": p.workflow, "tools_count": len(p.tools),
                    "active": name == config.persona,
                })
            except Exception:
                pass
        return result

    # ── Health / info ────────────────────────────────────────────

    @app.get("/api/health")
    async def api_health():
        state = await load_state(config.state_file)
        return {
            "status": "ok",
            "agent_name": config.agent_name,
            "persona": config.persona,
            "provider": config.provider,
            "model": config.model,
            "heartbeat_count": state.execution_count,
            "last_heartbeat": state.last_heartbeat,
        }

    @app.get("/api/status")
    async def api_status():
        task = getattr(app.state, "scheduler_task", None)
        return await build_operator_status(config, scheduler_task=task)

    # ── Heartbeat status & log ───────────────────────────────────

    @app.get("/api/heartbeat/status")
    async def api_heartbeat_status():
        """Check heartbeat freshness and scheduler state for the dashboard."""
        task = getattr(app.state, "scheduler_task", None)
        snap = await build_operator_status(config, scheduler_task=task)
        hb = snap.get("heartbeat", {})
        sched = snap.get("scheduler", {})
        return {
            "status": hb.get("status", "unknown"),
            "last_heartbeat": hb.get("last"),
            "seconds_ago": hb.get("seconds_ago"),
            "heartbeat_count": hb.get("count", 0),
            "scheduler_running": bool(sched.get("running", False)),
            "stale_threshold_sec": STALE_THRESHOLD_SEC,
            # Extra scheduler fields for richer web client rendering.
            "scheduler": {
                "in_process_task_running": bool(sched.get("in_process_task_running", False)),
                "control": sched.get("control", {}),
                "backpressure": sched.get("backpressure", {}),
            },
        }

    @app.get("/api/heartbeat/log")
    async def api_heartbeat_log(limit: int = 20):
        """Return recent heartbeat metrics from the metrics DB."""
        try:
            store = MetricsStore(metrics_db)
            rows = store.recent(limit)
            summary = store.summary()
            store.close()
            rows.reverse()
            return {"entries": rows, "summary": summary}
        except Exception as e:
            return {"entries": [], "summary": {}, "error": str(e)}

    @app.get("/api/heartbeat/activity")
    async def api_heartbeat_activity(limit: int = 10):
        """Return recent execution log entries."""
        db_path = os.path.join(os.path.dirname(config.state_file), "execution_log.db")
        entries = _recent_log_entries(limit, db_path=db_path)
        entries = entries[-limit:]
        return [{
            "timestamp": e["timestamp"], "step": e["step"],
            "prompt": e["prompt"][:200], "response": e["response"][:300],
        } for e in entries]

    # ── Scheduler control ────────────────────────────────────────

    @app.post("/api/scheduler/start")
    async def api_scheduler_start():
        task = getattr(app.state, "scheduler_task", None)
        if task is not None and not task.done():
            return {"status": "already_running"}
        from scheduler import run_scheduler
        task = asyncio.create_task(
            run_scheduler(config), name="natl-scheduler",
        )
        app.state.scheduler_task = task
        return {"status": "started"}

    @app.post("/api/scheduler/stop")
    async def api_scheduler_stop():
        task = getattr(app.state, "scheduler_task", None)
        if task is None or task.done():
            app.state.scheduler_task = None
            return {"status": "not_running"}
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        app.state.scheduler_task = None
        return {"status": "stopped"}

    @app.get("/api/scheduler/status")
    async def api_scheduler_status():
        from scheduler import get_scheduler_lock_info
        lock_info = get_scheduler_lock_info(config.state_file)
        control = await load_scheduler_control(config.state_file)
        task = getattr(app.state, "scheduler_task", None)
        if task is None:
            return {"running": False, "lock": lock_info, "control": asdict(control)}
        if not task.done():
            return {"running": True, "lock": lock_info, "control": asdict(control)}
        # Task finished (error or completed) — clean up
        app.state.scheduler_task = None
        exc = task.exception() if not task.cancelled() else None
        return {
            "running": False,
            "lock": lock_info,
            "control": asdict(control),
            "error": str(exc) if exc else None,
        }

    @app.post("/api/scheduler/pause")
    async def api_scheduler_pause(req: SchedulerControlRequest):
        control = await update_scheduler_control(
            config.state_file,
            paused=True,
            reason=req.reason or "paused via api",
        )
        return {"status": "paused", "control": asdict(control)}

    @app.post("/api/scheduler/resume")
    async def api_scheduler_resume(req: SchedulerControlRequest):
        control = await update_scheduler_control(
            config.state_file,
            paused=False,
            maintenance_mode=False,
            reason=req.reason or "resumed via api",
        )
        return {"status": "resumed", "control": asdict(control)}

    @app.post("/api/scheduler/drain")
    async def api_scheduler_drain(req: SchedulerControlRequest):
        control = await update_scheduler_control(
            config.state_file,
            drain_requested=True,
            reason=req.reason or "drain requested via api",
        )
        return {"status": "drain_requested", "control": asdict(control)}

    @app.post("/api/scheduler/maintenance/enable")
    async def api_scheduler_maintenance_enable(req: SchedulerControlRequest):
        control = await update_scheduler_control(
            config.state_file,
            maintenance_mode=True,
            paused=True,
            reason=req.reason or "maintenance mode enabled via api",
        )
        return {"status": "maintenance_enabled", "control": asdict(control)}

    @app.post("/api/scheduler/maintenance/disable")
    async def api_scheduler_maintenance_disable(req: SchedulerControlRequest):
        control = await update_scheduler_control(
            config.state_file,
            maintenance_mode=False,
            paused=False,
            reason=req.reason or "maintenance mode disabled via api",
        )
        return {"status": "maintenance_disabled", "control": asdict(control)}

    # ── Report endpoints ─────────────────────────────────────────

    @app.get("/api/reports")
    async def api_list_reports():
        """List saved audit reports."""
        reports_dir = Path("data") / "reports"
        if not reports_dir.is_dir():
            return []
        reports = []
        for f in sorted(reports_dir.glob("*.md"), reverse=True):
            stat = f.stat()
            reports.append({
                "filename": f.name,
                "size": stat.st_size,
                "created": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })
        return reports

    @app.get("/api/reports/{filename}")
    async def api_get_report(filename: str):
        """Read a saved audit report."""
        # Safety: prevent path traversal
        if "/" in filename or "\\" in filename or ".." in filename:
            raise HTTPException(400, "Invalid filename")
        path = Path("data") / "reports" / filename
        if not path.is_file():
            raise HTTPException(404, "Report not found")
        content = path.read_text(encoding="utf-8")
        return {"filename": filename, "content": content}

    @app.post("/api/reports/generate")
    async def api_generate_report():
        """Trigger a workspace audit report in the background."""
        proc = app.state.report_proc
        if proc is not None and proc.poll() is None:
            return {"status": "already_running", "pid": proc.pid}
        cmd = [sys.executable, "-m", "cli", "report", "--save"]
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                cwd=os.path.dirname(os.path.abspath(__file__)),
            )
            app.state.report_proc = proc
            return {"status": "started", "pid": proc.pid}
        except Exception as e:
            raise HTTPException(500, f"Failed to start report: {e}")

    # ── Dashboard ────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def dashboard():
        """Serve the embedded dashboard."""
        return _DASHBOARD_HTML

    return app


# ── Embedded dashboard HTML ──────────────────────────────────────────

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NATLClaw Dashboard</title>
<style>
  :root {
    --bg: #0d1117; --surface: #161b22; --border: #30363d;
    --text: #e6edf3; --text-dim: #8b949e; --accent: #58a6ff;
    --green: #3fb950; --yellow: #d29922; --red: #f85149;
    --purple: #bc8cff;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    background: var(--bg); color: var(--text); line-height: 1.5;
  }
  .app { max-width: 1400px; margin: 0 auto; padding: 16px; }
  header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 16px 0; border-bottom: 1px solid var(--border); margin-bottom: 24px;
  }
  header h1 { font-size: 20px; font-weight: 600; }
  header h1 span { color: var(--accent); }
  .status-badge {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 4px 12px; border-radius: 20px; font-size: 12px;
    background: rgba(63, 185, 80, 0.15); color: var(--green); border: 1px solid rgba(63, 185, 80, 0.3);
  }
  .status-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green); }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }
  .grid-full { grid-column: 1 / -1; }
  @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }
  .card h2 { font-size: 14px; font-weight: 600; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 12px; }
  .card h2 .count { color: var(--accent); margin-left: 6px; }
  .stats { display: flex; gap: 24px; flex-wrap: wrap; }
  .stat { text-align: center; }
  .stat-value { font-size: 28px; font-weight: 700; color: var(--accent); }
  .stat-label { font-size: 12px; color: var(--text-dim); }
  .task-item { display: flex; align-items: center; gap: 12px; padding: 10px 12px; border-radius: 6px; margin-bottom: 6px; background: var(--bg); border: 1px solid var(--border); }
  .task-item:hover { border-color: var(--accent); }
  .task-icon { font-size: 16px; flex-shrink: 0; }
  .task-body { flex: 1; min-width: 0; }
  .task-title { font-size: 14px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .task-meta { font-size: 11px; color: var(--text-dim); }
  .task-badge { padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 500; white-space: nowrap; }
  .badge-pending { background: rgba(139, 148, 158, 0.15); color: var(--text-dim); }
  .badge-in_progress { background: rgba(88, 166, 255, 0.15); color: var(--accent); }
  .badge-blocked { background: rgba(210, 153, 34, 0.15); color: var(--yellow); }
  .badge-completed { background: rgba(63, 185, 80, 0.15); color: var(--green); }
  .badge-failed { background: rgba(248, 81, 73, 0.15); color: var(--red); }
  .badge-assigned { background: rgba(188, 140, 255, 0.15); color: var(--purple); }
  .priority-urgent { border-left: 3px solid var(--red); }
  .priority-high { border-left: 3px solid var(--yellow); }
  .priority-medium { border-left: 3px solid var(--accent); }
  .priority-low { border-left: 3px solid var(--text-dim); }
  .note-item { padding: 8px 12px; border-radius: 6px; margin-bottom: 4px; background: var(--bg); border: 1px solid var(--border); font-size: 13px; }
  .note-tags { font-size: 11px; color: var(--purple); }
  .chat-container { display: flex; flex-direction: column; height: 400px; }
  .chat-messages { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 8px; }
  .chat-msg { padding: 8px 12px; border-radius: 8px; max-width: 85%; font-size: 14px; word-wrap: break-word; white-space: pre-wrap; }
  .chat-msg.user { align-self: flex-end; background: var(--accent); color: #000; border-bottom-right-radius: 2px; }
  .chat-msg.assistant { align-self: flex-start; background: var(--surface); border: 1px solid var(--border); border-bottom-left-radius: 2px; }
  .chat-input-row { display: flex; gap: 8px; padding-top: 8px; }
  .chat-input-row input { flex: 1; padding: 10px 14px; border-radius: 8px; border: 1px solid var(--border); background: var(--bg); color: var(--text); font-size: 14px; outline: none; }
  .chat-input-row input:focus { border-color: var(--accent); }
  .chat-input-row button { padding: 10px 20px; border-radius: 8px; border: none; background: var(--accent); color: #000; font-weight: 600; cursor: pointer; font-size: 14px; }
  .chat-input-row button:disabled { opacity: 0.5; cursor: not-allowed; }
  .persona-select { padding: 6px 10px; border-radius: 6px; border: 1px solid var(--border); background: var(--surface); color: var(--text); font-size: 13px; }
  .form-row { display: flex; gap: 8px; margin-bottom: 8px; }
  .form-row input, .form-row select { padding: 8px 12px; border-radius: 6px; border: 1px solid var(--border); background: var(--bg); color: var(--text); font-size: 13px; }
  .form-row input { flex: 1; }
  .btn-sm { padding: 6px 14px; border-radius: 6px; border: none; cursor: pointer; font-size: 13px; font-weight: 500; }
  .btn-primary { background: var(--accent); color: #000; }
  .btn-danger { background: var(--red); color: #fff; }
  .btn-ghost { background: transparent; border: 1px solid var(--border); color: var(--text-dim); }
  .btn-ghost:hover { border-color: var(--accent); color: var(--text); }
  .answer-form { display: flex; gap: 6px; margin-top: 6px; }
  .answer-form input { flex: 1; padding: 6px 10px; border-radius: 4px; border: 1px solid var(--border); background: var(--bg); color: var(--text); font-size: 12px; }
  .search-row { display: flex; gap: 8px; margin-bottom: 12px; }
  .search-row input { flex: 1; padding: 8px 12px; border-radius: 6px; border: 1px solid var(--border); background: var(--bg); color: var(--text); font-size: 13px; }
  .hb-table { width: 100%; border-collapse: collapse; font-size: 12px; }
  .hb-table th { text-align: left; padding: 6px 8px; color: var(--text-dim); border-bottom: 1px solid var(--border); font-weight: 500; }
  .hb-table td { padding: 5px 8px; border-bottom: 1px solid rgba(48,54,61,0.5); }
  .hb-table tr:hover td { background: rgba(88,166,255,0.05); }
  .scheduler-bar { display: flex; align-items: center; gap: 12px; padding: 10px 14px; border-radius: 6px; margin-bottom: 12px; background: var(--bg); border: 1px solid var(--border); }
  .scheduler-status { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 500; }
  .sched-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
  .sched-dot.running { background: var(--green); box-shadow: 0 0 6px rgba(63,185,80,0.5); }
  .sched-dot.stopped { background: var(--red); }
  .sched-dot.stale { background: var(--yellow); }
  .activity-item { padding: 6px 10px; border-radius: 4px; margin-bottom: 3px; background: var(--bg); border: 1px solid var(--border); font-size: 12px; }
  .activity-step { display: inline-block; padding: 1px 6px; border-radius: 3px; background: rgba(88,166,255,0.15); color: var(--accent); font-size: 11px; font-weight: 500; margin-right: 6px; }
  .activity-time { color: var(--text-dim); font-size: 11px; }
  .report-item { padding: 8px 12px; border-radius: 6px; margin-bottom: 4px; background: var(--bg); border: 1px solid var(--border); font-size: 13px; display: flex; align-items: center; justify-content: space-between; cursor: pointer; }
  .report-item:hover { border-color: var(--accent); }
  .report-content { white-space: pre-wrap; font-size: 13px; padding: 12px; background: var(--bg); border-radius: 6px; border: 1px solid var(--border); max-height: 400px; overflow-y: auto; }
  .spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid var(--border); border-top-color: var(--accent); border-radius: 50%; animation: spin 0.6s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  ::-webkit-scrollbar { width: 6px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
</style>
</head>
<body>
<div class="app" id="app">
  <header>
    <h1><span>NATL</span>Claw</h1>
    <div style="display:flex;align-items:center;gap:12px;">
      <select class="persona-select" id="personaSelect" onchange="switchPersona(this.value)"></select>
      <div class="status-badge" id="healthBadge"><div class="status-dot"></div><span>Loading...</span></div>
    </div>
  </header>

  <div class="card" style="margin-bottom:16px;">
    <div class="stats" id="statsRow">
      <div class="stat"><div class="stat-value" id="statHeartbeats">-</div><div class="stat-label">Heartbeats</div></div>
      <div class="stat"><div class="stat-value" id="statNotes">-</div><div class="stat-label">Brain Notes</div></div>
      <div class="stat"><div class="stat-value" id="statConnections">-</div><div class="stat-label">Connections</div></div>
      <div class="stat"><div class="stat-value" id="statTasks">-</div><div class="stat-label">Active Tasks</div></div>
      <div class="stat"><div class="stat-value" id="statPersona">-</div><div class="stat-label">Persona</div></div>
      <div class="stat"><div class="stat-value" id="statProvider">-</div><div class="stat-label">Provider</div></div>
    </div>
  </div>

  <div class="grid" style="margin-bottom:16px;">
    <div class="card">
      <h2>Scheduler</h2>
      <div class="scheduler-bar">
        <div class="scheduler-status"><span class="sched-dot stopped" id="schedDot"></span><span id="schedLabel">Checking...</span></div>
        <div style="flex:1;"></div>
        <span id="schedAgo" style="font-size:12px;color:var(--text-dim);"></span>
        <button class="btn-sm btn-primary" id="schedStartBtn" onclick="startScheduler()" style="display:none;">Start</button>
        <button class="btn-sm btn-danger" id="schedStopBtn" onclick="stopScheduler()" style="display:none;">Stop</button>
      </div>
      <div style="font-size:12px;color:var(--text-dim);margin-bottom:8px;" id="schedSummary"></div>
      <div id="heartbeatLog" style="max-height:260px;overflow-y:auto;"></div>
    </div>
    <div class="card">
      <h2>Recent Activity</h2>
      <div id="activityLog" style="max-height:320px;overflow-y:auto;"><span class="spinner"></span> Loading...</div>
    </div>
  </div>

  <div class="grid">
    <div class="card">
      <h2>Tasks <span class="count" id="taskCount"></span></h2>
      <div class="form-row">
        <input type="text" id="newTaskTitle" placeholder="New task title..." onkeydown="if(event.key==='Enter')addTask()">
        <select id="newTaskPriority"><option value="medium">Medium</option><option value="low">Low</option><option value="high">High</option><option value="urgent">Urgent</option></select>
        <button class="btn-sm btn-primary" onclick="addTask()">Add</button>
      </div>
      <div id="taskList" style="max-height:350px;overflow-y:auto;"></div>
    </div>
    <div class="card">
      <h2>Chat</h2>
      <div class="chat-container">
        <div class="chat-messages" id="chatMessages"></div>
        <div class="chat-input-row">
          <input type="text" id="chatInput" placeholder="Ask NATLClaw..." onkeydown="if(event.key==='Enter'&&!event.shiftKey)sendChat()">
          <button id="chatSendBtn" onclick="sendChat()">Send</button>
        </div>
      </div>
    </div>
    <div class="card">
      <h2>Workspace Reports</h2>
      <button class="btn-sm btn-primary" onclick="generateReport()" id="reportGenBtn" style="margin-bottom:12px;">Generate Audit Report</button>
      <div id="reportList" style="max-height:150px;overflow-y:auto;margin-bottom:8px;"></div>
      <div id="reportContent"></div>
    </div>
    <div class="card">
      <h2>Second Brain</h2>
      <div class="search-row">
        <input type="text" id="brainQuery" placeholder="Search brain..." onkeydown="if(event.key==='Enter')searchBrain()">
        <button class="btn-sm btn-primary" onclick="searchBrain()">Search</button>
      </div>
      <div id="brainResults" style="max-height:300px;overflow-y:auto;"></div>
      <div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--border);">
        <h2 style="margin-bottom:8px;">Dreaming</h2>
        <div style="font-size:12px;color:var(--text-dim);margin-bottom:6px;" id="dreamPolicyLine">Policy: loading...</div>
        <div style="font-size:12px;color:var(--text-dim);margin-bottom:8px;" id="dreamLastRunLine">Last run: -</div>
        <div style="display:flex;gap:8px;align-items:center;">
          <button class="btn-sm btn-ghost" id="dreamDryBtn" onclick="runDream(false)">Run Dry</button>
          <button class="btn-sm btn-primary" id="dreamApplyBtn" onclick="runDream(true)">Run Apply</button>
        </div>
        <div id="dreamResult" style="font-size:12px;color:var(--text-dim);margin-top:8px;"></div>
        <div id="dreamToast" style="display:none;font-size:12px;margin-top:6px;color:var(--green);"></div>
        <div style="display:flex;align-items:center;gap:8px;margin-top:8px;">
          <label for="dreamHistoryFilter" style="font-size:12px;color:var(--text-dim);">History filter:</label>
          <select id="dreamHistoryFilter" class="persona-select" style="font-size:12px;padding:4px 8px;" onchange="renderDreamHistory()">
            <option value="all">all</option>
            <option value="auto_idle">auto idle</option>
            <option value="api">api</option>
            <option value="cli">cli</option>
          </select>
        </div>
        <div id="dreamHistory" style="font-size:12px;color:var(--text-dim);margin-top:8px;max-height:130px;overflow-y:auto;"></div>
      </div>
    </div>
  </div>
</div>

<script>
const API = '';
let dreamRunsCache = [];

async function fetchHealth() {
  try {
    const r = await fetch(API + '/api/health');
    const d = await r.json();
    document.getElementById('statHeartbeats').textContent = d.heartbeat_count || 0;
    document.getElementById('statPersona').textContent = d.persona || '-';
    document.getElementById('statProvider').textContent = d.provider || '-';
    document.getElementById('healthBadge').querySelector('span').textContent = d.status === 'ok' ? 'Online' : 'Error';
  } catch(e) { document.getElementById('healthBadge').querySelector('span').textContent = 'Offline'; }
}

async function fetchHeartbeatStatus() {
  try {
    const r = await fetch(API + '/api/heartbeat/status');
    const d = await r.json();
    const dot = document.getElementById('schedDot');
    const label = document.getElementById('schedLabel');
    const ago = document.getElementById('schedAgo');
    const badge = document.getElementById('healthBadge');
    const badgeDot = badge.querySelector('.status-dot');
    const startBtn = document.getElementById('schedStartBtn');
    const stopBtn = document.getElementById('schedStopBtn');

    if (d.status === 'active') {
      dot.className = 'sched-dot running'; label.textContent = 'Heartbeat Active'; label.style.color = 'var(--green)';
      badgeDot.style.background = 'var(--green)'; badge.style.background = 'rgba(63,185,80,0.15)'; badge.style.color = 'var(--green)'; badge.querySelector('span').textContent = 'Active';
    } else if (d.status === 'stale') {
      dot.className = 'sched-dot stale'; label.textContent = 'Heartbeat Stale'; label.style.color = 'var(--yellow)';
      badgeDot.style.background = 'var(--yellow)'; badge.style.background = 'rgba(210,153,34,0.15)'; badge.style.color = 'var(--yellow)'; badge.querySelector('span').textContent = 'Stale';
    } else {
      dot.className = 'sched-dot stopped'; label.textContent = d.status === 'never_run' ? 'Never Run' : 'Stopped'; label.style.color = 'var(--red)';
      badgeDot.style.background = 'var(--red)'; badge.style.background = 'rgba(248,81,73,0.15)'; badge.style.color = 'var(--red)'; badge.querySelector('span').textContent = label.textContent;
    }
    ago.textContent = d.seconds_ago !== null ? (d.seconds_ago < 60 ? Math.round(d.seconds_ago)+'s ago' : d.seconds_ago < 3600 ? Math.round(d.seconds_ago/60)+'m ago' : Math.round(d.seconds_ago/3600)+'h ago') : '';
    startBtn.style.display = d.scheduler_running ? 'none' : '';
    stopBtn.style.display = d.scheduler_running ? '' : 'none';
  } catch(e) { document.getElementById('schedLabel').textContent = 'Error'; }
}

async function fetchHeartbeatLog() {
  try {
    const r = await fetch(API + '/api/heartbeat/log?limit=15');
    const d = await r.json();
    const el = document.getElementById('heartbeatLog');
    const sum = d.summary || {};
    const sumEl = document.getElementById('schedSummary');
    if (sum.total_heartbeats) sumEl.textContent = `Total: ${sum.total_heartbeats} heartbeats | Avg: ${(sum.avg_elapsed_sec||0).toFixed(1)}s | Notes: ${sum.total_notes_created||0} | Score: ${(sum.avg_score||0).toFixed(1)} avg`;
    const entries = d.entries || [];
    if (!entries.length) { el.innerHTML = '<p style="color:var(--text-dim);font-size:12px;">No heartbeat metrics yet.</p>'; return; }
    let html = '<table class="hb-table"><thead><tr><th>#</th><th>Time</th><th>Persona</th><th>Elapsed</th><th>Notes</th><th>Conns</th><th>Score</th></tr></thead><tbody>';
    entries.forEach(e => { const t = e.timestamp ? new Date(e.timestamp).toLocaleTimeString() : '-'; const sc = e.score > 0 ? 'var(--green)' : 'var(--text-dim)';
      html += `<tr><td>${e.heartbeat_number}</td><td style="color:var(--text-dim);">${t}</td><td>${e.persona||'-'}</td><td>${(e.elapsed_sec||0).toFixed(1)}s</td><td>${e.notes_created||0}</td><td>${e.connections_created||0}</td><td style="color:${sc};font-weight:600;">${e.score}</td></tr>`; });
    el.innerHTML = html + '</tbody></table>';
  } catch(e) { document.getElementById('heartbeatLog').innerHTML = '<p style="color:var(--red);font-size:12px;">Failed to load.</p>'; }
}

async function fetchActivity() {
  try {
    const r = await fetch(API + '/api/heartbeat/activity?limit=15');
    const entries = await r.json();
    const el = document.getElementById('activityLog');
    if (!entries.length) { el.innerHTML = '<p style="color:var(--text-dim);font-size:12px;">No activity yet.</p>'; return; }
    el.innerHTML = entries.reverse().map(e => { const t = e.timestamp ? new Date(e.timestamp).toLocaleTimeString() : '';
      const resp = (e.response || '').replace(/\\n/g, ' ').slice(0, 150);
      return `<div class="activity-item"><span class="activity-step">${escHtml(e.step)}</span><span class="activity-time">${t}</span><div style="color:var(--text);margin-top:2px;">${escHtml(resp)}${resp.length>=150?'...':''}</div></div>`; }).join('');
  } catch(e) { document.getElementById('activityLog').innerHTML = '<p style="color:var(--red);">Failed to load.</p>'; }
}

async function fetchBrainStats() {
  try { const r = await fetch(API + '/api/brain/stats'); const d = await r.json();
    document.getElementById('statNotes').textContent = d.notes || 0;
    document.getElementById('statConnections').textContent = d.connections || 0;
  } catch(e) {}
}

async function fetchDreamPanel() {
  try {
    const [policyResp, statsResp] = await Promise.all([
      fetch(API + '/api/brain/dream/policy'),
      fetch(API + '/api/brain/stats'),
    ]);
    const policy = await policyResp.json();
    const stats = await statsResp.json();
    const d = policy.dream || {};
    document.getElementById('dreamPolicyLine').textContent =
      `Policy: enabled=${d.enabled} | idle_streak_min=${d.idle_streak_min} | max_age_days=${d.max_age_days}`;
    const lastDream = stats.last_dream || 'never';
    const lastHb = stats.last_dream_heartbeat || 0;
    document.getElementById('dreamLastRunLine').textContent =
      `Last run: ${lastDream} (heartbeat ${lastHb})`;
    dreamRunsCache = Array.isArray(stats.dream_runs_recent) ? stats.dream_runs_recent.slice(0, 20) : [];
    renderDreamHistory();
  } catch (e) {
    document.getElementById('dreamPolicyLine').textContent = 'Policy: unavailable';
    const hist = document.getElementById('dreamHistory');
    if (hist) hist.textContent = 'Recent runs: unavailable';
  }
}

function renderDreamHistory() {
  const hist = document.getElementById('dreamHistory');
  const filter = (document.getElementById('dreamHistoryFilter')?.value || 'all').toLowerCase();
  const runs = (dreamRunsCache || []).filter(run => {
    const trigger = String(run?.trigger || '').toLowerCase();
    if (filter === 'all') return true;
    if (filter === 'auto_idle') return trigger === 'auto_idle';
    if (filter === 'api') return trigger.startsWith('api_');
    if (filter === 'cli') return trigger.startsWith('cli_');
    return true;
  });
  if (!runs.length) {
    hist.textContent = 'Recent runs: none';
    return;
  }
  hist.innerHTML = runs.slice(0, 8).map(run => {
    const ts = run.timestamp ? new Date(run.timestamp).toLocaleString() : '-';
    const trig = run.trigger || 'unknown';
    const ded = run.deduped || 0;
    const stale = run.stale_archived || 0;
    const lint = run.lint_issues || 0;
    const cacheIndex = (dreamRunsCache || []).indexOf(run);
    return `<div class="activity-item">` +
      `<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;">` +
      `<div><span class="activity-time">${escHtml(ts)}</span> <span style="color:var(--accent);">${escHtml(trig)}</span> <span>dedup=${ded}, stale=${stale}, lint=${lint}</span></div>` +
      `<button class="btn-sm btn-ghost" style="padding:2px 8px;font-size:11px;" onclick="copyDreamRun(${cacheIndex})">Copy JSON</button>` +
      `</div>` +
      `</div>`;
  }).join('');
}

async function copyDreamRun(index) {
  const run = (dreamRunsCache || [])[index];
  if (!run) {
    showDreamToast('Copy failed: run not found', true);
    return;
  }
  const payload = JSON.stringify(run);
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(payload);
      showDreamToast('Copied run JSON');
    } else {
      throw new Error('clipboard unavailable');
    }
  } catch (_e) {
    showDreamToast('Clipboard unavailable, showing JSON below', true);
    const resultEl = document.getElementById('dreamResult');
    resultEl.textContent = `Run JSON: ${payload}`;
  }
}

let dreamToastTimer = null;
function showDreamToast(message, isError = false) {
  const toast = document.getElementById('dreamToast');
  if (!toast) return;
  toast.textContent = message;
  toast.style.display = 'block';
  toast.style.color = isError ? 'var(--yellow)' : 'var(--green)';
  if (dreamToastTimer) clearTimeout(dreamToastTimer);
  dreamToastTimer = setTimeout(() => {
    toast.style.display = 'none';
  }, 1800);
}

async function fetchTasks() {
  try { const r = await fetch(API + '/api/tasks'); const tasks = await r.json();
    const active = tasks.filter(t => ['pending','assigned','in_progress','blocked'].includes(t.status));
    document.getElementById('statTasks').textContent = active.length;
    document.getElementById('taskCount').textContent = `(${tasks.length})`;
    renderTasks(tasks);
  } catch(e) { document.getElementById('taskList').innerHTML = '<p style="color:var(--text-dim);">Failed to load tasks.</p>'; }
}

async function fetchPersonas() {
  try { const r = await fetch(API + '/api/personas'); const personas = await r.json();
    document.getElementById('personaSelect').innerHTML = personas.map(p => `<option value="${p.name}" ${p.active?'selected':''}>${p.name}</option>`).join('');
  } catch(e) {}
}

async function fetchReports() {
  try { const r = await fetch(API + '/api/reports'); const reports = await r.json();
    const el = document.getElementById('reportList');
    if (!reports.length) { el.innerHTML = '<p style="color:var(--text-dim);font-size:12px;">No reports yet. Click Generate to create one.</p>'; return; }
    el.innerHTML = reports.slice(0,10).map(rp => {
      const date = new Date(rp.created).toLocaleString();
      const kb = (rp.size/1024).toFixed(1);
      return `<div class="report-item" onclick="loadReport('${rp.filename}')"><span>${rp.filename}</span><span style="color:var(--text-dim);font-size:11px;">${date} (${kb}KB)</span></div>`;
    }).join('');
  } catch(e) {}
}

async function loadReport(filename) {
  const el = document.getElementById('reportContent');
  el.innerHTML = '<span class="spinner"></span> Loading...';
  try { const r = await fetch(API + '/api/reports/' + filename); const d = await r.json();
    el.innerHTML = `<div class="report-content">${escHtml(d.content)}</div>`;
  } catch(e) { el.innerHTML = '<p style="color:var(--red);">Failed to load report.</p>'; }
}

async function generateReport() {
  const btn = document.getElementById('reportGenBtn');
  btn.disabled = true; btn.textContent = 'Generating...';
  try { await fetch(API + '/api/reports/generate', {method:'POST'});
    btn.textContent = 'Running in background...';
    setTimeout(() => { btn.disabled = false; btn.textContent = 'Generate Audit Report'; fetchReports(); }, 30000);
  } catch(e) { btn.disabled = false; btn.textContent = 'Generate Audit Report'; }
}

const TASK_ICONS = { pending:'\\u23f3', assigned:'\\u2192', in_progress:'\\u25b6\\ufe0f', blocked:'\\u23f8\\ufe0f', completed:'\\u2705', failed:'\\u274c' };
function renderTasks(tasks) {
  const el = document.getElementById('taskList');
  if (!tasks.length) { el.innerHTML = '<p style="color:var(--text-dim);font-size:13px;">No tasks yet.</p>'; return; }
  const order = {urgent:0,high:1,medium:2,low:3};
  const statusOrder = {in_progress:0,assigned:1,blocked:2,pending:3,completed:4,failed:5};
  tasks.sort((a,b) => (statusOrder[a.status]||9)-(statusOrder[b.status]||9) || (order[a.priority]||9)-(order[b.priority]||9));
  el.innerHTML = tasks.map(t => {
    const icon = TASK_ICONS[t.status]||'?'; let extra = '';
    if (t.status==='in_progress') extra = ` | ${t.heartbeats_spent}/${t.max_heartbeats} heartbeats`;
    if (t.status==='blocked' && t.questions.length) { const q = t.questions[t.questions.length-1].question;
      extra += `<div style="color:var(--yellow);font-size:12px;margin-top:4px;">Q: ${escHtml(q.slice(0,100))}</div><div class="answer-form"><input type="text" id="ans-${t.id}" placeholder="Your answer..."><button class="btn-sm btn-primary" onclick="answerTask('${t.id}')">Answer</button></div>`; }
    const actions = [];
    if (['pending','assigned','in_progress','blocked'].includes(t.status)) actions.push(`<button class="btn-sm btn-ghost" onclick="cancelTask('${t.id}')">Cancel</button>`);
    if (['failed','blocked'].includes(t.status)) actions.push(`<button class="btn-sm btn-ghost" onclick="retryTask('${t.id}')">Retry</button>`);
    return `<div class="task-item priority-${t.priority}"><div class="task-icon">${icon}</div><div class="task-body"><div class="task-title">${escHtml(t.title)}</div><div class="task-meta">${t.id} | ${t.priority}${t.assigned_to?' | @'+t.assigned_to:''}${extra}</div></div><span class="task-badge badge-${t.status}">${t.status.replace('_',' ')}</span><div style="display:flex;gap:4px;">${actions.join('')}</div></div>`; }).join('');
}

async function addTask() { const title = document.getElementById('newTaskTitle').value.trim(); if (!title) return;
  await fetch(API+'/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title,priority:document.getElementById('newTaskPriority').value})}); document.getElementById('newTaskTitle').value=''; fetchTasks(); }
async function answerTask(id) { const input = document.getElementById('ans-'+id); if (!input||!input.value.trim()) return;
  await fetch(API+`/api/tasks/${id}/answer`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({answer:input.value.trim()})}); fetchTasks(); }
async function cancelTask(id) { await fetch(API+`/api/tasks/${id}/cancel`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reason:'Cancelled from dashboard'})}); fetchTasks(); }
async function retryTask(id) { await fetch(API+`/api/tasks/${id}/retry`,{method:'POST'}); fetchTasks(); }

async function sendChat() { const input = document.getElementById('chatInput'); const msg = input.value.trim(); if (!msg) return; input.value = '';
  const msgs = document.getElementById('chatMessages'); msgs.innerHTML += `<div class="chat-msg user">${escHtml(msg)}</div>`; msgs.scrollTop = msgs.scrollHeight;
  const btn = document.getElementById('chatSendBtn'); btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>';
  try { const r = await fetch(API+'/v1/chat/completions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model:document.getElementById('personaSelect').value||'default',messages:[{role:'user',content:msg}],stream:false})}); const d = await r.json();
    msgs.innerHTML += `<div class="chat-msg assistant">${escHtml(d.choices?.[0]?.message?.content||'No response.')}</div>`;
  } catch(e) { msgs.innerHTML += `<div class="chat-msg assistant" style="color:var(--red);">Error: ${escHtml(e.message)}</div>`; }
  btn.disabled = false; btn.textContent = 'Send'; msgs.scrollTop = msgs.scrollHeight; }

async function searchBrain() { const query = document.getElementById('brainQuery').value.trim(); if (!query) return;
  const el = document.getElementById('brainResults'); el.innerHTML = '<span class="spinner"></span> Searching...';
  try { const r = await fetch(API+'/api/brain/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query,max_results:15})}); const d = await r.json();
    if (!d.results||!d.results.length) { el.innerHTML = `<p style="color:var(--text-dim);font-size:13px;">No results for "${escHtml(query)}"</p>`; return; }
    el.innerHTML = d.results.map(n => { const tags = (n.tags||[]).join(', '); const summary = n.summary||(n.content||'').slice(0,120);
      return `<div class="note-item"><strong>[${n.id}]</strong> ${escHtml(summary)}${tags?`<div class="note-tags">${escHtml(tags)}</div>`:''}</div>`; }).join('');
  } catch(e) { el.innerHTML = '<p style="color:var(--red);">Search failed.</p>'; } }

async function runDream(apply) {
  const dryBtn = document.getElementById('dreamDryBtn');
  const applyBtn = document.getElementById('dreamApplyBtn');
  const resultEl = document.getElementById('dreamResult');
  dryBtn.disabled = true;
  applyBtn.disabled = true;
  resultEl.textContent = apply ? 'Running apply dream...' : 'Running dry dream...';
  try {
    const r = await fetch(API + '/api/brain/dream/run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({apply: !!apply}),
    });
    const d = await r.json();
    const p = d.phases || {};
    const dedup = p.consolidate?.exact_duplicates_archived || 0;
    const stale = p.prune?.stale_archived || 0;
    const lint = p.prune?.lint_issues || 0;
    resultEl.textContent = `Dream ${apply ? 'apply' : 'dry'} done: dedup=${dedup}, stale=${stale}, lint=${lint}`;
    await fetchDreamPanel();
    if (apply) await fetchBrainStats();
  } catch (e) {
    resultEl.textContent = `Dream failed: ${e.message || 'unknown error'}`;
  } finally {
    dryBtn.disabled = false;
    applyBtn.disabled = false;
  }
}

async function startScheduler() { const btn = document.getElementById('schedStartBtn'); btn.disabled=true; btn.textContent='Starting...';
  try { const r = await fetch(API+'/api/scheduler/start',{method:'POST'}); const d = await r.json(); btn.textContent = d.status==='started'?'Started!':'Already running';
  } catch(e) { btn.textContent='Error'; } setTimeout(()=>{btn.disabled=false;btn.textContent='Start';fetchHeartbeatStatus();},2000); }
async function stopScheduler() { const btn = document.getElementById('schedStopBtn'); btn.disabled=true; btn.textContent='Stopping...';
  try { const r = await fetch(API+'/api/scheduler/stop',{method:'POST'}); const d = await r.json(); btn.textContent=d.status==='stopped'?'Stopped!':'Not running';
  } catch(e) { btn.textContent='Error'; } setTimeout(()=>{btn.disabled=false;btn.textContent='Stop';fetchHeartbeatStatus();},2000); }

function switchPersona(name) { console.log('Switched to persona:', name); }
function escHtml(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

async function init() {
  await Promise.all([fetchHealth(), fetchBrainStats(), fetchDreamPanel(), fetchTasks(), fetchPersonas(), fetchHeartbeatStatus(), fetchHeartbeatLog(), fetchActivity(), fetchReports()]);
  setInterval(() => { fetchHeartbeatStatus(); }, 15000);
  setInterval(() => { fetchHealth(); fetchBrainStats(); fetchDreamPanel(); fetchTasks(); fetchHeartbeatLog(); fetchActivity(); fetchReports(); }, 30000);
}
init();
</script>
</body>
</html>"""


# ── Standalone runner ────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="NATLClaw API Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    parser.add_argument("--env", default=".env", help="Path to .env file")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    config = load_config(args.env)
    app = create_app(config)

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)


# Module-level app for uvicorn --reload (e.g. uvicorn api_server:app)
app = create_app()


if __name__ == "__main__":
    main()
