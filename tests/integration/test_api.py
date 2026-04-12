"""Tests for the NATLClaw FastAPI layer (api_server.py)."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from api_server import create_app
from config import AppConfig
from messaging import Message, save_outbox
from tasks import Task, save_tasks

_SECRET_FIELDS = frozenset({
    "openai_api_key", "github_pat", "openrouter_api_key", "azure_openai_api_key",
})


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _tmp_data(tmp_path, monkeypatch):
    """Point the API at a temporary data directory."""
    state_file = str(tmp_path / "agent_state.json")
    monkeypatch.setattr("api_server._default_config", AppConfig(state_file=state_file))
    return tmp_path


@pytest.fixture()
def config():
    import api_server
    return api_server._default_config


@pytest.fixture()
def client(config):
    app = create_app(config)
    return TestClient(app)


@pytest.fixture()
def state_file(config):
    return config.state_file


# ── Health ─────────────────────────────────────────────────────────────


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ── Tasks ──────────────────────────────────────────────────────────────


def test_create_and_list_tasks(client):
    r = client.post("/api/tasks", json={"title": "Fix login", "priority": "high"})
    assert r.status_code == 200
    data = r.json()
    assert data["title"] == "Fix login"
    assert data["priority"] == "high"
    assert data["status"] == "pending"
    task_id = data["id"]

    r = client.get("/api/tasks")
    assert r.status_code == 200
    assert any(t["id"] == task_id for t in r.json())


def test_list_tasks_filtered(client):
    client.post("/api/tasks", json={"title": "A"})
    r = client.get("/api/tasks?status=completed")
    assert r.status_code == 200
    assert r.json() == []


def test_get_task_by_id(client):
    r = client.post("/api/tasks", json={"title": "Test detail"})
    task_id = r.json()["id"]

    r = client.get(f"/api/tasks/{task_id}")
    assert r.status_code == 200
    assert r.json()["title"] == "Test detail"


def test_get_task_not_found(client):
    r = client.get("/api/tasks/no_such")
    assert r.status_code == 404


def test_answer_blocked_task(client, state_file):
    task = Task(title="Blocked task", status="blocked")
    task.questions.append({"question": "Which DB?", "timestamp": "t"})
    asyncio.run(save_tasks([task], state_file))

    r = client.post(f"/api/tasks/{task.id}/answer", json={"answer": "PostgreSQL"})
    assert r.status_code == 200
    assert r.json()["status"] == "assigned"


def test_answer_non_blocked_task(client):
    r = client.post("/api/tasks", json={"title": "Pending"})
    task_id = r.json()["id"]
    r = client.post(f"/api/tasks/{task_id}/answer", json={"answer": "x"})
    assert r.status_code == 409


def test_cancel_task(client):
    r = client.post("/api/tasks", json={"title": "To be cancelled"})
    task_id = r.json()["id"]

    r = client.post(f"/api/tasks/{task_id}/cancel", json={"reason": "no longer needed"})
    assert r.status_code == 200
    assert r.json()["status"] == "failed"


def test_cancel_terminal_task(client, state_file):
    task = Task(title="Done", status="completed")
    asyncio.run(save_tasks([task], state_file))

    r = client.post(f"/api/tasks/{task.id}/cancel")
    assert r.status_code == 409


def test_retry_failed_task(client, state_file):
    task = Task(title="Retry me", status="failed")
    asyncio.run(save_tasks([task], state_file))

    r = client.post(f"/api/tasks/{task.id}/retry")
    assert r.status_code == 200
    assert r.json()["status"] == "pending"


def test_retry_pending_task(client):
    r = client.post("/api/tasks", json={"title": "Pending"})
    task_id = r.json()["id"]
    r = client.post(f"/api/tasks/{task_id}/retry")
    assert r.status_code == 409


# ── Inbox ──────────────────────────────────────────────────────────────


def test_inbox_empty(client):
    r = client.get("/api/inbox")
    assert r.status_code == 200
    assert r.json() == []


def test_inbox_list_and_filter(client, state_file):
    m1 = Message(type="status", title="Started", status="unread")
    m2 = Message(type="alert", title="Alert!", status="read")
    asyncio.run(save_outbox([m1, m2], state_file))

    r = client.get("/api/inbox")
    assert len(r.json()) == 2

    r = client.get("/api/inbox?status=unread")
    assert len(r.json()) == 1
    assert r.json()[0]["status"] == "unread"

    r = client.get("/api/inbox?type=alert")
    assert len(r.json()) == 1
    assert r.json()[0]["type"] == "alert"


def test_inbox_show(client, state_file):
    m = Message(title="Hello")
    asyncio.run(save_outbox([m], state_file))

    r = client.get(f"/api/inbox/{m.id}")
    assert r.status_code == 200
    assert r.json()["title"] == "Hello"


def test_inbox_show_not_found(client):
    r = client.get("/api/inbox/no_such")
    assert r.status_code == 404


def test_inbox_dismiss(client, state_file):
    m = Message(title="Dismiss me")
    asyncio.run(save_outbox([m], state_file))

    r = client.post(f"/api/inbox/{m.id}/dismiss")
    assert r.status_code == 200
    assert r.json()["status"] == "dismissed"
    assert r.json()["dismissed_at"] is not None


def test_inbox_dismiss_not_found(client):
    r = client.post("/api/inbox/no_such/dismiss")
    assert r.status_code == 404


def test_inbox_clear(client, state_file):
    m1 = Message(title="A")
    m2 = Message(title="B")
    asyncio.run(save_outbox([m1, m2], state_file))

    r = client.post("/api/inbox/clear")
    assert r.status_code == 200
    assert r.json()["dismissed"] == 2


# ── Brain ──────────────────────────────────────────────────────────────


def test_brain_stats(client):
    r = client.get("/api/brain/stats")
    assert r.status_code == 200
    assert isinstance(r.json(), dict)


def test_brain_search_empty(client):
    r = client.post("/api/brain/search", json={"query": "anything"})
    assert r.status_code == 200


def test_brain_topics_empty(client):
    r = client.get("/api/brain/topics")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_brain_note_not_found(client):
    r = client.get("/api/brain/notes/n9999")
    assert r.status_code == 404


def test_brain_topic_not_found(client):
    r = client.get("/api/brain/topics/nonexistent")
    assert r.status_code == 404


def test_brain_add_note(client):
    r = client.post("/api/brain/add", json={
        "content": "FastAPI integration works",
        "tags": ["api", "test"],
        "category": "projects",
    })
    assert r.status_code == 200
    assert "note_id" in r.json()


def test_brain_add_then_describe(client):
    r = client.post("/api/brain/add", json={"content": "Describe me"})
    note_id = r.json()["note_id"]

    r = client.get(f"/api/brain/notes/{note_id}")
    assert r.status_code == 200
    assert r.json()["content"] == "Describe me"


def test_brain_feedback_not_found(client):
    r = client.post("/api/brain/notes/n9999/feedback", json={"relevant": True})
    assert r.status_code == 404


def test_brain_feedback(client):
    r = client.post("/api/brain/add", json={"content": "Feedback target"})
    note_id = r.json()["note_id"]

    r = client.post(f"/api/brain/notes/{note_id}/feedback", json={
        "relevant": True,
        "reason": "very useful",
    })
    assert r.status_code == 200
    assert r.json()["note_id"] == note_id


def test_brain_contradict(client):
    r1 = client.post("/api/brain/add", json={"content": "Old fact"})
    r2 = client.post("/api/brain/add", json={"content": "New fact"})
    old_id = r1.json()["note_id"]
    new_id = r2.json()["note_id"]

    r = client.post(f"/api/brain/notes/{old_id}/contradict", json={
        "contradicting_note_id": new_id,
        "reason": "updated info",
    })
    assert r.status_code == 200


def test_brain_contradict_not_found(client):
    r = client.post("/api/brain/notes/n9999/contradict", json={
        "contradicting_note_id": "n9998",
    })
    assert r.status_code == 404


def test_brain_lint(client):
    r = client.post("/api/brain/lint")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ── Watcher ────────────────────────────────────────────────────────────


def test_watch_status(client):
    with patch("api_server.is_watcher_running", return_value=False):
        r = client.get("/api/watch/status")
    assert r.status_code == 200
    assert r.json()["running"] is False


def test_watch_start_already_running(client):
    with patch("api_server.is_watcher_running", return_value=True):
        r = client.post("/api/watch/start")
    assert r.json()["status"] == "already_running"


def test_watch_stop_not_running(client):
    with patch("api_server.is_watcher_running", return_value=False):
        r = client.post("/api/watch/stop")
    assert r.json()["status"] == "not_running"


# ── Config ─────────────────────────────────────────────────────────────


def test_config_sanitised(client):
    r = client.get("/api/config")
    assert r.status_code == 200
    data = r.json()
    for key in _SECRET_FIELDS:
        assert data.get(key, "") in ("", "***")


# ── Heartbeat ──────────────────────────────────────────────────────────


def test_heartbeat_trigger(client):
    with patch("scheduler.run_scheduler", new_callable=AsyncMock) as mock:
        r = client.post("/api/heartbeat/trigger")
    assert r.status_code == 200
    assert r.json()["status"] == "completed"
    mock.assert_awaited_once()
