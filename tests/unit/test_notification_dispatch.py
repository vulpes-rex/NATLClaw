"""Tests for notification_dispatch.py."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from notification_dispatch import (
    _webhooks_from_config,
    dispatch_message,
    dispatch_new_messages,
    dispatch_to_webhooks,
    urgency_meets_threshold,
    webhook_payload,
)


# ── Fixtures ───────────────────────────────────────────────────────────


@dataclass
class _FakeMessage:
    id: str = "m_test"
    type: str = "alert"
    urgency: str = "high"
    title: str = "Test alert"
    body: str = "Something happened"
    task_id: str = ""
    persona: str = "test_persona"
    heartbeat: int = 1
    created_at: str = "2026-04-13T00:00:00+00:00"
    payload: dict = field(default_factory=dict)


@dataclass
class _FakeConfig:
    notification_webhooks: tuple = ()
    notification_os_toast: bool = False
    notification_min_urgency: str = "normal"


# ── urgency_meets_threshold ────────────────────────────────────────────


class TestUrgencyThreshold:
    @pytest.mark.parametrize("urgency,min_urgency,expected", [
        ("urgent", "normal", True),
        ("high",   "normal", True),
        ("normal", "normal", True),
        ("low",    "normal", False),
        ("urgent", "urgent", True),
        ("high",   "urgent", False),
        ("normal", "high",   False),
        ("low",    "low",    True),
        ("unknown", "normal", True),   # unknown urgency maps to 1 (normal)
    ])
    def test_threshold(self, urgency, min_urgency, expected):
        assert urgency_meets_threshold(urgency, min_urgency) is expected


# ── webhook_payload ────────────────────────────────────────────────────


class TestWebhookPayload:
    def test_shape(self):
        msg = _FakeMessage()
        p = webhook_payload(msg)
        assert p["id"] == "m_test"
        assert p["urgency"] == "high"
        assert p["title"] == "Test alert"
        assert p["persona"] == "test_persona"
        assert "payload" in p

    def test_serialisable(self):
        msg = _FakeMessage(payload={"key": "val"})
        p = webhook_payload(msg)
        json.dumps(p)  # must not raise


# ── _webhooks_from_config ──────────────────────────────────────────────


class TestWebhooksFromConfig:
    def test_tuple(self):
        cfg = _FakeConfig(notification_webhooks=("https://a.io", "https://b.io"))
        assert _webhooks_from_config(cfg) == ["https://a.io", "https://b.io"]

    def test_empty_tuple(self):
        cfg = _FakeConfig(notification_webhooks=())
        assert _webhooks_from_config(cfg) == []

    def test_string(self):
        cfg = _FakeConfig()
        cfg.notification_webhooks = "https://a.io,https://b.io"  # type: ignore
        result = _webhooks_from_config(cfg)
        assert result == ["https://a.io", "https://b.io"]

    def test_string_strips_spaces(self):
        cfg = _FakeConfig()
        cfg.notification_webhooks = " https://a.io , https://b.io "  # type: ignore
        result = _webhooks_from_config(cfg)
        assert result == ["https://a.io", "https://b.io"]

    def test_missing_attribute(self):
        class _Bare:
            pass
        assert _webhooks_from_config(_Bare()) == []


# ── dispatch_to_webhooks ───────────────────────────────────────────────


class TestDispatchToWebhooks:
    def test_no_urls_noop(self):
        msg = _FakeMessage()
        asyncio.run(dispatch_to_webhooks(msg, []))  # must not raise

    def test_posts_to_url(self):
        msg = _FakeMessage()
        calls = []

        def _fake_post(url, body, timeout):
            calls.append((url, json.loads(body)))

        with patch("notification_dispatch._post_webhook_sync", side_effect=_fake_post):
            asyncio.run(dispatch_to_webhooks(msg, ["https://hook.test/notify"]))

        assert len(calls) == 1
        url, body = calls[0]
        assert url == "https://hook.test/notify"
        assert body["id"] == "m_test"
        assert body["urgency"] == "high"

    def test_posts_to_multiple_urls(self):
        msg = _FakeMessage()
        calls = []

        def _fake_post(url, body, timeout):
            calls.append(url)

        with patch("notification_dispatch._post_webhook_sync", side_effect=_fake_post):
            asyncio.run(dispatch_to_webhooks(
                msg, ["https://a.io/hook", "https://b.io/hook"]
            ))

        assert len(calls) == 2

    def test_webhook_error_does_not_raise(self):
        msg = _FakeMessage()

        def _raise(url, body, timeout):
            raise RuntimeError("network down")

        with patch("notification_dispatch._post_webhook_sync", side_effect=_raise):
            # Should not propagate exception
            asyncio.run(dispatch_to_webhooks(msg, ["https://hook.test/notify"]))


# ── dispatch_message ───────────────────────────────────────────────────


class TestDispatchMessage:
    def test_below_threshold_does_not_dispatch(self):
        msg = _FakeMessage(urgency="low")
        cfg = _FakeConfig(
            notification_webhooks=("https://hook.test/notify",),
            notification_min_urgency="high",
        )
        calls = []

        def _fake_post(url, body, timeout):
            calls.append(url)

        with patch("notification_dispatch._post_webhook_sync", side_effect=_fake_post):
            asyncio.run(dispatch_message(msg, cfg))

        assert calls == []

    def test_at_threshold_dispatches(self):
        msg = _FakeMessage(urgency="high")
        cfg = _FakeConfig(
            notification_webhooks=("https://hook.test/notify",),
            notification_min_urgency="high",
        )
        calls = []

        def _fake_post(url, body, timeout):
            calls.append(url)

        with patch("notification_dispatch._post_webhook_sync", side_effect=_fake_post):
            loop = asyncio.new_event_loop()
            loop.run_until_complete(dispatch_message(msg, cfg))
            # Drain pending tasks created by create_task
            loop.run_until_complete(asyncio.sleep(0))
            loop.run_until_complete(asyncio.sleep(0))
            loop.close()

        assert len(calls) == 1

    def test_os_toast_called_when_enabled(self):
        msg = _FakeMessage(urgency="high")
        cfg = _FakeConfig(
            notification_webhooks=(),
            notification_os_toast=True,
            notification_min_urgency="normal",
        )
        with patch("notification_dispatch._try_os_toast", return_value=True) as mock_toast:
            asyncio.run(dispatch_message(msg, cfg))
        mock_toast.assert_called_once_with(msg.title, msg.body)

    def test_os_toast_not_called_when_disabled(self):
        msg = _FakeMessage(urgency="high")
        cfg = _FakeConfig(notification_os_toast=False)
        with patch("notification_dispatch._try_os_toast") as mock_toast:
            asyncio.run(dispatch_message(msg, cfg))
        mock_toast.assert_not_called()


# ── dispatch_new_messages ─────────────────────────────────────────────


class TestDispatchNewMessages:
    def test_empty_list_noop(self):
        cfg = _FakeConfig()
        asyncio.run(dispatch_new_messages([], cfg))  # must not raise

    def test_dispatches_all_messages(self):
        msgs = [_FakeMessage(id=f"m_{i}", urgency="high") for i in range(3)]
        cfg = _FakeConfig(
            notification_webhooks=("https://hook.test/notify",),
            notification_min_urgency="normal",
        )
        calls = []

        def _fake_post(url, body, timeout):
            calls.append(json.loads(body)["id"])

        with patch("notification_dispatch._post_webhook_sync", side_effect=_fake_post):
            loop = asyncio.new_event_loop()
            loop.run_until_complete(dispatch_new_messages(msgs, cfg))
            loop.run_until_complete(asyncio.sleep(0))
            loop.run_until_complete(asyncio.sleep(0))
            loop.close()

        assert sorted(calls) == ["m_0", "m_1", "m_2"]

    def test_filters_by_urgency(self):
        msgs = [
            _FakeMessage(id="m_low",  urgency="low"),
            _FakeMessage(id="m_high", urgency="high"),
        ]
        cfg = _FakeConfig(
            notification_webhooks=("https://hook.test/notify",),
            notification_min_urgency="high",
        )
        calls = []

        def _fake_post(url, body, timeout):
            calls.append(json.loads(body)["id"])

        with patch("notification_dispatch._post_webhook_sync", side_effect=_fake_post):
            loop = asyncio.new_event_loop()
            loop.run_until_complete(dispatch_new_messages(msgs, cfg))
            loop.run_until_complete(asyncio.sleep(0))
            loop.run_until_complete(asyncio.sleep(0))
            loop.close()

        assert calls == ["m_high"]


# ── Config integration ────────────────────────────────────────────────


class TestConfigIntegration:
    def test_load_config_defaults(self):
        from config import AppConfig
        cfg = AppConfig()
        assert cfg.notification_webhooks == ()
        assert cfg.notification_os_toast is False
        assert cfg.notification_min_urgency == "normal"

    def test_load_config_from_env(self, monkeypatch):
        monkeypatch.setenv("NOTIFICATION_WEBHOOKS", "https://a.io,https://b.io")
        monkeypatch.setenv("NOTIFICATION_OS_TOAST", "true")
        monkeypatch.setenv("NOTIFICATION_MIN_URGENCY", "high")
        from config import load_config
        cfg = load_config(".env.nonexistent")
        assert "https://a.io" in cfg.notification_webhooks
        assert "https://b.io" in cfg.notification_webhooks
        assert cfg.notification_os_toast is True
        assert cfg.notification_min_urgency == "high"


# ── Move C: webhook_payload routing fields ────────────────────────────


@dataclass
class _FakeInboundMessage(_FakeMessage):
    """Like _FakeMessage but with Move A routing fields."""
    sender: str = "developer"
    addressed_to: str = "workspace_observer"
    thread_id: str = "m_root"
    reply_to: str = ""


class TestWebhookPayloadRoutingFields:
    def test_sender_included_when_set(self):
        msg = _FakeInboundMessage(sender="developer")
        p = webhook_payload(msg)
        assert p["sender"] == "developer"

    def test_addressed_to_included_when_set(self):
        msg = _FakeInboundMessage(addressed_to="workspace_observer")
        p = webhook_payload(msg)
        assert p["addressed_to"] == "workspace_observer"

    def test_thread_id_included_when_set(self):
        msg = _FakeInboundMessage(thread_id="m_root")
        p = webhook_payload(msg)
        assert p["thread_id"] == "m_root"

    def test_reply_to_included_when_set(self):
        msg = _FakeInboundMessage(reply_to="m_original")
        p = webhook_payload(msg)
        assert p["reply_to"] == "m_original"

    def test_routing_fields_omitted_when_empty(self):
        """Legacy messages without routing fields don't add empty keys."""
        msg = _FakeMessage()  # no sender/addressed_to/thread_id/reply_to attrs
        p = webhook_payload(msg)
        assert "sender" not in p
        assert "addressed_to" not in p
        assert "thread_id" not in p
        assert "reply_to" not in p

    def test_empty_routing_field_omitted(self):
        """routing fields that are empty strings are omitted."""
        msg = _FakeInboundMessage(
            sender="",
            addressed_to="",
            thread_id="",
            reply_to="",
        )
        p = webhook_payload(msg)
        assert "sender" not in p
        assert "addressed_to" not in p
        assert "thread_id" not in p
        assert "reply_to" not in p

    def test_base_fields_always_present(self):
        """Core fields never removed by routing logic."""
        msg = _FakeInboundMessage()
        p = webhook_payload(msg)
        for key in ("id", "type", "urgency", "title", "body", "task_id",
                    "persona", "heartbeat", "created_at", "payload"):
            assert key in p

    def test_webhook_body_serialisable_with_routing(self):
        """Resulting payload can always be JSON-serialised."""
        msg = _FakeInboundMessage(
            sender="developer",
            addressed_to="obs",
            thread_id="th1",
            reply_to="m1",
        )
        import json as _json
        p = webhook_payload(msg)
        body = _json.dumps(p)  # must not raise
        assert "developer" in body
        assert "obs" in body
