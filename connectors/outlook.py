"""Outlook / Exchange connector via Microsoft Graph API.

Sends email from a service mailbox and reads unread replies — enabling
the agent to communicate with humans via email (standup summaries,
blocked questions, task completions, etc.).

Configuration (.env)::

    MS_TENANT_ID         Azure AD tenant ID.
    MS_CLIENT_ID         Azure AD app client ID.
    MS_CLIENT_SECRET     Azure AD app client secret.
    OUTLOOK_SENDER       UPN of the mailbox to send from, e.g.
                         "natl-agent@company.com".
                         The app needs *Mail.Send* application permission
                         on this mailbox (or use admin consent for all users).
    OUTLOOK_REPLY_TO     Optional reply-to address shown on outgoing mail.

Azure AD permissions required (application, not delegated)::

    Mail.Send            Send as / on behalf of the mailbox.
    Mail.Read            Read messages from the mailbox.
    Mail.ReadWrite       Mark messages as read (optional).

All HTTP calls are synchronous/blocking — wrap in ``asyncio.to_thread``
when calling from async contexts.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from .base import ConnectorStatus

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"


# ── Data model ─────────────────────────────────────────────────────────

@dataclass
class Email:
    """A received email message."""

    id: str
    subject: str
    sender: str
    sender_email: str
    body: str                   # plain-text extracted body
    body_html: str              # raw HTML body
    received_at: str            # ISO datetime string
    is_read: bool = False
    conversation_id: str = ""
    reply_to: str = ""
    attachments: list[str] = field(default_factory=list)


# ── Connector ──────────────────────────────────────────────────────────

class OutlookConnector:
    """Send and receive email via Microsoft Graph.

    Parameters
    ----------
    tenant_id / client_id / client_secret:
        Azure AD credentials for client-credentials flow.
    sender:
        UPN of the mailbox to send from / read from.
    reply_to:
        Optional reply-to address on outgoing mail.
    """

    def __init__(
        self,
        tenant_id: str = "",
        client_id: str = "",
        client_secret: str = "",
        sender: str = "",
        reply_to: str = "",
    ) -> None:
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._sender = sender
        self._reply_to = reply_to

    @property
    def _enabled(self) -> bool:
        return bool(
            self._tenant_id
            and self._client_id
            and self._client_secret
            and self._sender
        )

    # ── Health ─────────────────────────────────────────────────────────

    def health_check(self) -> ConnectorStatus:
        if not self._enabled:
            return ConnectorStatus(
                "outlook", enabled=False, healthy=False,
                error="MS Graph credentials or OUTLOOK_SENDER not configured",
            )
        try:
            token = self._token()
            # Lightweight probe: fetch mailbox display name
            url = f"{_GRAPH_BASE}/users/{urllib.parse.quote(self._sender)}"
            _graph_get(url, token)
            return ConnectorStatus("outlook", enabled=True, healthy=True)
        except Exception as exc:
            return ConnectorStatus("outlook", enabled=True, healthy=False, error=str(exc))

    # ── Send ───────────────────────────────────────────────────────────

    def send_email(
        self,
        to: list[str] | str,
        subject: str,
        body: str,
        html: bool = True,
        cc: list[str] | None = None,
    ) -> bool:
        """Send an email from the configured service mailbox.

        Parameters
        ----------
        to:
            Recipient address(es).
        subject:
            Email subject line.
        body:
            Message body (HTML when ``html=True``, plain text otherwise).
        html:
            When True, ``body`` is treated as HTML content.
        cc:
            Optional CC address(es).
        """
        if not self._enabled:
            logger.debug("[outlook] send_email: connector not configured")
            return False

        to_list = [to] if isinstance(to, str) else to
        cc_list = cc or []

        def _addr(email: str) -> dict:
            return {"emailAddress": {"address": email}}

        message: dict = {
            "subject": subject,
            "body": {
                "contentType": "HTML" if html else "Text",
                "content": body,
            },
            "toRecipients": [_addr(a) for a in to_list],
        }
        if cc_list:
            message["ccRecipients"] = [_addr(a) for a in cc_list]
        if self._reply_to:
            message["replyTo"] = [_addr(self._reply_to)]

        try:
            token = self._token()
            url = f"{_GRAPH_BASE}/users/{urllib.parse.quote(self._sender)}/sendMail"
            _graph_post(url, {"message": message, "saveToSentItems": True}, token)
            logger.debug("[outlook] Email sent to %s — %r", to_list, subject)
            return True
        except Exception as exc:
            logger.warning("[outlook] send_email failed: %s", exc)
            return False

    def send_standup_email(
        self,
        recipients: list[str],
        entries: list[dict],
    ) -> bool:
        """Send a standup report as a formatted HTML email.

        Parameters
        ----------
        recipients:
            Email addresses to send to.
        entries:
            Same format as ``TeamsConnector.send_standup_report``.
        """
        from datetime import datetime, timezone
        _now = datetime.now(timezone.utc)
        date_str = _now.strftime("%A, %B") + f" {_now.day}, {_now.year}"
        subject = f"🤖 Daily Standup — {date_str}"
        body = _standup_html(entries)
        return self.send_email(recipients, subject, body, html=True)

    # ── Read / inbox ───────────────────────────────────────────────────

    def get_unread_emails(
        self,
        folder: str = "Inbox",
        subject_contains: str = "",
        limit: int = 50,
    ) -> list[Email]:
        """Return unread messages from the mailbox.

        Parameters
        ----------
        folder:
            Folder name or well-known name (``"Inbox"``, ``"SentItems"``).
        subject_contains:
            Optional substring filter on subject (case-insensitive).
        limit:
            Maximum number of messages to return.
        """
        if not self._enabled:
            return []
        try:
            token = self._token()
            filters = ["isRead eq false"]
            if subject_contains:
                filters.append(
                    f"contains(tolower(subject), '{subject_contains.lower()}')"
                )
            params = urllib.parse.urlencode({
                "$filter": " and ".join(filters),
                "$select": (
                    "id,subject,from,body,receivedDateTime,"
                    "isRead,conversationId,replyTo"
                ),
                "$top": str(limit),
                "$orderby": "receivedDateTime desc",
            })
            url = (
                f"{_GRAPH_BASE}/users/{urllib.parse.quote(self._sender)}"
                f"/mailFolders/{urllib.parse.quote(folder)}/messages?{params}"
            )
            data = _graph_get(url, token)
            return [_parse_email(m) for m in data.get("value", [])]
        except Exception as exc:
            logger.warning("[outlook] get_unread_emails failed: %s", exc)
            return []

    def reply_to_email(self, message_id: str, body: str, html: bool = True) -> bool:
        """Send a reply to an existing email thread."""
        if not self._enabled:
            return False
        try:
            token = self._token()
            url = (
                f"{_GRAPH_BASE}/users/{urllib.parse.quote(self._sender)}"
                f"/messages/{message_id}/reply"
            )
            payload = {
                "message": {},
                "comment": body,
            }
            _graph_post(url, payload, token)
            return True
        except Exception as exc:
            logger.warning("[outlook] reply_to_email failed: %s", exc)
            return False

    def mark_as_read(self, message_id: str) -> bool:
        """Mark a message as read."""
        if not self._enabled:
            return False
        try:
            token = self._token()
            url = (
                f"{_GRAPH_BASE}/users/{urllib.parse.quote(self._sender)}"
                f"/messages/{message_id}"
            )
            _graph_patch(url, {"isRead": True}, token)
            return True
        except Exception as exc:
            logger.warning("[outlook] mark_as_read failed: %s", exc)
            return False

    # ── Internal ───────────────────────────────────────────────────────

    def _token(self) -> str:
        from .graph_auth import get_graph_token
        return get_graph_token(self._tenant_id, self._client_id, self._client_secret)


# ── Email parser ───────────────────────────────────────────────────────

def _parse_email(raw: dict) -> Email:
    from_info = raw.get("from", {}).get("emailAddress", {})
    reply_to_list = raw.get("replyTo", [])
    reply_to_addr = ""
    if reply_to_list:
        reply_to_addr = reply_to_list[0].get("emailAddress", {}).get("address", "")

    body_html = raw.get("body", {}).get("content", "")
    body_text = _html_to_text(body_html)

    return Email(
        id=raw.get("id", ""),
        subject=raw.get("subject", ""),
        sender=from_info.get("name", ""),
        sender_email=from_info.get("address", ""),
        body=body_text,
        body_html=body_html,
        received_at=raw.get("receivedDateTime", ""),
        is_read=raw.get("isRead", False),
        conversation_id=raw.get("conversationId", ""),
        reply_to=reply_to_addr,
    )


def _html_to_text(html: str) -> str:
    """Minimal HTML → plain text (no dependencies)."""
    import re
    html = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<[^>]+>", "", html)
    html = re.sub(r"\n{3,}", "\n\n", html.strip())
    return html


# ── Graph HTTP helpers ─────────────────────────────────────────────────

def _graph_get(url: str, token: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
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
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def _graph_patch(url: str, payload: dict, token: str) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="PATCH",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


# ── HTML email formatter ───────────────────────────────────────────────

def _standup_html(entries: list[dict]) -> str:
    """Render a standup report as an HTML email body."""
    from datetime import datetime, timezone
    _now = datetime.now(timezone.utc)
    date_str = _now.strftime("%A, %B") + f" {_now.day}, {_now.year}"

    rows = ""
    for e in entries:
        persona = e.get("persona", "agent")
        yesterday = e.get("yesterday", "—")
        today = e.get("today", "—")
        blockers = e.get("blockers", "") or "None"
        three_amigos: list[str] = e.get("three_amigos", [])

        ta_row = ""
        if three_amigos:
            items = "".join(f"<li>{item}</li>" for item in three_amigos)
            ta_row = (
                f"<tr><td style='padding:4px 8px;color:#b45309;font-weight:bold'>"
                f"⚠️ Three amigos</td>"
                f"<td style='padding:4px 8px'><ul style='margin:0;padding-left:16px'>"
                f"{items}</ul></td></tr>"
            )

        rows += f"""
        <tr>
          <td colspan="2" style="padding:12px 8px 4px;font-weight:bold;
                                  font-size:15px;border-top:1px solid #e5e7eb">
            👤 {persona}
          </td>
        </tr>
        <tr>
          <td style="padding:4px 8px;color:#6b7280;width:120px">Yesterday</td>
          <td style="padding:4px 8px">{yesterday}</td>
        </tr>
        <tr>
          <td style="padding:4px 8px;color:#6b7280">Today</td>
          <td style="padding:4px 8px">{today}</td>
        </tr>
        <tr>
          <td style="padding:4px 8px;color:#6b7280">Blockers</td>
          <td style="padding:4px 8px">{blockers}</td>
        </tr>
        {ta_row}
        """

    return f"""<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;color:#111827;max-width:600px;margin:0 auto">
  <h2 style="color:#1d4ed8">🤖 Daily Standup — {date_str}</h2>
  <table style="width:100%;border-collapse:collapse">
    {rows}
  </table>
  <p style="margin-top:24px;color:#9ca3af;font-size:12px">
    Generated by NATLClaw — autonomous AI coworker
  </p>
</body>
</html>"""


# ── Convenience: build connector from AppConfig ────────────────────────

def connector_from_config(config: "object") -> "OutlookConnector":
    return OutlookConnector(
        tenant_id=getattr(config, "ms_tenant_id", ""),
        client_id=getattr(config, "ms_client_id", ""),
        client_secret=getattr(config, "ms_client_secret", ""),
        sender=getattr(config, "outlook_sender", ""),
        reply_to=getattr(config, "outlook_reply_to", ""),
    )
