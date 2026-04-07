"""Tools for the project_manager skill — task tracking."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Annotated

_TASKS_FILE = "data/tasks.json"


def _load_tasks() -> list[dict]:
    if os.path.exists(_TASKS_FILE):
        with open(_TASKS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def _save_tasks(tasks: list[dict]) -> None:
    os.makedirs(os.path.dirname(_TASKS_FILE) or ".", exist_ok=True)
    with open(_TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, indent=2)


def list_tasks(
    status_filter: Annotated[str, "Filter: all, todo, in_progress, done"] = "all",
) -> str:
    """List project tasks, optionally filtered by status."""
    tasks = _load_tasks()
    if status_filter != "all":
        tasks = [t for t in tasks if t.get("status") == status_filter]
    if not tasks:
        return "(no tasks)"
    lines = []
    for t in tasks:
        line = f"[{t['id']}] ({t['status']}) {t['title']}"
        if t.get("due"):
            line += f"  due:{t['due']}"
        if t.get("priority"):
            line += f"  priority:{t['priority']}"
        lines.append(line)
    return "\n".join(lines)


def add_task(
    title: Annotated[str, "Short task title"],
    priority: Annotated[str, "Priority: low, medium, high"] = "medium",
    due: Annotated[str, "Due date YYYY-MM-DD (optional)"] = "",
) -> str:
    """Add a new task to the project board."""
    tasks = _load_tasks()
    task_id = len(tasks) + 1
    task = {
        "id": task_id,
        "title": title,
        "status": "todo",
        "priority": priority,
        "due": due,
        "created": datetime.now(timezone.utc).isoformat(),
    }
    tasks.append(task)
    _save_tasks(tasks)
    return f"Created task #{task_id}: {title}"


def update_task(
    task_id: Annotated[int, "ID of the task to update"],
    status: Annotated[str, "New status: todo, in_progress, done"] = "",
    priority: Annotated[str, "New priority: low, medium, high"] = "",
) -> str:
    """Update task status or priority."""
    tasks = _load_tasks()
    for t in tasks:
        if t["id"] == task_id:
            if status:
                t["status"] = status
            if priority:
                t["priority"] = priority
            t["updated"] = datetime.now(timezone.utc).isoformat()
            _save_tasks(tasks)
            return f"Updated task #{task_id}: status={t['status']}, priority={t['priority']}"
    return f"Task #{task_id} not found."


def get_project_summary() -> str:
    """Get a summary of the project board."""
    tasks = _load_tasks()
    if not tasks:
        return "No tasks on the board yet."
    by_status: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    for t in tasks:
        s = t.get("status", "?")
        p = t.get("priority", "?")
        by_status[s] = by_status.get(s, 0) + 1
        by_priority[p] = by_priority.get(p, 0) + 1
    lines = [
        f"Total tasks: {len(tasks)}",
        f"By status: {', '.join(f'{k}={v}' for k, v in by_status.items())}",
        f"By priority: {', '.join(f'{k}={v}' for k, v in by_priority.items())}",
    ]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    overdue = [t for t in tasks if t.get("due") and t["due"] < today and t["status"] != "done"]
    if overdue:
        labels = ", ".join(f"#{t['id']} {t['title']}" for t in overdue)
        lines.append(f"OVERDUE: {labels}")
    return "\n".join(lines)
