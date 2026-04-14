"""Surface ingress bridge for S18 single-channel MVP.

Validates normalized surface envelopes, applies idempotency checks, routes
to task/inbox primitives, and emits scheduler wake signals via event queue.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from event_watcher import enqueue_event
from messaging import append_message, create_message, load_outbox, save_outbox
from tasks import create_task, load_tasks, save_tasks

logger = logging.getLogger(__name__)

_ROUTING_PRIORITIES = {"low", "normal", "high", "urgent"}
_TASK_PRIORITY_MAP = {
    "low": "low",
    "normal": "medium",
    "high": "high",
    "urgent": "urgent",
}
_ACTION_HINTS = (
    "please",
    "todo",
    "action",
    "follow-up",
    "follow up",
    "create task",
    "summarize",
    "need",
)


class SurfaceIngressError(ValueError):
    """Base class for surface ingress validation and processing errors."""


class SurfaceIngressDisabledError(SurfaceIngressError):
    """Raised when the ingress feature flag is disabled."""


class SurfaceAdapterNotAllowedError(SurfaceIngressError):
    """Raised when adapter/channel is not allowed by configuration."""


class SurfaceIdempotencyConflictError(SurfaceIngressError):
    """Raised when idempotency key collides with different payload."""


def _idempotency_store_path(state_file: str) -> str:
    return os.path.join(os.path.dirname(state_file), "surface_idempotency.json")


def _payload_hash(event: dict[str, Any]) -> str:
    payload = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_idempotency_store(state_file: str) -> dict[str, Any]:
    path = _idempotency_store_path(state_file)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            return raw
    except (OSError, json.JSONDecodeError, TypeError):
        logger.warning("Failed to load surface idempotency store from %s", path, exc_info=True)
    return {}


def _save_idempotency_store(state_file: str, store: dict[str, Any]) -> None:
    path = _idempotency_store_path(state_file)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    dir_name = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _require_object(raw: Any, field: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise SurfaceIngressError(f"'{field}' must be an object")
    return raw


def _require_string(raw: dict[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise SurfaceIngressError(f"'{field}' must be a non-empty string")
    return value.strip()


def _validate_timestamp(value: str) -> str:
    normalized = value.replace("Z", "+00:00")
    try:
        datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SurfaceIngressError("'ts' must be a valid ISO-8601 timestamp") from exc
    return value


def validate_surface_event(raw_event: Any) -> dict[str, Any]:
    """Validate a surface-event-v1 envelope and normalize optional fields."""
    event = _require_object(raw_event, "body")
    spec_version = _require_string(event, "spec_version")
    if spec_version != "1.0":
        raise SurfaceIngressError("'spec_version' must be '1.0'")

    event_id = _require_string(event, "event_id")
    event_type = _require_string(event, "event_type")
    ts = _validate_timestamp(_require_string(event, "ts"))

    source = _require_object(event.get("source"), "source")
    _require_string(source, "adapter")
    _require_string(source, "channel_type")

    session = _require_object(event.get("session"), "session")
    _require_string(session, "session_id")

    payload = _require_object(event.get("payload"), "payload")

    routing = event.get("routing") or {}
    if not isinstance(routing, dict):
        raise SurfaceIngressError("'routing' must be an object")
    if "priority" in routing and routing["priority"] not in _ROUTING_PRIORITIES:
        raise SurfaceIngressError("'routing.priority' must be one of: low, normal, high, urgent")
    if "requires_reply" in routing and not isinstance(routing["requires_reply"], bool):
        raise SurfaceIngressError("'routing.requires_reply' must be a boolean")

    meta = event.get("meta") or {}
    if not isinstance(meta, dict):
        raise SurfaceIngressError("'meta' must be an object")

    return {
        **event,
        "spec_version": spec_version,
        "event_id": event_id,
        "event_type": event_type,
        "ts": ts,
        "source": source,
        "session": session,
        "payload": payload,
        "routing": routing,
        "meta": meta,
    }


def _derive_decision(event: dict[str, Any]) -> tuple[str, str]:
    payload = event.get("payload", {})
    routing = event.get("routing", {})
    event_type = str(event.get("event_type", "")).lower()
    text = str(payload.get("text", "")).strip()
    text_lower = text.lower()
    intent = str(payload.get("intent", "")).strip().lower()
    requires_reply = bool(routing.get("requires_reply", False))

    if intent in {"create_task", "task"}:
        return "create_task", "payload intent requested task creation"
    if event_type in {"task.requested", "action.requested"}:
        return "create_task", "event type indicates actionable request"
    if requires_reply:
        return "create_task", "routing.requires_reply=true"
    if any(token in text_lower for token in _ACTION_HINTS):
        return "create_task", "message text appears actionable"
    return "append_inbox_message", "message treated as informational"


def _build_task_fields(event: dict[str, Any]) -> tuple[str, str, str]:
    payload = event["payload"]
    source = event["source"]
    session = event["session"]
    routing = event.get("routing", {})

    text = str(payload.get("text", "")).strip()
    title = str(payload.get("task_title", "")).strip()
    if not title:
        if text:
            title = text[:120]
        else:
            title = f"Surface event {event['event_id']}"

    priority = _TASK_PRIORITY_MAP.get(str(routing.get("priority", "normal")).lower(), "medium")
    description = (
        f"Surface ingress event {event['event_id']} from "
        f"{source.get('adapter', 'unknown')} session {session.get('session_id', 'unknown')}.\n"
        f"Event type: {event.get('event_type')}\n"
        f"Text: {text or '(none)'}"
    )
    return title, description, priority


def _idempotency_key(event: dict[str, Any]) -> str:
    meta = event.get("meta", {})
    explicit_key = meta.get("idempotency_key")
    if isinstance(explicit_key, str) and explicit_key.strip():
        return explicit_key.strip()
    return f"{event['source']['adapter']}:{event['event_id']}"


async def process_surface_event(
    event: dict[str, Any],
    *,
    state_file: str,
    ingress_enabled: bool,
    allowed_channels: set[str],
) -> dict[str, Any]:
    """Route a validated surface event into task/inbox outcomes."""
    if not ingress_enabled:
        raise SurfaceIngressDisabledError("Surface ingress is disabled")

    adapter = str(event["source"].get("adapter", "")).strip()
    if not adapter:
        raise SurfaceIngressError("'source.adapter' must be set")
    if allowed_channels and adapter not in allowed_channels:
        raise SurfaceAdapterNotAllowedError(
            f"Adapter '{adapter}' is not enabled for surface ingress"
        )

    idem_key = _idempotency_key(event)
    idem_hash = _payload_hash(event)
    store = _load_idempotency_store(state_file)
    existing = store.get(idem_key)
    if isinstance(existing, dict):
        existing_hash = existing.get("payload_hash")
        if existing_hash == idem_hash:
            return {
                "event_id": event["event_id"],
                "session_id": event["session"]["session_id"],
                "decision": existing.get("decision", "duplicate"),
                "idempotent": True,
                "status": "accepted_noop",
                "task_id": existing.get("task_id"),
                "message_id": existing.get("message_id"),
            }
        raise SurfaceIdempotencyConflictError(
            f"Idempotency key '{idem_key}' was already used for a different payload"
        )

    decision, reason = _derive_decision(event)
    task_id = None
    message_id = None

    try:
        if decision == "create_task":
            title, description, priority = _build_task_fields(event)
            tasks = await load_tasks(state_file)
            task = create_task(
                title=title,
                description=description,
                priority=priority,
                max_heartbeats=10,
            )
            tasks.append(task)
            await save_tasks(tasks, state_file)
            enqueue_event(
                "task_created",
                {
                    "task_id": task.id,
                    "source": "surface_ingress",
                    "event_id": event["event_id"],
                    "session_id": event["session"]["session_id"],
                },
            )
            task_id = task.id
        else:
            messages = await load_outbox(state_file)
            text = str(event["payload"].get("text", "")).strip() or "(empty message)"
            msg = create_message(
                "fyi",
                title=f"Surface message ({event['source'].get('adapter', 'unknown')})",
                body=text,
                urgency="normal",
                requires_response=bool(event.get("routing", {}).get("requires_reply", False)),
                payload={
                    "surface_event_id": event["event_id"],
                    "session_id": event["session"]["session_id"],
                    "source_adapter": event["source"].get("adapter"),
                    "event_type": event["event_type"],
                },
            )
            append_message(messages, msg)
            await save_outbox(messages, state_file)
            message_id = msg.id
    except Exception:
        logger.exception("Surface bridge failed for event '%s'", event["event_id"])
        messages = await load_outbox(state_file)
        alert = create_message(
            "alert",
            title="Surface ingress bridge failure",
            body=f"Failed to bridge event {event['event_id']} ({event['source'].get('adapter', 'unknown')}).",
            urgency="high",
            payload={
                "surface_event_id": event["event_id"],
                "session_id": event["session"]["session_id"],
                "trace_id": event.get("meta", {}).get("trace_id"),
            },
        )
        append_message(messages, alert)
        await save_outbox(messages, state_file)
        decision = "escalate_operator"
        reason = "bridge failure escalated to operator inbox"
        message_id = alert.id

    store[idem_key] = {
        "event_id": event["event_id"],
        "session_id": event["session"]["session_id"],
        "decision": decision,
        "payload_hash": idem_hash,
        "task_id": task_id,
        "message_id": message_id,
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_idempotency_store(state_file, store)

    return {
        "event_id": event["event_id"],
        "session_id": event["session"]["session_id"],
        "decision": decision,
        "idempotent": False,
        "status": "accepted",
        "reason": reason,
        "task_id": task_id,
        "message_id": message_id,
    }
