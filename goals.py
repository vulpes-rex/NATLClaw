"""Goal / task management for multi-heartbeat objectives.

Goals live in ``state.context["active_goals"]`` and follow a simple
lifecycle: ``pending → in_progress → completed | abandoned``.

Each heartbeat's review step evaluates goal progress.  The status-check
prompt at the *start* of the next heartbeat injects active goals so the
agent keeps working toward them.

Usage
-----
::

    from goals import add_goal, get_active_goals, advance_goal, complete_goal

    gid = add_goal(state, "Map React component hierarchy", target_heartbeats=5)
    advance_goal(state, gid, "Found 12 components")
    complete_goal(state, gid)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from state import AgentState

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# CRUD helpers
# ──────────────────────────────────────────────────────────────────────

def _goals_list(state: AgentState) -> list[dict]:
    """Return (and lazily initialise) the goals list in state.context."""
    if "active_goals" not in state.context:
        state.context["active_goals"] = []
    return state.context["active_goals"]


def add_goal(
    state: AgentState,
    description: str,
    *,
    target_heartbeats: int = 5,
    priority: int = 1,
) -> str:
    """Create a new goal and return its ID."""
    goals = _goals_list(state)
    gid = f"g{uuid.uuid4().hex[:6]}"
    goals.append({
        "id": gid,
        "description": description,
        "status": "pending",
        "priority": priority,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "target_heartbeats": target_heartbeats,
        "heartbeats_spent": 0,
        "progress_notes": [],
    })
    logger.info("[goals] created %s: %s (target=%d HBs)", gid, description[:80], target_heartbeats)
    return gid


def get_active_goals(state: AgentState) -> list[dict]:
    """Return goals that are ``pending`` or ``in_progress``, sorted by priority."""
    return sorted(
        [g for g in _goals_list(state) if g["status"] in ("pending", "in_progress")],
        key=lambda g: g.get("priority", 99),
    )


def get_goal(state: AgentState, goal_id: str) -> dict | None:
    """Look up a single goal by ID."""
    for g in _goals_list(state):
        if g["id"] == goal_id:
            return g
    return None


def advance_goal(state: AgentState, goal_id: str, note: str) -> bool:
    """Record progress on a goal.  Returns False if goal not found."""
    goal = get_goal(state, goal_id)
    if goal is None:
        logger.warning("[goals] advance_goal: %s not found", goal_id)
        return False
    goal["status"] = "in_progress"
    goal["heartbeats_spent"] = goal.get("heartbeats_spent", 0) + 1
    goal["progress_notes"].append(note[:500])
    logger.info("[goals] advanced %s (%d/%d): %s",
                goal_id, goal["heartbeats_spent"], goal["target_heartbeats"], note[:80])
    return True


def complete_goal(state: AgentState, goal_id: str) -> bool:
    """Mark a goal as completed.  Returns False if not found."""
    goal = get_goal(state, goal_id)
    if goal is None:
        return False
    goal["status"] = "completed"
    goal["completed_at"] = datetime.now(timezone.utc).isoformat()
    logger.info("[goals] completed %s", goal_id)
    return True


def abandon_goal(state: AgentState, goal_id: str, reason: str = "") -> bool:
    """Mark a goal as abandoned.  Returns False if not found."""
    goal = get_goal(state, goal_id)
    if goal is None:
        return False
    goal["status"] = "abandoned"
    goal["abandoned_at"] = datetime.now(timezone.utc).isoformat()
    if reason:
        goal["progress_notes"].append(f"Abandoned: {reason[:300]}")
    logger.info("[goals] abandoned %s: %s", goal_id, reason[:80])
    return True


def build_goals_block(state: AgentState) -> str:
    """Build a text block summarising active goals for prompt injection."""
    active = get_active_goals(state)
    if not active:
        return ""
    lines = ["\n== ACTIVE GOALS =="]
    for g in active:
        spent = g.get("heartbeats_spent", 0)
        target = g.get("target_heartbeats", "?")
        status = g["status"].upper()
        lines.append(f"  [{g['id']}] {g['description']}")
        lines.append(f"    Status: {status}  |  Progress: {spent}/{target} heartbeats")
        notes = g.get("progress_notes", [])
        if notes:
            for n in notes[-3:]:  # last 3 progress notes
                lines.append(f"    - {n[:120]}")
    return "\n".join(lines)


def auto_expire_goals(state: AgentState) -> list[str]:
    """Abandon goals that have exceeded their target heartbeats by 2×.

    Returns the IDs of newly-abandoned goals.
    """
    expired: list[str] = []
    for g in _goals_list(state):
        if g["status"] in ("pending", "in_progress"):
            if g.get("heartbeats_spent", 0) >= g.get("target_heartbeats", 5) * 2:
                abandon_goal(state, g["id"], "Auto-expired: exceeded 2× target heartbeats")
                expired.append(g["id"])
    return expired
