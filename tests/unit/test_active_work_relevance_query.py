from __future__ import annotations

from project_context import format_active_work_search_query


def test_format_active_work_empty():
    assert format_active_work_search_query(None) == ""
    assert format_active_work_search_query({}) == ""


def test_format_active_work_combines_fields():
    s = format_active_work_search_query(
        {
            "commit_intent": "fix scheduler wake",
            "summary": "branch=feat | files=scheduler.py",
            "branch": "feat",
            "files": ["scheduler.py", "event_watcher.py"],
        }
    )
    assert "fix scheduler wake" in s
    assert "scheduler.py" in s
