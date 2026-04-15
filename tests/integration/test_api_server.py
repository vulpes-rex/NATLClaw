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
            surface_channels_enabled=("canary", "canary_webhook"),
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
    assert "Dreaming" in r.text
    assert "History filter" in r.text
    assert "Copy JSON" in r.text


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


def test_heartbeat_status_uses_operator_scheduler_state(client):
    async def _snap(*_a, **_kw):
        return {
            "heartbeat": {
                "status": "active",
                "last": "2026-01-01T00:00:00+00:00",
                "seconds_ago": 1.2,
                "count": 9,
            },
            "scheduler": {
                "running": True,
                "in_process_task_running": False,
                "control": {"paused": False},
                "backpressure": {"queue_depth_before_decision": 0},
            },
        }

    with patch("api_server.build_operator_status", side_effect=_snap):
        r = client.get("/api/heartbeat/status")
    assert r.status_code == 200
    data = r.json()
    assert data["scheduler_running"] is True
    assert data["scheduler"]["in_process_task_running"] is False
    assert "control" in data["scheduler"]


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
    assert "dream" in data
    assert "enabled" in data["dream"]
    assert "idle_streak_min" in data["dream"]
    assert "max_age_days" in data["dream"]
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


def test_surface_ingress_second_channel_webhook_bridge(client, state_file):
    event = _surface_event(
        event_id="evt_webhook_0001",
        adapter="canary_webhook",
        requires_reply=True,
        idempotency_key="canary_webhook:u_42:msg_1",
    )
    event["payload"] = {"message": "Please create task from webhook payload"}

    r = client.post("/api/surface/events", json=event)
    assert r.status_code == 202
    body = r.json()
    assert body["decision"] == "create_task"
    assert body["task_id"]

    tasks = asyncio.run(load_tasks(state_file))
    assert len(tasks) == 1
    assert tasks[0].id == body["task_id"]
    assert "webhook payload" in tasks[0].title.lower()


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


def test_surface_sessions_are_queryable(client):
    event = _surface_event(
        event_id="evt_session_0001",
        idempotency_key="canary:sess:evt_session_0001",
    )
    r = client.post("/api/surface/events", json=event)
    assert r.status_code == 202

    sessions = client.get("/api/surface/sessions")
    assert sessions.status_code == 200
    data = sessions.json()
    assert len(data) >= 1
    session = next((s for s in data if s["session_id"] == "sess_canary_u_42"), None)
    assert session is not None
    assert session["active_persona"]
    assert session["last_event_id"] == "evt_session_0001"

    detail = client.get("/api/surface/sessions/sess_canary_u_42")
    assert detail.status_code == 200
    assert detail.json()["session_id"] == "sess_canary_u_42"


def test_surface_routes_recent_trace_includes_outcome(client):
    event = _surface_event(
        event_id="evt_route_trace_0001",
        idempotency_key="canary:trace:evt_route_trace_0001",
        requires_reply=True,
    )
    accepted = client.post("/api/surface/events", json=event)
    assert accepted.status_code == 202
    accepted_body = accepted.json()

    routes = client.get("/api/surface/routes/recent?limit=10")
    assert routes.status_code == 200
    rows = routes.json()
    trace = next((r for r in rows if r["event_id"] == "evt_route_trace_0001"), None)
    assert trace is not None
    assert trace["session_id"] == "sess_canary_u_42"
    assert trace["decision"] == "create_task"
    assert trace["task_id"] == accepted_body["task_id"]
    assert trace["status"] == "accepted"

    filtered = client.get("/api/surface/routes/recent?event_id=evt_route_trace_0001")
    assert filtered.status_code == 200
    filtered_rows = filtered.json()
    assert len(filtered_rows) >= 1
    assert all(r["event_id"] == "evt_route_trace_0001" for r in filtered_rows)


def test_surface_routes_capture_idempotent_replay(client):
    event = _surface_event(
        event_id="evt_replay_0001",
        idempotency_key="canary:replay:evt_replay_0001",
        requires_reply=False,
        text="FYI replay candidate",
    )
    first = client.post("/api/surface/events", json=event)
    assert first.status_code == 202
    second = client.post("/api/surface/events", json=event)
    assert second.status_code == 202
    assert second.json()["idempotent"] is True

    routes = client.get("/api/surface/routes/recent?event_id=evt_replay_0001")
    assert routes.status_code == 200
    rows = routes.json()
    statuses = {row["status"] for row in rows}
    assert "accepted" in statuses
    assert "accepted_noop" in statuses


def test_surface_health_endpoint_reflects_rollout_state(client):
    seed = _surface_event(
        event_id="evt_health_0001",
        idempotency_key="canary:health:evt_health_0001",
    )
    accepted = client.post("/api/surface/events", json=seed)
    assert accepted.status_code == 202

    health = client.get("/api/surface/health")
    assert health.status_code == 200
    body = health.json()
    assert body["ingress_enabled"] is True
    assert "canary" in body["allowed_channels"]
    assert body["session_count"] >= 1
    assert body["recent_routes_count"] >= 1
    assert body["latest_route"]["event_id"] == "evt_health_0001"


def test_surface_ingress_adapter_allowlist_rejects_unknown(client):
    event = _surface_event(
        event_id="evt_unknown_adapter_0001",
        adapter="discord",
        idempotency_key="discord:u_42:evt_unknown_adapter_0001",
    )
    r = client.post("/api/surface/events", json=event)
    assert r.status_code == 400
    assert "not enabled" in r.json()["detail"]


def test_surface_ingress_conflicting_idempotency_key_returns_409(client):
    first = _surface_event(
        event_id="evt_conflict_0001",
        idempotency_key="canary:conflict:key-1",
        text="first payload",
    )
    second = _surface_event(
        event_id="evt_conflict_0002",
        idempotency_key="canary:conflict:key-1",
        text="different payload",
    )
    r1 = client.post("/api/surface/events", json=first)
    assert r1.status_code == 202
    r2 = client.post("/api/surface/events", json=second)
    assert r2.status_code == 409
    assert "already used for a different payload" in r2.json()["detail"]


def test_surface_ingress_returns_503_when_disabled(tmp_path, monkeypatch):
    state_file = str(tmp_path / "agent_state.json")
    cfg = AppConfig(
        state_file=state_file,
        surface_ingress_enabled=False,
        surface_channels_enabled=("canary",),
    )
    monkeypatch.setattr("api_server._default_config", cfg)

    async def _noop_scheduler(*a, **kw):
        await asyncio.sleep(999)

    with patch("scheduler.run_scheduler", side_effect=_noop_scheduler), \
         patch("scheduler.acquire_scheduler_lock", return_value=True), \
         patch("scheduler.release_scheduler_lock"):
        app = create_app(cfg)
        local_client = TestClient(app)
        r = local_client.post("/api/surface/events", json=_surface_event())
        assert r.status_code == 503


def test_surface_routing_is_deterministic_for_same_input_and_session(client):
    base = _surface_event(
        event_id="evt_determinism_0001",
        idempotency_key="canary:determinism:1",
        requires_reply=False,
        text="FYI only: deployment completed.",
    )
    first = client.post("/api/surface/events", json=base)
    assert first.status_code == 202

    second_event = _surface_event(
        event_id="evt_determinism_0002",
        idempotency_key="canary:determinism:2",
        requires_reply=False,
        text="FYI only: deployment completed.",
    )
    second = client.post("/api/surface/events", json=second_event)
    assert second.status_code == 202

    assert first.json()["decision"] == second.json()["decision"]


def test_surface_invalid_persona_hint_falls_back_to_default(client):
    event = _surface_event(
        event_id="evt_persona_fallback_0001",
        idempotency_key="canary:persona:fallback:1",
        requires_reply=True,
    )
    event["routing"]["persona_hint"] = "definitely-not-a-real-persona"

    r = client.post("/api/surface/events", json=event)
    assert r.status_code == 202
    body = r.json()
    assert body["persona"] == "default"
    assert "fell back to default" in body["reason"]


def test_surface_dm_and_group_sessions_do_not_collide(client):
    dm_event = _surface_event(
        event_id="evt_dm_session_0001",
        idempotency_key="canary:dm:session:1",
    )
    group_event = _surface_event(
        event_id="evt_group_session_0001",
        idempotency_key="canary:group:session:1",
    )
    group_event["session"] = {
        "session_id": "sess_canary_group_ops_001",
        "thread_id": "ops-room",
        "user_id": "u_ops_1",
        "group_id": "g_ops",
    }

    assert client.post("/api/surface/events", json=dm_event).status_code == 202
    assert client.post("/api/surface/events", json=group_event).status_code == 202

    sessions = client.get("/api/surface/sessions")
    assert sessions.status_code == 200
    ids = {entry["session_id"] for entry in sessions.json()}
    assert "sess_canary_u_42" in ids
    assert "sess_canary_group_ops_001" in ids
    assert len(ids) >= 2


def test_surface_suspended_session_suppresses_task_without_override(client, state_file):
    sessions_path = Path(state_file).with_name("surface_sessions.json")
    sessions_path.write_text(
        json.dumps(
            {
                "sess_canary_u_42": {
                    "session_id": "sess_canary_u_42",
                    "channel_type": "canary",
                    "origin_type": "dm",
                    "active_persona": "default",
                    "state": "suspended",
                    "reply_mode": "manual_review",
                    "last_event_ts": "2026-04-14T20:00:00Z",
                    "last_event_id": "seed_suspended",
                    "last_adapter": "canary",
                    "updated_at": "2026-04-14T20:00:00Z"
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    blocked = _surface_event(
        event_id="evt_suspended_0001",
        idempotency_key="canary:suspended:1",
        requires_reply=True,
        text="Please create a task while suspended",
    )
    blocked_resp = client.post("/api/surface/events", json=blocked)
    assert blocked_resp.status_code == 202
    blocked_body = blocked_resp.json()
    assert blocked_body["decision"] == "append_inbox_message"
    assert "suppressed" in blocked_body["reason"]
    assert blocked_body["task_id"] is None

    override = _surface_event(
        event_id="evt_suspended_0002",
        idempotency_key="canary:suspended:2",
        requires_reply=True,
        text="Please create a task with override",
    )
    override["routing"]["allow_suspended_override"] = True
    override_resp = client.post("/api/surface/events", json=override)
    assert override_resp.status_code == 202
    override_body = override_resp.json()
    assert override_body["decision"] == "create_task"
    assert override_body["task_id"]
