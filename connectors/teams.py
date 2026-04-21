"""Microsoft Teams connector — send messages and Adaptive Cards.

Two integration modes (can be used independently):

**Webhook mode** (simple, no app registration required):
    Post to an *Incoming Webhook* URL configured in a Teams channel.
    One-way — you can send but not receive.  Zero auth beyond the URL.
    Perfect for standup reports and notifications.

**Graph mode** (full send + receive, requires app registration):
    Uses the Microsoft Graph API to send channel messages and read replies.
    Requires MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET in .env and the
    ``ChannelMessage.Send`` / ``ChannelMessage.Read.All`` application permissions.

Configuration (.env)::

    TEAMS_WEBHOOK_URL    Incoming webhook URL for the target channel.
    TEAMS_TEAM_ID        Teams team ID (for Graph mode reads).
    TEAMS_CHANNEL_ID     Teams channel ID (for Graph mode reads).
    MS_TENANT_ID         Azure AD tenant ID  (Graph mode).
    MS_CLIENT_ID         Azure AD app client ID (Graph mode).
    MS_CLIENT_SECRET     Azure AD app client secret (Graph mode).

Setting only ``TEAMS_WEBHOOK_URL`` enables send-only (webhook) mode with
no Azure AD registration required.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from .base import ConnectorStatus

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_URGENCY_COLOUR = {
    "low": "Good",      # green
    "normal": "Accent", # blue
    "high": "Warning",  # yellow
    "urgent": "Attention",  # red
}


class TeamsConnector:
    """Send messages to Microsoft Teams via webhook and/or Graph API.

    Parameters
    ----------
    webhook_url:
        Incoming webhook URL.  Required for webhook-mode sends.
    tenant_id / client_id / client_secret:
        Azure AD credentials for Graph-mode sends and message reads.
    team_id / channel_id:
        Team and channel identifiers for Graph-mode operations.
    """

    def __init__(
        self,
        webhook_url: str = "",
        tenant_id: str = "",
        client_id: str = "",
        client_secret: str = "",
        team_id: str = "",
        channel_id: str = "",
    ) -> None:
        self._webhook_url = webhook_url
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._team_id = team_id
        self._channel_id = channel_id

    @property
    def _webhook_enabled(self) -> bool:
        return bool(self._webhook_url)

    @property
    def _graph_enabled(self) -> bool:
        return bool(self._tenant_id and self._client_id and self._client_secret)

    # ── Health ─────────────────────────────────────────────────────────

    def health_check(self) -> ConnectorStatus:
        """Verify at least one mode is configured."""
        if not self._webhook_enabled and not self._graph_enabled:
            return ConnectorStatus(
                "teams", enabled=False, healthy=False,
                error="Neither TEAMS_WEBHOOK_URL nor Graph credentials configured",
            )
        if self._webhook_enabled:
            # No cheap health-check for webhooks; report enabled
            return ConnectorStatus("teams", enabled=True, healthy=True)
        # Graph mode: try a lightweight token fetch
        try:
            from .graph_auth import get_graph_token
            get_graph_token(self._tenant_id, self._client_id, self._client_secret)
            return ConnectorStatus("teams", enabled=True, healthy=True)
        except Exception as exc:
            return ConnectorStatus("teams", enabled=True, healthy=False, error=str(exc))

    # ── Simple text message ────────────────────────────────────────────

    def send_message(self, text: str, title: str = "") -> bool:
        """Send a plain text message.

        Uses webhook mode when available; falls back to Graph.
        """
        if self._webhook_enabled:
            return self._webhook_send(_simple_card(title or text, text if title else ""))
        if self._graph_enabled:
            return self._graph_send_message(f"**{title}**\n\n{text}" if title else text)
        logger.debug("[teams] send_message: no transport configured")
        return False

    # ── Notification message ───────────────────────────────────────────

    def send_notification(
        self,
        title: str,
        body: str,
        urgency: str = "normal",
        task_id: str = "",
        persona: str = "",
    ) -> bool:
        """Send a NATLClaw notification as a formatted Teams card."""
        card = _notification_card(title, body, urgency, task_id, persona)
        if self._webhook_enabled:
            return self._webhook_send(card)
        if self._graph_enabled:
            return self._graph_send_message(_card_to_text(title, body, urgency, persona))
        return False

    # ── Standup report card ────────────────────────────────────────────

    def send_standup_report(self, entries: list[dict]) -> bool:
        """Post a standup report as an Adaptive Card.

        Parameters
        ----------
        entries:
            List of dicts with keys: persona, yesterday, today, blockers,
            three_amigos (list[str]).
        """
        card = _standup_card(entries)
        if self._webhook_enabled:
            return self._webhook_send(card)
        if self._graph_enabled:
            text = _standup_to_text(entries)
            return self._graph_send_message(text)
        return False

    # ── Graph: read recent messages ────────────────────────────────────

    def get_recent_messages(self, limit: int = 20) -> list[dict]:
        """Return recent channel messages (Graph mode only).

        Each dict has keys: id, from, body, created_at.
        """
        if not self._graph_enabled or not (self._team_id and self._channel_id):
            return []
        try:
            token = self._graph_token()
            url = (
                f"{_GRAPH_BASE}/teams/{self._team_id}"
                f"/channels/{self._channel_id}/messages"
                f"?$top={limit}&$orderby=createdDateTime desc"
            )
            data = _graph_get(url, token)
            return [
                {
                    "id": m.get("id", ""),
                    "from": (m.get("from") or {}).get("user", {}).get("displayName", ""),
                    "body": (m.get("body") or {}).get("content", ""),
                    "created_at": m.get("createdDateTime", ""),
                }
                for m in data.get("value", [])
            ]
        except Exception as exc:
            logger.warning("[teams] get_recent_messages failed: %s", exc)
            return []

    # ── Internal: webhook delivery ─────────────────────────────────────

    def _webhook_send(self, payload: dict) -> bool:
        """POST an Adaptive Card payload to the incoming webhook URL."""
        try:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                self._webhook_url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "NATLClaw-teams/1.0",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = resp.status
                if status >= 400:
                    logger.warning("[teams] Webhook returned HTTP %s", status)
                    return False
                return True
        except urllib.error.HTTPError as exc:
            logger.warning("[teams] Webhook HTTP error %s: %s", exc.code, exc.read())
            return False
        except Exception as exc:
            logger.warning("[teams] Webhook send failed: %s", exc)
            return False

    # ── Internal: Graph delivery ───────────────────────────────────────

    def _graph_send_message(self, text: str) -> bool:
        """POST a plain-text message to the channel via Graph API."""
        if not (self._team_id and self._channel_id):
            logger.debug("[teams] Graph send: team_id / channel_id not configured")
            return False
        try:
            token = self._graph_token()
            url = (
                f"{_GRAPH_BASE}/teams/{self._team_id}"
                f"/channels/{self._channel_id}/messages"
            )
            payload = {"body": {"contentType": "html", "content": text}}
            _graph_post(url, payload, token)
            return True
        except Exception as exc:
            logger.warning("[teams] Graph send failed: %s", exc)
            return False

    def _graph_token(self) -> str:
        from .graph_auth import get_graph_token
        return get_graph_token(self._tenant_id, self._client_id, self._client_secret)


# ── Card builders ──────────────────────────────────────────────────────

def _simple_card(title: str, body: str) -> dict:
    """Minimal Adaptive Card with a title and body text."""
    return {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.4",
                "body": [
                    {"type": "TextBlock", "weight": "Bolder", "text": title, "wrap": True},
                    {"type": "TextBlock", "text": body, "wrap": True, "spacing": "Medium"},
                ],
            },
        }],
    }


def _notification_card(
    title: str,
    body: str,
    urgency: str = "normal",
    task_id: str = "",
    persona: str = "",
) -> dict:
    """Adaptive Card for a NATLClaw outbox notification."""
    colour = _URGENCY_COLOUR.get(urgency, "Accent")
    facts = []
    if task_id:
        facts.append({"title": "Task", "value": task_id})
    if persona:
        facts.append({"title": "Persona", "value": persona})
    facts.append({"title": "Urgency", "value": urgency.capitalize()})
    facts.append({
        "title": "Time",
        "value": datetime.now(timezone.utc).strftime("%H:%M UTC"),
    })

    card_body: list[dict] = [
        {
            "type": "TextBlock",
            "size": "Medium",
            "weight": "Bolder",
            "color": colour,
            "text": f"🤖 {title}",
            "wrap": True,
        },
        {"type": "TextBlock", "text": body, "wrap": True, "spacing": "Medium"},
    ]
    if facts:
        card_body.append({
            "type": "FactSet",
            "facts": facts,
            "spacing": "Medium",
        })

    return {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.4",
                "body": card_body,
            },
        }],
    }


def _standup_card(entries: list[dict]) -> dict:
    """Adaptive Card containing one section per persona for daily standup.

    Each entry dict:
        persona      str
        yesterday    str
        today        str
        blockers     str  (empty string = none)
        three_amigos list[str]  (empty = none)
    """
    _now = datetime.now(timezone.utc)
    date_str = _now.strftime("%A, %B") + f" {_now.day}"
    card_body: list[dict] = [
        {
            "type": "TextBlock",
            "size": "Large",
            "weight": "Bolder",
            "text": f"🤖 Daily Standup — {date_str}",
        },
        {"type": "Separator"},
    ]

    for entry in entries:
        persona = entry.get("persona", "agent")
        yesterday = entry.get("yesterday", "—")
        today = entry.get("today", "—")
        blockers = entry.get("blockers", "") or "None"
        three_amigos: list[str] = entry.get("three_amigos", [])

        facts: list[dict] = [
            {"title": "Yesterday", "value": yesterday},
            {"title": "Today", "value": today},
            {"title": "Blockers", "value": blockers},
        ]
        if three_amigos:
            facts.append({
                "title": "⚠️ Three amigos",
                "value": "; ".join(three_amigos),
            })

        card_body.append({
            "type": "TextBlock",
            "weight": "Bolder",
            "text": f"👤 {persona}",
            "spacing": "Large",
        })
        card_body.append({"type": "FactSet", "facts": facts})

    return {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.4",
                "body": card_body,
            },
        }],
    }


def _card_to_text(title: str, body: str, urgency: str, persona: str) -> str:
    prefix = {"urgent": "🔴", "high": "🟡", "normal": "🔵", "low": "⚪"}.get(urgency, "🔵")
    parts = [f"{prefix} **{title}**", body]
    if persona:
        parts.append(f"*Persona: {persona}*")
    return "\n\n".join(parts)


def _standup_to_text(entries: list[dict]) -> str:
    _now2 = datetime.now(timezone.utc)
    lines = [f"**🤖 Daily Standup — {_now2.strftime('%A, %B')} {_now2.day}**\n"]
    for e in entries:
        lines.append(f"**{e.get('persona', 'agent')}**")
        lines.append(f"Yesterday: {e.get('yesterday', '—')}")
        lines.append(f"Today: {e.get('today', '—')}")
        lines.append(f"Blockers: {e.get('blockers', '') or 'None'}")
        if e.get("three_amigos"):
            lines.append(f"⚠️ Three amigos: {'; '.join(e['three_amigos'])}")
        lines.append("")
    return "\n".join(lines)


# ── Graph HTTP helpers ─────────────────────────────────────────────────

def _graph_get(url: str, token: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _graph_post(url: str, payload: dict, token: str) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


# ── Convenience: build connector from AppConfig ────────────────────────

def connector_from_config(config: "Any") -> "TeamsConnector":
    return TeamsConnector(
        webhook_url=getattr(config, "teams_webhook_url", ""),
        tenant_id=getattr(config, "ms_tenant_id", ""),
        client_id=getattr(config, "ms_client_id", ""),
        client_secret=getattr(config, "ms_client_secret", ""),
        team_id=getattr(config, "teams_team_id", ""),
        channel_id=getattr(config, "teams_channel_id", ""),
    )
