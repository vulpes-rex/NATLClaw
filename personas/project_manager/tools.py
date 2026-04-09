"""Tools for the project_manager skill — task tracking."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Annotated


_TASKS_FILE = "data/tasks.json"


def _validate_path(
    path: str,
    allow_directories: bool = False,
    must_exist: bool = True,
    operation: str = "access"
) -> tuple[bool, str]:
    """
    Validate a file/directory path against workspace security restrictions.
    
    Returns:
        (is_valid, error_message)
    """
    # Get workspace root (current working directory)
    workspace = os.path.abspath(os.getcwd())
    
    # Resolve to absolute path
    try:
        abs_path = os.path.abspath(path)
    except Exception:
        return False, f"Invalid path format: {path}"
    
    # Check if path is within workspace (primary validation)
    if not abs_path.startswith(workspace):
        return False, f"{operation}: '{path}' is outside the workspace directory"
    
    # Additional check: ensure path doesn't contain redundant parent directory references
    # This catches cases like "dir/../secret" where the normalized path might be valid
    # but the original input is suspicious
    if path.replace("\\", "/").count("../") > 0:
        # Check if the path tries to go above workspace root
        common = os.path.commonpath([workspace, abs_path])
        if common != workspace:
            return False, f"{operation}: '{path}' contains path traversal attempt"
    
    # Check if path exists
    if must_exist and not os.path.exists(abs_path):
        return False, f"{operation}: Path '{path}' does not exist"
    
    # For directories, ensure it's actually a directory if required
    if allow_directories and must_exist and not os.path.isdir(abs_path):
        return False, f"{operation}: '{path}' is not a directory"
    
    return True, ""


def _load_tasks() -> list[dict]:
    # Validate path even if file doesn't exist (we'll return empty list)
    is_valid, error = _validate_path(_TASKS_FILE, operation="load_tasks", must_exist=False)
    if not is_valid:
        return []
    
    if not os.path.exists(_TASKS_FILE):
        return []
    
    try:
        with open(_TASKS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return []
    except Exception:
        return []


def _save_tasks(tasks: list[dict]) -> None:
    # Validate path - file may not exist yet, that's okay
    is_valid, error = _validate_path(_TASKS_FILE, operation="save_tasks", must_exist=False)
    if not is_valid:
        return
    
    try:
        os.makedirs(os.path.dirname(_TASKS_FILE) or ".", exist_ok=True)
        with open(_TASKS_FILE, "w", encoding="utf-8") as f:
            json.dump(tasks, f, indent=2)
    except Exception:
        pass


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
