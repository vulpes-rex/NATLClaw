"""Proactive notification dispatch — push outbox messages to multiple channels.

Zero new required dependencies: HTTP uses stdlib ``urllib``, OS toasts use
``plyer`` when installed (optional).  All dispatches are fire-and-forget so
they never block the scheduler.

Channels (configured via AppConfig / .env):

    NOTIFICATION_WEBHOOKS       Comma-separated HTTP(S) URLs to POST to.
    NOTIFICATION_OS_TOAST       "true" / "false" — show native desktop toast.
    NOTIFICATION_MIN_URGENCY    Minimum urgency to dispatch: low | normal | high | urgent
                                Default: "normal".
    TEAMS_WEBHOOK_URL           Post notifications as Teams Adaptive Cards.
    OUTLOOK_SENDER + MS_*       Send notifications as Outlook email (Graph API).

Webhook payload::

    {
      "id":         "m_abc123",
      "type":       "alert",
      "urgency":    "high",
      "title":      "Task completed: …",
      "body":       "…",
      "task_id":    "t_xyz",
      "persona":    "workspace_observer",
      "created_at": "2026-04-13T…",
      "payload":    { … }
    }
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config import AppConfig
    from messaging import Message

logger = logging.getLogger(__name__)

_URGENCY_RANK: dict[str, int] = {
    "low": 0,
    "normal": 1,
    "high": 2,
    "urgent": 3,
}


# ── Urgency filtering ─────────────────────────────────────────────────


def urgency_meets_threshold(urgency: str, min_urgency: str) -> bool:
    """True when *urgency* is at or above *min_urgency*."""
    return _URGENCY_RANK.get(urgency, 1) >= _URGENCY_RANK.get(min_urgency, 1)


# ── Serialisation ──────────────────────────────────────────────────────


def webhook_payload(message: "Message") -> dict:
    """Convert a Message to the standard webhook JSON payload.

    Move C: routing fields (sender, addressed_to, thread_id, reply_to) are
    included when non-empty so webhook consumers can filter/route inbound messages.
    """
    data: dict = {
        "id": message.id,
        "type": message.type,
        "urgency": message.urgency,
        "title": message.title,
        "body": message.body,
        "task_id": message.task_id,
        "persona": message.persona,
        "heartbeat": message.heartbeat,
        "created_at": message.created_at,
        "payload": message.payload,
    }
    # Include Move A routing fields only when present (stays backward-compat)
    for field in ("sender", "addressed_to", "thread_id", "reply_to"):
        val = getattr(message, field, "")
        if val:
            data[field] = val
    return data


# ── Webhook delivery ───────────────────────────────────────────────────


def _post_webhook_sync(url: str, body: bytes, timeout: float) -> None:
    """Blocking POST using stdlib (called via asyncio.to_thread)."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "NATLClaw-notify/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            if status >= 400:
                logger.warning("Webhook %s returned HTTP %s", url, status)
            else:
                logger.debug("Webhook %s → HTTP %s", url, status)
    except urllib.error.URLError as exc:
        logger.warning("Webhook POST to %s failed: %s", url, exc)
    except Exception as exc:
        logger.warning("Webhook POST to %s unexpected error: %s", url, exc)


async def _post_webhook_async(url: str, payload: dict, timeout: float) -> None:
    """Fire a single webhook POST without blocking the event loop."""
    try:
        body = json.dumps(payload, ensure_ascii=False).encode()
        await asyncio.to_thread(_post_webhook_sync, url, body, timeout)
    except Exception as exc:
        logger.warning("Webhook dispatch error for %s: %s", url, exc)


async def dispatch_to_webhooks(
    message: "Message",
    urls: list[str],
    *,
    timeout: float = 5.0,
) -> None:
    """POST *message* to all *urls* concurrently (fire-and-forget friendly)."""
    if not urls:
        return
    payload = webhook_payload(message)
    await asyncio.gather(
        *[_post_webhook_async(url, payload, timeout) for url in urls],
        return_exceptions=True,
    )


# ── OS toast ──────────────────────────────────────────────────────────


def _try_os_toast(title: str, body: str) -> bool:
    """Attempt to show a native desktop notification. Returns True on success."""
    try:
        from plyer import notification  # type: ignore[import]
        notification.notify(
            title=title,
            message=body[:256],
            app_name="NATLClaw",
            timeout=8,
        )
        return True
    except ImportError:
        logger.debug("plyer not installed; OS toast unavailable")
    except Exception as exc:
        logger.debug("OS toast failed: %s", exc)
    return False


# ── Main entry points ──────────────────────────────────────────────────


async def dispatch_to_teams(message: "Message", config: "AppConfig") -> None:
    """Send a message to Teams via the configured connector (fire-and-forget)."""
    webhook_url = getattr(config, "teams_webhook_url", "")
    tenant_id = getattr(config, "ms_tenant_id", "")
    if not webhook_url and not tenant_id:
        return
    try:
        from connectors.teams import TeamsConnector
        conn = TeamsConnector(
            webhook_url=webhook_url,
            tenant_id=tenant_id,
            client_id=getattr(config, "ms_client_id", ""),
            client_secret=getattr(config, "ms_client_secret", ""),
            team_id=getattr(config, "teams_team_id", ""),
            channel_id=getattr(config, "teams_channel_id", ""),
        )
        await asyncio.to_thread(
            conn.send_notification,
            message.title,
            message.body,
            message.urgency,
            message.task_id or "",
            message.persona or "",
        )
    except Exception as exc:
        logger.warning("Teams dispatch error: %s", exc)


async def dispatch_to_outlook(message: "Message", config: "AppConfig") -> None:
    """Send a message via Outlook email (fire-and-forget)."""
    sender = getattr(config, "outlook_sender", "")
    tenant_id = getattr(config, "ms_tenant_id", "")
    recipients = list(getattr(config, "outlook_standup_recipients", ()))
    if not sender or not tenant_id or not recipients:
        return
    try:
        from connectors.outlook import OutlookConnector
        conn = OutlookConnector(
            tenant_id=tenant_id,
            client_id=getattr(config, "ms_client_id", ""),
            client_secret=getattr(config, "ms_client_secret", ""),
            sender=sender,
            reply_to=getattr(config, "outlook_reply_to", ""),
        )
        urgency_prefix = {"urgent": "🔴", "high": "🟡", "normal": "🔵", "low": "⚪"}.get(
            message.urgency, "🔵"
        )
        subject = f"{urgency_prefix} {message.title}"
        body = f"<p>{message.body}</p>"
        if message.task_id:
            body += f"<p><small>Task: {message.task_id} | Persona: {message.persona}</small></p>"
        await asyncio.to_thread(
            conn.send_email,
            recipients,
            subject,
            body,
            True,
        )
    except Exception as exc:
        logger.warning("Outlook dispatch error: %s", exc)


async def dispatch_message(
    message: "Message",
    config: "AppConfig",
) -> None:
    """Dispatch a single message to all configured channels."""
    min_urgency = getattr(config, "notification_min_urgency", "normal")
    if not urgency_meets_threshold(message.urgency, min_urgency):
        return

    webhooks = _webhooks_from_config(config)
    if webhooks:
        asyncio.create_task(dispatch_to_webhooks(message, webhooks))

    if getattr(config, "teams_webhook_url", "") or getattr(config, "ms_tenant_id", ""):
        asyncio.create_task(dispatch_to_teams(message, config))

    if getattr(config, "outlook_sender", "") and getattr(config, "ms_tenant_id", ""):
        asyncio.create_task(dispatch_to_outlook(message, config))

    if getattr(config, "notification_os_toast", False):
        _try_os_toast(message.title, message.body)


async def dispatch_new_messages(
    messages: list["Message"],
    config: "AppConfig",
) -> None:
    """Dispatch each message in *messages* that meets the urgency threshold."""
    if not messages:
        return
    for msg in messages:
        await dispatch_message(msg, config)


# ── Config helper ──────────────────────────────────────────────────────


def _webhooks_from_config(config: "AppConfig") -> list[str]:
    """Return the list of webhook URLs from config."""
    raw = getattr(config, "notification_webhooks", ())
    if isinstance(raw, str):
        # Raw string — split on commas
        return [u.strip() for u in raw.split(",") if u.strip()]
    if isinstance(raw, (list, tuple)):
        return [str(u).strip() for u in raw if str(u).strip()]
    return []
