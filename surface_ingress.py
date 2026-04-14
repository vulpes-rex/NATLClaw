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


def _normalize_canary_event(event: dict[str, Any]) -> dict[str, Any]:
    return event


def _normalize_canary_webhook_event(event: dict[str, Any]) -> dict[str, Any]:
    """Normalize webhook-style canary payloads into surface-event text shape."""
    payload = dict(event.get("payload", {}))
    if not payload.get("text") and isinstance(payload.get("message"), str):
        payload["text"] = payload["message"]
    return {**event, "payload": payload}


_ADAPTER_NORMALIZERS: dict[str, Any] = {
    "canary": _normalize_canary_event,
    "canary_webhook": _normalize_canary_webhook_event,
}


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


def _sessions_store_path(state_file: str) -> str:
    return os.path.join(os.path.dirname(state_file), "surface_sessions.json")


def _routes_store_path(state_file: str) -> str:
    return os.path.join(os.path.dirname(state_file), "surface_routes.json")


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


def _load_json_object(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            return raw
    except (OSError, json.JSONDecodeError, TypeError):
        logger.warning("Failed to load JSON object store from %s", path, exc_info=True)
    return {}


def _load_json_list(path: str) -> list[dict[str, Any]]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, list):
            return [entry for entry in raw if isinstance(entry, dict)]
    except (OSError, json.JSONDecodeError, TypeError):
        logger.warning("Failed to load JSON list store from %s", path, exc_info=True)
    return []


def _save_json_atomic(path: str, value: Any) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    dir_name = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _infer_origin_type(event: dict[str, Any]) -> str:
    session = event.get("session", {})
    source = event.get("source", {})
    if session.get("group_id"):
        return "group"
    if session.get("thread_id"):
        return "api"
    channel_type = str(source.get("channel_type", "")).lower()
    if channel_type in {"webhook", "api"}:
        return channel_type
    return "dm"


def _choose_persona(
    event: dict[str, Any],
    default_persona: str,
    allowed_personas: set[str] | None = None,
) -> tuple[str, bool]:
    routing = event.get("routing", {})
    hinted = routing.get("persona_hint")
    if isinstance(hinted, str) and hinted.strip():
        hinted_name = hinted.strip()
        if allowed_personas and hinted_name not in allowed_personas:
            return default_persona, True
        return hinted_name, False
    return default_persona, False


def _upsert_surface_session(
    event: dict[str, Any],
    *,
    state_file: str,
    persona: str,
) -> dict[str, Any]:
    sessions_path = _sessions_store_path(state_file)
    sessions = _load_json_object(sessions_path)

    session = event["session"]
    source = event["source"]
    session_id = session["session_id"]
    requires_reply = bool(event.get("routing", {}).get("requires_reply", False))

    current = sessions.get(session_id, {})
    if not isinstance(current, dict):
        current = {}
    current.update(
        {
            "session_id": session_id,
            "channel_type": source.get("channel_type"),
            "origin_type": _infer_origin_type(event),
            "active_persona": persona,
            "state": "active",
            "reply_mode": "manual_review" if requires_reply else "auto",
            "last_event_ts": event.get("ts"),
            "last_event_id": event.get("event_id"),
            "last_adapter": source.get("adapter"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    sessions[session_id] = current
    _save_json_atomic(sessions_path, sessions)
    return current


def _append_route_record(
    *,
    state_file: str,
    event: dict[str, Any],
    decision: str,
    reason: str,
    persona: str,
    task_id: str | None,
    message_id: str | None,
    idempotent: bool,
    status: str,
) -> dict[str, Any]:
    routes_path = _routes_store_path(state_file)
    routes = _load_json_list(routes_path)
    route = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_id": event.get("event_id"),
        "session_id": event.get("session", {}).get("session_id"),
        "decision": decision,
        "persona": persona,
        "priority": event.get("routing", {}).get("priority", "normal"),
        "reason": reason,
        "task_id": task_id,
        "message_id": message_id,
        "adapter": event.get("source", {}).get("adapter"),
        "event_type": event.get("event_type"),
        "status": status,
        "idempotent": idempotent,
    }
    routes.append(route)
    if len(routes) > 1000:
        routes = routes[-1000:]
    _save_json_atomic(routes_path, routes)
    return route


def list_surface_sessions(state_file: str) -> list[dict[str, Any]]:
    """Return all known surface sessions sorted by latest event timestamp."""
    sessions = _load_json_object(_sessions_store_path(state_file))
    values = [entry for entry in sessions.values() if isinstance(entry, dict)]
    return sorted(values, key=lambda item: str(item.get("last_event_ts", "")), reverse=True)


def get_surface_session(state_file: str, session_id: str) -> dict[str, Any] | None:
    """Return one session by ID, if present."""
    sessions = _load_json_object(_sessions_store_path(state_file))
    entry = sessions.get(session_id)
    if isinstance(entry, dict):
        return entry
    return None


def list_recent_surface_routes(
    state_file: str,
    *,
    limit: int = 50,
    session_id: str | None = None,
    event_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return recent route records, optionally filtered by session/event."""
    routes = _load_json_list(_routes_store_path(state_file))
    if session_id:
        routes = [r for r in routes if r.get("session_id") == session_id]
    if event_id:
        routes = [r for r in routes if r.get("event_id") == event_id]
    routes = sorted(routes, key=lambda item: str(item.get("timestamp", "")), reverse=True)
    return routes[: max(1, int(limit))]


def get_surface_health(
    state_file: str,
    *,
    ingress_enabled: bool,
    allowed_channels: set[str],
) -> dict[str, Any]:
    """Build a lightweight health snapshot for surface rollout operations."""
    sessions = list_surface_sessions(state_file)
    routes = list_recent_surface_routes(state_file, limit=200)
    accepted = sum(1 for row in routes if row.get("status") == "accepted")
    accepted_noop = sum(1 for row in routes if row.get("status") == "accepted_noop")
    escalated = sum(1 for row in routes if row.get("decision") == "escalate_operator")
    return {
        "ingress_enabled": ingress_enabled,
        "allowed_channels": sorted(allowed_channels),
        "session_count": len(sessions),
        "recent_routes_count": len(routes),
        "recent_accepted_count": accepted,
        "recent_idempotent_noop_count": accepted_noop,
        "recent_escalation_count": escalated,
        "latest_route": routes[0] if routes else None,
    }


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
    default_persona: str = "default",
    allowed_personas: set[str] | None = None,
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
    normalizer = _ADAPTER_NORMALIZERS.get(adapter)
    if callable(normalizer):
        event = normalizer(event)

    idem_key = _idempotency_key(event)
    idem_hash = _payload_hash(event)
    store = _load_idempotency_store(state_file)
    existing = store.get(idem_key)
    if isinstance(existing, dict):
        existing_hash = existing.get("payload_hash")
        if existing_hash == idem_hash:
            chosen_persona, persona_fallback = _choose_persona(
                event,
                default_persona=default_persona,
                allowed_personas=allowed_personas,
            )
            persona = str(existing.get("persona") or chosen_persona)
            reason = "idempotency replay accepted as no-op"
            if persona_fallback:
                reason = "idempotency replay accepted; invalid persona hint fell back to default"
            _upsert_surface_session(
                event,
                state_file=state_file,
                persona=persona,
            )
            _append_route_record(
                state_file=state_file,
                event=event,
                decision=str(existing.get("decision", "duplicate")),
                reason=reason,
                persona=persona,
                task_id=existing.get("task_id"),
                message_id=existing.get("message_id"),
                idempotent=True,
                status="accepted_noop",
            )
            return {
                "event_id": event["event_id"],
                "session_id": event["session"]["session_id"],
                "decision": existing.get("decision", "duplicate"),
                "persona": persona,
                "idempotent": True,
                "status": "accepted_noop",
                "task_id": existing.get("task_id"),
                "message_id": existing.get("message_id"),
                "reason": reason,
            }
        raise SurfaceIdempotencyConflictError(
            f"Idempotency key '{idem_key}' was already used for a different payload"
        )

    decision, reason = _derive_decision(event)
    persona, persona_fallback = _choose_persona(
        event,
        default_persona=default_persona,
        allowed_personas=allowed_personas,
    )
    task_id = None
    message_id = None

    prior_session = get_surface_session(state_file, event["session"]["session_id"]) or {}
    if (
        prior_session.get("state") == "suspended"
        and decision == "create_task"
        and not bool(event.get("routing", {}).get("allow_suspended_override", False))
    ):
        decision = "append_inbox_message"
        reason = "session suspended; task creation suppressed without override"
    if persona_fallback:
        reason = f"{reason}; invalid persona hint fell back to default '{default_persona}'"

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

    _upsert_surface_session(
        event,
        state_file=state_file,
        persona=persona,
    )
    _append_route_record(
        state_file=state_file,
        event=event,
        decision=decision,
        reason=reason,
        persona=persona,
        task_id=task_id,
        message_id=message_id,
        idempotent=False,
        status="accepted",
    )

    store[idem_key] = {
        "event_id": event["event_id"],
        "session_id": event["session"]["session_id"],
        "decision": decision,
        "persona": persona,
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
        "persona": persona,
        "idempotent": False,
        "status": "accepted",
        "reason": reason,
        "task_id": task_id,
        "message_id": message_id,
    }
