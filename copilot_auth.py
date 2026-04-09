"""GitHub Copilot token exchange for CLI usage.

Implements the two-step authentication flow used by CodeIntel:
1. Obtain a GitHub token (from ``gh auth token`` or ``GITHUB_PAT`` env var)
2. Exchange it for a short-lived Copilot session token via the GitHub API
3. Use the session token to call the OpenAI-compatible Copilot endpoint

The session token is cached and automatically refreshed 60 s before expiry.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time

import httpx

_LOGGER = logging.getLogger(__name__)

_TOKEN_EXCHANGE_URL = "https://api.github.com/copilot_internal/v2/token"
COPILOT_BASE_URL = "https://api.githubcopilot.com"
COPILOT_DEFAULT_HEADERS = {
    "Copilot-Integration-Id": "vscode-chat",
    "User-Agent": "NATLClaw/1.0",
}

# How many seconds before expiry to pre-emptively refresh
_REFRESH_MARGIN_SEC = 60
# Fallback TTL when the API doesn't return ``expires_at``
_FALLBACK_TTL_SEC = 28 * 60  # 28 minutes


def _get_github_token() -> str:
    """Resolve a GitHub token from the environment or ``gh`` CLI.

    Priority:
    1. ``GITHUB_PAT`` environment variable
    2. ``gh auth token`` (requires GitHub CLI installed + authenticated)

    Raises:
        RuntimeError: If no token can be obtained.
    """
    pat = os.getenv("GITHUB_PAT", "").strip()
    if pat:
        _LOGGER.debug("Using GITHUB_PAT from environment")
        return pat

    # Try the GitHub CLI
    gh_path = shutil.which("gh")
    if gh_path is None:
        raise RuntimeError(
            "No GitHub token available.  Set the GITHUB_PAT environment "
            "variable or install the GitHub CLI (`gh`) and run `gh auth login`."
        )

    try:
        result = subprocess.run(
            [gh_path, "auth", "token"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            _LOGGER.debug("Using token from `gh auth token`")
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError) as exc:
        _LOGGER.warning("Failed to get token from gh CLI: %s", exc)

    raise RuntimeError(
        "GitHub CLI (`gh`) is installed but not authenticated.  "
        "Run `gh auth login` first, or set GITHUB_PAT in your .env."
    )


class CopilotTokenManager:
    """Manages Copilot session tokens with automatic refresh.

    The token exchange mirrors CodeIntel's ``GitHubCopilotLlmProvider``:

    * ``GET https://api.github.com/copilot_internal/v2/token``
      with ``Authorization: token <github_token>``
    * Returns ``{"token": "...", "expires_at": <unix_ts>}``

    The resulting session token is used as a Bearer token against
    ``https://api.githubcopilot.com/chat/completions``.
    """

    def __init__(self, github_token: str | None = None) -> None:
        self._github_token = github_token or _get_github_token()
        self._session_token: str | None = None
        self._expires_at: float = 0.0  # Unix timestamp

    # -- Token exchange ------------------------------------------------

    def _exchange(self) -> None:
        """Exchange the GitHub token for a Copilot session token."""
        headers = {
            "Authorization": f"token {self._github_token}",
            "Accept": "application/json",
            "User-Agent": "NATLClaw/1.0",
        }
        resp = httpx.get(_TOKEN_EXCHANGE_URL, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        self._session_token = data["token"]

        expires_at = data.get("expires_at")
        if isinstance(expires_at, (int, float)):
            self._expires_at = float(expires_at)
        else:
            self._expires_at = time.time() + _FALLBACK_TTL_SEC

        _LOGGER.info(
            "Copilot session token acquired (expires in %.0f s)",
            self._expires_at - time.time(),
        )

    # -- Public API ----------------------------------------------------

    def get_token(self) -> str:
        """Return a valid session token, refreshing if needed.

        This is designed to be passed as the ``api_key`` callable to
        ``OpenAIChatCompletionClient``.
        """
        if (
            self._session_token is None
            or time.time() >= self._expires_at - _REFRESH_MARGIN_SEC
        ):
            self._exchange()
        assert self._session_token is not None
        return self._session_token
