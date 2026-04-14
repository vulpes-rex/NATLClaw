from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

from config import AppConfig
from error_classification import classify_error_text, top_error_types
from execution_log import recent_entries
from messaging import load_outbox
from scheduler import get_scheduler_lock_info, get_scheduler_runtime_backpressure_stats
from state import load_state
from tasks import get_active_task, get_blocked_tasks, load_tasks


def _heartbeat_snapshot(last_heartbeat: str | None) -> dict[str, Any]:
    status = "unknown"
    seconds_ago: float | None = None
    if not last_heartbeat:
        return {"status": "never_run", "seconds_ago": None}
    try:
        last_dt = datetime.fromisoformat(last_heartbeat)
        now = datetime.now(timezone.utc)
        seconds_ago = (now - last_dt).total_seconds()
        status = "active" if seconds_ago < 300 else "stale"
    except (ValueError, TypeError):
        status = "unknown"
    return {"status": status, "seconds_ago": round(seconds_ago, 1) if seconds_ago is not None else None}


def _error_summary(state_file: str) -> dict[str, Any]:
    db_path = os.path.join(os.path.dirname(state_file), "execution_log.db")
    entries = recent_entries(50, db_path=db_path)
    error_entries = [
        e for e in entries
        if "ERROR" in (e.get("response", "") or "").upper()
    ]
    last_error = error_entries[-1] if error_entries else None
    top_types = top_error_types((e.get("response", "") or "" for e in error_entries), limit=3)
    return {
        "recent_error_count": len(error_entries),
        "top_error_types": top_types,
        "last_error": {
            "timestamp": last_error.get("timestamp"),
            "step": last_error.get("step"),
            "preview": (last_error.get("response", "") or "")[:200],
            "type": classify_error_text(last_error.get("response", "") or ""),
        } if last_error else None,
    }


def _reliability_summary(
    *,
    execution_count: int,
    recent_error_count: int,
    lock_info: dict[str, Any],
    sample_window: int = 50,
) -> dict[str, Any]:
    window_heartbeats = min(max(0, execution_count), sample_window)
    error_rate = (
        round(recent_error_count / window_heartbeats, 3)
        if window_heartbeats > 0
        else None
    )
    stale_lock = bool(lock_info.get("exists") and lock_info.get("stale"))

    status = "healthy"
    reasons: list[str] = []
    if stale_lock:
        status = "degraded"
        reasons.append("stale_lock_detected")
    if error_rate is not None and error_rate > 0.2:
        status = "degraded"
        reasons.append("high_recent_error_rate")

    return {
        "status": status,
        "window_heartbeats": window_heartbeats,
        "recent_error_count": recent_error_count,
        "error_rate": error_rate,
        "stale_lock": stale_lock,
        "reasons": reasons,
    }


def _task_sla_summary(tasks: list[Any]) -> dict[str, Any]:
    activeish = [t for t in tasks if getattr(t, "status", "") in ("assigned", "in_progress")]
    at_risk = 0
    breached = 0
    for task in activeish:
        max_hb = int(getattr(task, "max_heartbeats", 0) or 0)
        spent = int(getattr(task, "heartbeats_spent", 0) or 0)
        if max_hb <= 0:
            continue
        if spent >= max_hb:
            breached += 1
        elif spent / max_hb >= 0.8:
            at_risk += 1

    oldest_pending_age_sec: float | None = None
    pending = [t for t in tasks if getattr(t, "status", "") == "pending"]
    now = datetime.now(timezone.utc)
    for task in pending:
        created_at = getattr(task, "created_at", "")
        if not created_at:
            continue
        try:
            age = (now - datetime.fromisoformat(created_at)).total_seconds()
        except (ValueError, TypeError):
            continue
        if oldest_pending_age_sec is None or age > oldest_pending_age_sec:
            oldest_pending_age_sec = age

    return {
        "at_risk_count": at_risk,
        "breached_count": breached,
        "oldest_pending_age_sec": (
            round(oldest_pending_age_sec, 1) if oldest_pending_age_sec is not None else None
        ),
    }


async def build_operator_status(
    config: AppConfig,
    *,
    scheduler_task: asyncio.Task | None = None,
) -> dict[str, Any]:
    """Build a single operator-facing runtime snapshot."""
    state = await load_state(config.state_file)
    tasks = await load_tasks(config.state_file)
    outbox = await load_outbox(config.state_file)

    lock = get_scheduler_lock_info(config.state_file)
    heartbeat = _heartbeat_snapshot(state.last_heartbeat)

    active_task = get_active_task(tasks, config.persona)
    blocked = get_blocked_tasks(tasks)
    unread = [m for m in outbox if getattr(m, "status", "") == "unread"]

    in_process_scheduler_running = bool(scheduler_task is not None and not scheduler_task.done())
    lock_scheduler_running = bool(lock.get("exists") and lock.get("pid_alive"))
    errors = _error_summary(config.state_file)
    reliability = _reliability_summary(
        execution_count=state.execution_count,
        recent_error_count=int(errors.get("recent_error_count", 0)),
        lock_info=lock,
    )

    return {
        "agent": {
            "name": config.agent_name,
            "persona": config.persona,
            "provider": config.provider,
            "model": config.model,
        },
        "scheduler": {
            "running": in_process_scheduler_running or lock_scheduler_running,
            "in_process_task_running": in_process_scheduler_running,
            "lock": lock,
            "backpressure": get_scheduler_runtime_backpressure_stats(),
        },
        "heartbeat": {
            "count": state.execution_count,
            "last": state.last_heartbeat,
            **heartbeat,
        },
        "tasks": {
            "total": len(tasks),
            "active": {
                "id": active_task.id,
                "title": active_task.title,
                "status": active_task.status,
                "priority": active_task.priority,
                "heartbeats_spent": active_task.heartbeats_spent,
                "max_heartbeats": active_task.max_heartbeats,
            } if active_task else None,
            "blocked_count": len(blocked),
            "sla": _task_sla_summary(tasks),
        },
        "inbox": {
            "unread_count": len(unread),
            "requires_response_count": sum(1 for m in unread if getattr(m, "requires_response", False)),
        },
        "errors": errors,
        "reliability": reliability,
    }
