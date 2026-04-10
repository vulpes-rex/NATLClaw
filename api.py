"""FastAPI HTTP layer for NATLClaw.

Exposes the core agent functions as REST endpoints so external
orchestrators (n8n, webhooks, etc.) can drive the agent over HTTP.

Start with::

    natl api                     # default 127.0.0.1:8321
    natl api --host 0.0.0.0 --port 9000

Or directly::

    uvicorn api:app --host 127.0.0.1 --port 8321
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from config import AppConfig, load_config, validate_config
from event_watcher import is_watcher_running, start_background_watcher, stop_background_watcher
from messaging import Message, load_outbox, save_outbox
from second_brain import (
    add_note,
    apply_relevance_feedback,
    build_brain_stats_from_store,
    describe_note_from_store,
    get_topic_map_from_store,
    lint_brain,
    load_brain,
    record_contradiction,
    save_brain,
    search_notes_from_store,
    trace_topic_from_store,
)
from tasks import (
    Task,
    answer_task,
    cancel_task,
    create_task,
    find_task,
    load_tasks,
    retry_task,
    save_tasks,
)

logger = logging.getLogger(__name__)

# ── Startup ────────────────────────────────────────────────────────────

_config: AppConfig | None = None


def _get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


app = FastAPI(
    title="NATLClaw API",
    version="0.1.0",
    description="HTTP interface for the NATLClaw autonomous agent.",
)


# ── Request / response models ─────────────────────────────────────────


class TaskCreateRequest(BaseModel):
    title: str
    description: str = ""
    priority: str = "medium"
    max_heartbeats: int = 10


class TaskAnswerRequest(BaseModel):
    answer: str


class TaskCancelRequest(BaseModel):
    reason: str = ""


class NoteAddRequest(BaseModel):
    content: str
    summary: str = ""
    source: str = "api"
    note_type: str = "general"
    status: str = "active"
    confidence: int | None = None
    evidence: list[str] | None = None
    tags: list[str] | None = None
    category: str = "resources"


class FeedbackRequest(BaseModel):
    relevant: bool
    reason: str = ""


class ContradictionRequest(BaseModel):
    contradicting_note_id: str
    reason: str = ""
    supersede: bool | None = None


# ── Health ─────────────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ── Heartbeat ──────────────────────────────────────────────────────────


@app.post("/heartbeat")
async def trigger_heartbeat() -> dict[str, Any]:
    """Run a single scheduler heartbeat and return."""
    from scheduler import run_scheduler

    config = _get_config()
    try:
        await run_scheduler(config, max_iterations=1)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"status": "completed", "iterations": 1}


# ── Tasks ──────────────────────────────────────────────────────────────


@app.get("/tasks")
async def list_tasks(
    status: str = Query("all", description="Filter by status or 'all'"),
) -> list[dict[str, Any]]:
    config = _get_config()
    tasks = await load_tasks(config.state_file)
    if status != "all":
        tasks = [t for t in tasks if t.status == status]
    return [asdict(t) for t in tasks]


@app.post("/tasks", status_code=201)
async def create_task_endpoint(body: TaskCreateRequest) -> dict[str, Any]:
    config = _get_config()
    task = create_task(
        title=body.title,
        description=body.description,
        priority=body.priority,
        max_heartbeats=body.max_heartbeats,
    )
    tasks = await load_tasks(config.state_file)
    tasks.append(task)
    await save_tasks(tasks, config.state_file)
    return asdict(task)


@app.get("/tasks/{task_id}")
async def get_task(task_id: str) -> dict[str, Any]:
    config = _get_config()
    tasks = await load_tasks(config.state_file)
    task = find_task(tasks, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return asdict(task)


@app.post("/tasks/{task_id}/answer")
async def answer_task_endpoint(task_id: str, body: TaskAnswerRequest) -> dict[str, Any]:
    config = _get_config()
    tasks = await load_tasks(config.state_file)
    task = find_task(tasks, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    if task.status != "blocked":
        raise HTTPException(status_code=409, detail=f"Task {task_id} is not blocked (status={task.status})")
    answer_task(task, body.answer)
    await save_tasks(tasks, config.state_file)
    return asdict(task)


@app.post("/tasks/{task_id}/cancel")
async def cancel_task_endpoint(task_id: str, body: TaskCancelRequest | None = None) -> dict[str, Any]:
    config = _get_config()
    tasks = await load_tasks(config.state_file)
    task = find_task(tasks, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    if task.status in ("completed", "failed"):
        raise HTTPException(status_code=409, detail=f"Task {task_id} is already terminal (status={task.status})")
    cancel_task(task, reason=body.reason if body else "")
    await save_tasks(tasks, config.state_file)
    return asdict(task)


@app.post("/tasks/{task_id}/retry")
async def retry_task_endpoint(task_id: str) -> dict[str, Any]:
    config = _get_config()
    tasks = await load_tasks(config.state_file)
    task = find_task(tasks, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    if task.status not in ("failed", "blocked"):
        raise HTTPException(
            status_code=409,
            detail=f"Task {task_id} cannot be retried (status={task.status})",
        )
    retry_task(task)
    await save_tasks(tasks, config.state_file)
    return asdict(task)


# ── Inbox (outbox messages) ───────────────────────────────────────────


@app.get("/inbox")
async def list_inbox(
    status: str = Query("all", description="Filter: all, unread, read, dismissed"),
    msg_type: str = Query("all", alias="type", description="Filter by message type"),
) -> list[dict[str, Any]]:
    config = _get_config()
    messages = await load_outbox(config.state_file)
    if status != "all":
        messages = [m for m in messages if m.status == status]
    if msg_type != "all":
        messages = [m for m in messages if m.type == msg_type]
    return [asdict(m) for m in messages]


@app.get("/inbox/{message_id}")
async def get_inbox_message(message_id: str) -> dict[str, Any]:
    config = _get_config()
    messages = await load_outbox(config.state_file)
    for m in messages:
        if m.id == message_id:
            return asdict(m)
    raise HTTPException(status_code=404, detail=f"Message {message_id} not found")


@app.post("/inbox/{message_id}/dismiss")
async def dismiss_message(message_id: str) -> dict[str, Any]:
    from datetime import datetime, timezone

    config = _get_config()
    messages = await load_outbox(config.state_file)
    for m in messages:
        if m.id == message_id:
            m.status = "dismissed"
            m.dismissed_at = datetime.now(timezone.utc).isoformat()
            await save_outbox(messages, config.state_file)
            return asdict(m)
    raise HTTPException(status_code=404, detail=f"Message {message_id} not found")


@app.post("/inbox/clear")
async def clear_inbox() -> dict[str, int]:
    from datetime import datetime, timezone

    config = _get_config()
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


# ── Brain ──────────────────────────────────────────────────────────────


@app.get("/brain/stats")
async def brain_stats() -> dict[str, Any]:
    config = _get_config()
    return build_brain_stats_from_store(config.state_file)


@app.get("/brain/search")
async def brain_search(
    q: str = Query(..., description="Search query"),
    max_results: int = Query(10, ge=1, le=100),
) -> list[dict[str, Any]]:
    config = _get_config()
    return search_notes_from_store(
        config.state_file, q, max_results=max_results, record_access=True,
    )


@app.get("/brain/topics")
async def brain_topics() -> list[dict[str, Any]]:
    config = _get_config()
    return get_topic_map_from_store(config.state_file)


@app.get("/brain/topics/{topic_name}")
async def brain_trace_topic(
    topic_name: str,
    depth: int = Query(1, ge=1, le=5),
    limit: int = Query(10, ge=1, le=100),
) -> dict[str, Any]:
    config = _get_config()
    result = trace_topic_from_store(
        config.state_file,
        topic_name,
        depth=depth,
        limit=limit,
        include_connected=True,
        record_access=True,
    )
    if result is None:
        raise HTTPException(status_code=404, detail=f"Topic '{topic_name}' not found")
    return result


@app.get("/brain/notes/{note_id}")
async def brain_describe_note(note_id: str) -> dict[str, Any]:
    config = _get_config()
    result = describe_note_from_store(
        config.state_file, note_id, record_access=True,
    )
    if result is None:
        raise HTTPException(status_code=404, detail=f"Note {note_id} not found")
    return result


@app.post("/brain/notes", status_code=201)
async def brain_add_note(body: NoteAddRequest) -> dict[str, Any]:
    config = _get_config()
    brain = await load_brain(config.state_file)
    note_id = add_note(
        brain,
        body.content,
        summary=body.summary,
        source=body.source,
        note_type=body.note_type,
        status=body.status,
        confidence=body.confidence,
        evidence=body.evidence,
        tags=body.tags,
        category=body.category,
    )
    await save_brain(brain, config.state_file)
    return {"note_id": note_id}


@app.post("/brain/notes/{note_id}/feedback")
async def brain_feedback(note_id: str, body: FeedbackRequest) -> dict[str, Any]:
    config = _get_config()
    brain = await load_brain(config.state_file)
    ok = apply_relevance_feedback(
        brain, note_id, relevant=body.relevant, reason=body.reason,
    )
    if not ok:
        raise HTTPException(status_code=404, detail=f"Note {note_id} not found")
    await save_brain(brain, config.state_file)
    return {"note_id": note_id, "relevant": body.relevant}


@app.post("/brain/notes/{note_id}/contradict")
async def brain_contradict(note_id: str, body: ContradictionRequest) -> dict[str, Any]:
    config = _get_config()
    brain = await load_brain(config.state_file)
    ok = record_contradiction(
        brain,
        note_id,
        body.contradicting_note_id,
        reason=body.reason,
        supersede=body.supersede,
    )
    if not ok:
        raise HTTPException(status_code=404, detail=f"Note {note_id} or contradicting note not found")
    await save_brain(brain, config.state_file)
    return {"note_id": note_id, "contradicting_note_id": body.contradicting_note_id}


@app.post("/brain/lint")
async def brain_lint_endpoint() -> list[dict[str, Any]]:
    config = _get_config()
    brain = await load_brain(config.state_file)
    return lint_brain(brain)


# ── Watcher ────────────────────────────────────────────────────────────


@app.get("/watch/status")
async def watch_status() -> dict[str, Any]:
    running = is_watcher_running()
    return {"running": running}


@app.post("/watch/start")
async def watch_start() -> dict[str, str]:
    if is_watcher_running():
        return {"status": "already_running"}
    start_background_watcher()
    return {"status": "started"}


@app.post("/watch/stop")
async def watch_stop() -> dict[str, str]:
    if not is_watcher_running():
        return {"status": "not_running"}
    stop_background_watcher()
    return {"status": "stopped"}


# ── Config (sanitised) ────────────────────────────────────────────────

_SECRET_FIELDS = frozenset({
    "openai_api_key",
    "github_pat",
    "openrouter_api_key",
    "azure_openai_api_key",
})


@app.get("/config")
async def get_config() -> dict[str, Any]:
    config = _get_config()
    data = asdict(config)
    for key in _SECRET_FIELDS:
        if key in data and data[key]:
            data[key] = "***"
    return data
