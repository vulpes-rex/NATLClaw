from __future__ import annotations

import json

import event_watcher
from event_watcher import enqueue_event
from personas.workspace_observer import tools as observer_tools


def test_drain_events_reads_pending_events_ndjson(tmp_path, monkeypatch):
    ndjson = tmp_path / "pending_events.ndjson"
    monkeypatch.setattr(event_watcher, "_PENDING_EVENTS_FILE", ndjson)

    enqueue_event("task_created", {"task_id": "t100"})
    enqueue_event("file_change", {"path": "src/app.py"})

    drained = observer_tools.drain_events()
    lines = [line for line in drained.splitlines() if line.strip()]
    assert len(lines) == 2

    records = [json.loads(line) for line in lines]
    event_types = {record["event_type"] for record in records}
    assert event_types == {"task_created", "file_change"}
    assert ndjson.read_text(encoding="utf-8").strip() == ""


def test_drain_events_returns_no_pending_events_when_empty(tmp_path, monkeypatch):
    ndjson = tmp_path / "pending_events.ndjson"
    monkeypatch.setattr(event_watcher, "_PENDING_EVENTS_FILE", ndjson)
    ndjson.write_text("", encoding="utf-8")

    assert observer_tools.drain_events() == "No pending events."


def test_scan_todos_caps_files_under_event_storm(tmp_path, monkeypatch):
    for idx in range(6):
        (tmp_path / f"f{idx}.py").write_text("# TODO: test\n", encoding="utf-8")
    monkeypatch.setattr(observer_tools, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(observer_tools, "_MAX_SCAN_FILES", 2)

    output = observer_tools.scan_todos(max_results=10)
    assert "... scan capped at 2 files" in output


def test_list_recently_modified_caps_files_under_event_storm(tmp_path, monkeypatch):
    for idx in range(6):
        path = tmp_path / f"m{idx}.py"
        path.write_text("x=1\n", encoding="utf-8")
    monkeypatch.setattr(observer_tools, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(observer_tools, "_MAX_SCAN_FILES", 3)

    output = observer_tools.list_recently_modified(count=10)
    assert "... scan capped at 3 files" in output
