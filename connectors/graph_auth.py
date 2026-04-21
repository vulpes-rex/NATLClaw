"""Microsoft Graph API authentication helper.

Uses the OAuth 2.0 *client credentials* flow (daemon / service scenario).
No extra dependencies — pure stdlib ``urllib``.

Azure AD setup (one-time, done by an admin):
    1. Register an app in Azure AD (Entra ID).
    2. Under *API permissions* add **application** (not delegated) permissions:
           Mail.Send, Mail.Read, ChannelMessage.Send
    3. Click *Grant admin consent*.
    4. Under *Certificates & secrets* create a client secret.
    5. Note the Tenant ID, Client ID, and Client Secret.
    6. Set in .env:  MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET

Tokens are cached in memory and refreshed 60 s before expiry.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_GRAPH_SCOPE = "https://graph.microsoft.com/.default"


@dataclass
class _TokenEntry:
    access_token: str
    expires_at: float  # Unix epoch seconds


class GraphTokenCache:
    """In-memory token cache — one entry per (tenant, client) pair."""

    def __init__(self) -> None:
        self._cache: dict[str, _TokenEntry] = {}

    def get(self, tenant_id: str, client_id: str, client_secret: str) -> str:
        """Return a valid access token, fetching a fresh one when needed."""
        key = f"{tenant_id}:{client_id}"
        entry = self._cache.get(key)
        # Refresh 60 s before real expiry so we never send a stale token
        if entry and time.time() < entry.expires_at - 60:
            return entry.access_token
        data = _fetch_token(tenant_id, client_id, client_secret, _GRAPH_SCOPE)
        expires_in = float(data.get("expires_in", 3599))
        self._cache[key] = _TokenEntry(
            access_token=data["access_token"],
            expires_at=time.time() + expires_in,
        )
        logger.debug("Graph token acquired for tenant %s (expires in %ss)", tenant_id, int(expires_in))
        return self._cache[key].access_token

    def invalidate(self, tenant_id: str, client_id: str) -> None:
        """Force a re-fetch on the next call (e.g. after a 401)."""
        self._cache.pop(f"{tenant_id}:{client_id}", None)


# Module-level singleton — shared across all connectors in the process
_default_cache = GraphTokenCache()


def get_graph_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    """Get a valid Microsoft Graph bearer token (cached, auto-refresh)."""
    return _default_cache.get(tenant_id, client_id, client_secret)


def invalidate_graph_token(tenant_id: str, client_id: str) -> None:
    """Evict a cached token so it will be refreshed on the next request."""
    _default_cache.invalidate(tenant_id, client_id)


# ── Internal token fetch ───────────────────────────────────────────────

def _fetch_token(
    tenant_id: str,
    client_id: str,
    client_secret: str,
    scope: str,
) -> dict:
    """POST to the Azure AD token endpoint and return the JSON response."""
    url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": scope,
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Graph token fetch failed (HTTP {exc.code}): {body_text}"
        ) from exc
