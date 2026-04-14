"""Surface adapter conformance fixtures for multi-channel ingress."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api_server import create_app
from config import AppConfig
from messaging import load_outbox
from tasks import load_tasks


_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "surface"


def _load_fixture(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


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
    async def _noop_scheduler(*_args, **_kwargs):
        await asyncio.sleep(999)

    with patch("scheduler.run_scheduler", side_effect=_noop_scheduler), \
         patch("scheduler.acquire_scheduler_lock", return_value=True), \
         patch("scheduler.release_scheduler_lock"):
        app = create_app(config)
        yield TestClient(app)


@pytest.fixture()
def state_file(config):
    return config.state_file


@pytest.mark.parametrize(
    "fixture_path",
    sorted(_FIXTURES_DIR.glob("*.json")),
    ids=lambda p: p.stem,
)
def test_surface_fixture_conformance_accepts_expected_decision(
    client,
    state_file,
    fixture_path: Path,
):
    fixture = _load_fixture(fixture_path)
    event = fixture["event"]
    expected_decision = fixture["expected_decision"]

    r = client.post("/api/surface/events", json=event)
    assert r.status_code == 202
    body = r.json()
    assert body["decision"] == expected_decision
    assert body["idempotent"] is False

    tasks = asyncio.run(load_tasks(state_file))
    outbox = asyncio.run(load_outbox(state_file))
    if expected_decision == "create_task":
        assert len(tasks) == 1
        assert body["task_id"] == tasks[0].id
    if expected_decision == "append_inbox_message":
        assert len(outbox) == 1
        assert body["message_id"] == outbox[0].id


@pytest.mark.parametrize(
    "fixture_path",
    sorted(_FIXTURES_DIR.glob("*.json")),
    ids=lambda p: p.stem,
)
def test_surface_fixture_replay_is_idempotent_noop(client, fixture_path: Path):
    fixture = _load_fixture(fixture_path)
    event = fixture["event"]

    first = client.post("/api/surface/events", json=event)
    assert first.status_code == 202
    second = client.post("/api/surface/events", json=event)
    assert second.status_code == 202
    body = second.json()
    assert body["idempotent"] is True
    assert body["status"] == "accepted_noop"
