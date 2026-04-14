"""Tests for api_server.py endpoints not covered by test_api.py.

Covers: OpenAI-compatible endpoints, heartbeat status, scheduler control,
reports, and dashboard.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api_server import create_app
from config import AppConfig
from execution_log import append_entry
from messaging import Message, load_outbox, save_outbox
from tasks import Task, load_tasks, save_tasks


@pytest.fixture(autouse=True)
def _tmp_data(tmp_path, monkeypatch):
    state_file = str(tmp_path / "agent_state.json")
    monkeypatch.setattr(
        "api_server._default_config",
        AppConfig(
            state_file=state_file,
            surface_ingress_enabled=True,
            surface_channels_enabled=("canary",),
        ),
    )
    return tmp_path


@pytest.fixture()
def config():
    import api_server
    return api_server._default_config


@pytest.fixture()
def client(config):
    # Mock the scheduler so the lifespan auto-start doesn't run a real scheduler
    async def _noop_scheduler(*a, **kw):
        await asyncio.sleep(999)

    with patch("scheduler.run_scheduler", side_effect=_noop_scheduler), \
         patch("scheduler.acquire_scheduler_lock", return_value=True), \
         patch("scheduler.release_scheduler_lock"):
        app = create_app(config)
        yield TestClient(app)


@pytest.fixture()
def state_file(config):
    return config.state_file


def _surface_event(
    *,
    event_id: str = "evt_dm_0001",
    adapter: str = "canary",
    text: str = "Please summarize blockers and create follow-up tasks.",
    requires_reply: bool = True,
    idempotency_key: str = "canary:u_42:msg_0001",
) -> dict:
    return {
        "spec_version": "1.0",
        "event_id": event_id,
        "event_type": "message.received",
        "ts": "2026-04-14T20:25:01Z",
        "source": {
            "adapter": adapter,
            "channel_type": "canary",
            "channel_instance": "primary",
        },
        "session": {
            "session_id": "sess_canary_u_42",
            "thread_id": None,
            "user_id": "u_42",
            "group_id": None,
        },
        "routing": {
            "persona_hint": "project_manager",
            "priority": "high",
            "requires_reply": requires_reply,
        },
        "payload": {
            "text": text,
            "attachments": [],
        },
        "meta": {
            "trace_id": "trc_0001",
            "idempotency_key": idempotency_key,
        },
    }


# ── Dashboard ──────────────────────────────────────────────────────────


def test_dashboard_returns_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "NATLClaw Dashboard" in r.text


# ── OpenAI-compatible /v1/models ───────────────────────────────────────


def test_list_models(client):
    r = client.get("/v1/models")
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "list"
    assert isinstance(data["data"], list)
    assert len(data["data"]) > 0
    model = data["data"][0]
    assert "id" in model
    assert model["object"] == "model"
    assert model["owned_by"] == "natlclaw"


# ── Heartbeat status ──────────────────────────────────────────────────


def test_heartbeat_status_never_run(client, state_file):
    r = client.get("/api/heartbeat/status")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] in ("never_run", "stale")
    assert "heartbeat_count" in data


def test_heartbeat_status_active(client, state_file):
    from state import AgentState, save_state
    from datetime import datetime, timezone

    state = AgentState()
    state.last_heartbeat = datetime.now(timezone.utc).isoformat()
    state.execution_count = 5
    asyncio.run(save_state(state, state_file))

    r = client.get("/api/heartbeat/status")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "active"
    assert data["heartbeat_count"] == 5


def test_operator_status_snapshot(client, state_file):
    from state import AgentState, save_state
    from datetime import datetime, timezone

    state = AgentState()
    state.last_heartbeat = datetime.now(timezone.utc).isoformat()
    state.execution_count = 7
    asyncio.run(save_state(state, state_file))

    active = Task(title="Do S6", status="in_progress", priority="high")
    active.assigned_to = "default"
    blocked = Task(title="Need input", status="blocked")
    asyncio.run(save_tasks([active, blocked], state_file))

    unread = Message(type="question", title="Need answer", requires_response=True, status="unread")
    asyncio.run(save_outbox([unread], state_file))

    append_entry(
        "task_execute",
        "do task",
        "ERROR: failed to connect to service",
        db_path=os.path.join(os.path.dirname(state_file), "execution_log.db"),
    )

    r = client.get("/api/status")
    assert r.status_code == 200
    data = r.json()

    assert data["heartbeat"]["count"] == 7
    assert data["tasks"]["active"] is not None
    assert data["tasks"]["active"]["id"] == active.id
    assert data["tasks"]["blocked_count"] == 1
    assert "sla" in data["tasks"]
    assert "at_risk_count" in data["tasks"]["sla"]
    assert "breached_count" in data["tasks"]["sla"]
    assert data["inbox"]["unread_count"] == 1
    assert data["inbox"]["requires_response_count"] == 1
    assert data["errors"]["recent_error_count"] >= 1
    assert data["errors"]["last_error"]["type"] == "network"
    assert data["errors"]["top_error_types"]
    assert data["errors"]["top_error_types"][0]["type"] == "network"
    assert data["reliability"]["status"] in ("healthy", "degraded")
    assert data["reliability"]["window_heartbeats"] == 7
    assert data["reliability"]["recent_error_count"] >= 1


# ── Heartbeat log / metrics ──────────────────────────────────────────


def test_heartbeat_log_empty(client):
    r = client.get("/api/heartbeat/log")
    assert r.status_code == 200
    data = r.json()
    assert "entries" in data
    assert isinstance(data["entries"], list)


# ── Heartbeat activity / execution log ────────────────────────────────


def test_heartbeat_activity_empty(client):
    r = client.get("/api/heartbeat/activity")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ── Scheduler control ─────────────────────────────────────────────────


def test_scheduler_status_not_running(client):
    r = client.get("/api/scheduler/status")
    assert r.status_code == 200
    data = r.json()
    assert data["running"] is False
    assert "lock" in data
    assert data["lock"]["exists"] is False
    assert "control" in data
    assert data["control"]["paused"] is False


def test_scheduler_start(client):
    # Scheduler runs as an asyncio task now, not a subprocess.
    # Mock run_scheduler to be a no-op coroutine so it doesn't actually start.
    async def _noop_scheduler(*a, **kw):
        await asyncio.sleep(999)

    with patch("scheduler.run_scheduler", side_effect=_noop_scheduler):
        r = client.post("/api/scheduler/start")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "started"


def test_scheduler_stop_not_running(client):
    r = client.post("/api/scheduler/stop")
    assert r.status_code == 200
    assert r.json()["status"] == "not_running"


def test_scheduler_status_includes_stale_lock_info(client, tmp_path):
    lock_file = tmp_path / "scheduler.lock"
    lock_file.write_text("999999", encoding="utf-8")

    with patch("scheduler._is_pid_alive", return_value=False):
        r = client.get("/api/scheduler/status")

    assert r.status_code == 200
    data = r.json()
    assert data["running"] is False
    assert data["lock"]["exists"] is True
    assert data["lock"]["pid"] == 999999
    assert data["lock"]["pid_alive"] is False
    assert data["lock"]["stale"] is True


def test_scheduler_pause_resume_endpoints(client):
    r = client.post("/api/scheduler/pause", json={"reason": "incident"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "paused"
    assert data["control"]["paused"] is True
    assert data["control"]["reason"] == "incident"

    r = client.post("/api/scheduler/resume", json={"reason": "incident resolved"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "resumed"
    assert data["control"]["paused"] is False
    assert data["control"]["maintenance_mode"] is False


def test_scheduler_maintenance_enable_disable_endpoints(client):
    r = client.post("/api/scheduler/maintenance/enable", json={"reason": "db maintenance"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "maintenance_enabled"
    assert data["control"]["maintenance_mode"] is True
    assert data["control"]["paused"] is True

    r = client.post("/api/scheduler/maintenance/disable", json={"reason": "maintenance done"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "maintenance_disabled"
    assert data["control"]["maintenance_mode"] is False
    assert data["control"]["paused"] is False


def test_scheduler_drain_endpoint_sets_flag(client):
    r = client.post("/api/scheduler/drain", json={"reason": "safe shutdown"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "drain_requested"
    assert data["control"]["drain_requested"] is True

    r = client.get("/api/scheduler/status")
    assert r.status_code == 200
    status_data = r.json()
    assert status_data["control"]["drain_requested"] is True


# ── Reports ───────────────────────────────────────────────────────────


def test_reports_list(client):
    r = client.get("/api/reports")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_reports_list_and_read(client, tmp_path):
    reports_dir = Path("data") / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report = reports_dir / "test_report.md"
    report.write_text("# Test Report\nSome content", encoding="utf-8")

    try:
        r = client.get("/api/reports")
        assert r.status_code == 200
        reports = r.json()
        assert any(rp["filename"] == "test_report.md" for rp in reports)

        r = client.get("/api/reports/test_report.md")
        assert r.status_code == 200
        assert "Test Report" in r.json()["content"]
    finally:
        report.unlink(missing_ok=True)


def test_reports_not_found(client):
    r = client.get("/api/reports/nonexistent.md")
    assert r.status_code == 404


def test_reports_path_traversal(client):
    # FastAPI path params with slashes get 404 from router,
    # but we should also test the .. check directly
    r = client.get("/api/reports/..%2F..%2Fetc%2Fpasswd")
    assert r.status_code in (400, 404)


# ── Personas endpoint ─────────────────────────────────────────────────


def test_personas_list(client):
    r = client.get("/api/personas")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) > 0
    persona = data[0]
    assert "name" in persona
    assert "workflow" in persona
    assert "tools_count" in persona


# ── API Key Authentication ─────────────────────────────────────────────


class TestApiAuth:
    @pytest.fixture()
    def auth_client(self, tmp_path, monkeypatch):
        state_file = str(tmp_path / "agent_state.json")
        cfg = AppConfig(state_file=state_file, api_key="test-secret-key")
        monkeypatch.setattr("api_server._default_config", cfg)
        async def _noop_scheduler(*a, **kw):
            await asyncio.sleep(999)
        with patch("scheduler.run_scheduler", side_effect=_noop_scheduler), \
             patch("scheduler.acquire_scheduler_lock", return_value=True), \
             patch("scheduler.release_scheduler_lock"):
            app = create_app(cfg)
            yield TestClient(app)

    def test_no_key_blocks_api(self, auth_client):
        r = auth_client.get("/api/personas")
        assert r.status_code == 401
        assert "API key" in r.json()["detail"]

    def test_wrong_key_blocks_api(self, auth_client):
        r = auth_client.get("/api/personas", headers={"Authorization": "Bearer wrong-key"})
        assert r.status_code == 401

    def test_correct_key_allows_api(self, auth_client):
        r = auth_client.get("/api/personas", headers={"Authorization": "Bearer test-secret-key"})
        assert r.status_code == 200

    def test_dashboard_public(self, auth_client):
        r = auth_client.get("/")
        assert r.status_code == 200
        assert "NATLClaw Dashboard" in r.text

    def test_health_public(self, auth_client):
        r = auth_client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_no_key_configured_allows_all(self, client):
        """When api_key is empty, no auth required."""
        r = client.get("/api/personas")
        assert r.status_code == 200


def test_surface_ingress_create_task_bridge(client, state_file):
    event = _surface_event(requires_reply=True)

    r = client.post("/api/surface/events", json=event)
    assert r.status_code == 202
    body = r.json()
    assert body["decision"] == "create_task"
    assert body["status"] == "accepted"
    assert body["idempotent"] is False
    assert body["task_id"]

    tasks = asyncio.run(load_tasks(state_file))
    assert len(tasks) == 1
    assert tasks[0].id == body["task_id"]
    assert tasks[0].status == "pending"


def test_surface_ingress_append_inbox_bridge(client, state_file):
    event = _surface_event(
        event_id="evt_group_0001",
        text="FYI: CI is green and deployment completed.",
        requires_reply=False,
        idempotency_key="canary:g_ops:msg_100",
    )

    r = client.post("/api/surface/events", json=event)
    assert r.status_code == 202
    body = r.json()
    assert body["decision"] == "append_inbox_message"
    assert body["message_id"]

    outbox = asyncio.run(load_outbox(state_file))
    assert len(outbox) == 1
    assert outbox[0].id == body["message_id"]
    assert outbox[0].payload["surface_event_id"] == "evt_group_0001"


def test_surface_ingress_duplicate_is_idempotent_noop(client, state_file):
    event = _surface_event(idempotency_key="canary:u_42:msg_dup")

    first = client.post("/api/surface/events", json=event)
    assert first.status_code == 202
    first_body = first.json()
    assert first_body["idempotent"] is False
    task_id = first_body["task_id"]

    second = client.post("/api/surface/events", json=event)
    assert second.status_code == 202
    second_body = second.json()
    assert second_body["idempotent"] is True
    assert second_body["status"] == "accepted_noop"
    assert second_body["task_id"] == task_id

    tasks = asyncio.run(load_tasks(state_file))
    assert len(tasks) == 1


def test_surface_ingress_invalid_payload_returns_400(client):
    r = client.post("/api/surface/events", json={"event_id": "broken"})
    assert r.status_code == 400
    assert "spec_version" in r.json()["detail"]
