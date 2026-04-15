"""Tests for copilot_auth — token exchange and caching logic."""

from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch

import pytest

from copilot_auth import (
    COPILOT_BASE_URL,
    COPILOT_DEFAULT_HEADERS,
    CopilotTokenManager,
    _get_github_token,
)


# ─── _get_github_token ───────────────────────────────────────────────


class TestGetGithubToken:
    """Tests for the token resolution helper."""

    def test_prefers_env_var(self, monkeypatch):
        monkeypatch.setenv("GITHUB_PAT", "ghp_test123")
        assert _get_github_token() == "ghp_test123"

    def test_strips_whitespace(self, monkeypatch):
        monkeypatch.setenv("GITHUB_PAT", "  ghp_padded  \n")
        assert _get_github_token() == "ghp_padded"

    @patch("copilot_auth.shutil.which", return_value=None)
    def test_raises_when_no_pat_and_no_gh(self, mock_which, monkeypatch):
        monkeypatch.delenv("GITHUB_PAT", raising=False)
        with pytest.raises(RuntimeError, match="No GitHub token"):
            _get_github_token()

    @patch("copilot_auth.subprocess.run")
    @patch("copilot_auth.shutil.which", return_value="/usr/bin/gh")
    def test_falls_back_to_gh_cli(self, mock_which, mock_run, monkeypatch):
        monkeypatch.delenv("GITHUB_PAT", raising=False)
        mock_run.return_value = MagicMock(returncode=0, stdout="gho_from_cli\n")
        assert _get_github_token() == "gho_from_cli"
        mock_run.assert_called_once()

    @patch("copilot_auth.subprocess.run")
    @patch("copilot_auth.shutil.which", return_value="/usr/bin/gh")
    def test_raises_when_gh_not_authenticated(self, mock_which, mock_run, monkeypatch):
        monkeypatch.delenv("GITHUB_PAT", raising=False)
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        with pytest.raises(RuntimeError, match="not authenticated"):
            _get_github_token()


# ─── CopilotTokenManager ──────────────────────────────────────────


class TestCopilotTokenManager:
    """Tests for session token exchange and caching."""

    def _mock_exchange(self, mgr, token="session_tok", ttl=300):
        """Patch _exchange to inject a fake session token."""
        mgr._session_token = token
        mgr._expires_at = time.time() + ttl

    def test_constructor_accepts_explicit_token(self):
        mgr = CopilotTokenManager(github_token="ghp_explicit")
        assert mgr._github_token == "ghp_explicit"

    @patch("copilot_auth.httpx.get")
    def test_exchange_sets_token(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"token": "sess_abc", "expires_at": time.time() + 1800},
            raise_for_status=lambda: None,
        )
        mgr = CopilotTokenManager(github_token="ghp_test")
        token = mgr.get_token()
        assert token == "sess_abc"
        mock_get.assert_called_once()

    @patch("copilot_auth.httpx.get")
    def test_caches_token_within_ttl(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"token": "sess_cached", "expires_at": time.time() + 1800},
            raise_for_status=lambda: None,
        )
        mgr = CopilotTokenManager(github_token="ghp_test")
        t1 = mgr.get_token()
        t2 = mgr.get_token()  # should reuse, not call exchange again
        assert t1 == t2 == "sess_cached"
        assert mock_get.call_count == 1

    @patch("copilot_auth.httpx.get")
    def test_refreshes_when_near_expiry(self, mock_get):
        call_count = 0

        def fake_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return MagicMock(
                status_code=200,
                json=lambda: {
                    "token": f"sess_{call_count}",
                    "expires_at": time.time() + 1800,
                },
                raise_for_status=lambda: None,
            )

        mock_get.side_effect = fake_get
        mgr = CopilotTokenManager(github_token="ghp_test")
        mgr.get_token()
        # Simulate token about to expire
        mgr._expires_at = time.time() + 10  # within 60s margin
        tok = mgr.get_token()
        assert tok == "sess_2"
        assert call_count == 2

    @patch("copilot_auth.httpx.get")
    def test_fallback_ttl_when_no_expires_at(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"token": "sess_no_exp"},
            raise_for_status=lambda: None,
        )
        mgr = CopilotTokenManager(github_token="ghp_test")
        mgr.get_token()
        # Should be ~28 minutes from now
        remaining = mgr._expires_at - time.time()
        assert 1600 < remaining < 1700  # ~28 min ± margin


# ─── Constants ────────────────────────────────────────────────────


class TestConstants:
    def test_base_url(self):
        assert COPILOT_BASE_URL == "https://api.githubcopilot.com"

    def test_default_headers_has_integration_id(self):
        assert "Copilot-Integration-Id" in COPILOT_DEFAULT_HEADERS
        assert COPILOT_DEFAULT_HEADERS["Copilot-Integration-Id"] == "vscode-chat"

    def test_default_headers_has_user_agent(self):
        assert "User-Agent" in COPILOT_DEFAULT_HEADERS


# ─── Config integration ──────────────────────────────────────────


class TestConfigIntegration:
    _PROVIDER_ENV_KEYS = (
        "PROVIDER",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "OPENROUTER_API_KEY",
        "OLLAMA_HOST",
        "OLLAMA_MODEL",
        "GITHUB_COPILOT_MODEL",
        "AZURE_AI_MODEL_DEPLOYMENT_NAME",
    )

    @staticmethod
    def _clear_provider_env(monkeypatch):
        for key in TestConfigIntegration._PROVIDER_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)

    def test_config_has_github_pat_field(self):
        from config import AppConfig
        cfg = AppConfig()
        assert hasattr(cfg, "github_pat")
        assert cfg.github_pat == ""

    def test_load_config_reads_github_pat(self, monkeypatch, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("GITHUB_PAT=ghp_from_env\n")
        from config import load_config
        cfg = load_config(str(env_file))
        assert cfg.github_pat == "ghp_from_env"

    def test_provider_switch_uses_env_only_for_openai(self, tmp_path, monkeypatch):
        self._clear_provider_env(monkeypatch)
        env_file = tmp_path / ".env"
        env_file.write_text(
            "PROVIDER=openai\n"
            "OPENAI_API_KEY=test-key\n"
            "OPENAI_MODEL=gpt-4o-mini\n"
        )
        from config import load_config, validate_config
        cfg = load_config(str(env_file))
        assert cfg.provider == "openai"
        assert cfg.openai_api_key == "test-key"
        assert validate_config(cfg) == []

    def test_provider_switch_uses_env_only_for_ollama(self, tmp_path, monkeypatch):
        self._clear_provider_env(monkeypatch)
        env_file = tmp_path / ".env"
        env_file.write_text(
            "PROVIDER=ollama\n"
            "OLLAMA_HOST=http://localhost:11434\n"
            "OLLAMA_MODEL=llama3\n"
        )
        from config import load_config, validate_config
        cfg = load_config(str(env_file))
        assert cfg.provider == "ollama"
        assert cfg.ollama_host == "http://localhost:11434"
        assert cfg.model == "llama3"
        assert validate_config(cfg) == []
