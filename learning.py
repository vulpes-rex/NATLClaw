from __future__ import annotations

from datetime import datetime, timezone

from state import AgentState


def extract_lessons(step: str, prompt: str, response: str) -> list[dict]:
    """Extract lessons from an agent interaction based on signal words."""
    lessons: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()
    resp_lower = response.lower()

    # Look for strong negative signals (not just the word "error" in any context)
    error_patterns = ("failed to", "exception occurred", "error:", "traceback", "crash")
    if any(p in resp_lower for p in error_patterns):
        lessons.append({
            "type": "error_encountered",
            "step": step,
            "description": f"Error signal during '{step}': {response[:120]}",
            "timestamp": now,
        })

    # Look for explicit completion signals
    success_patterns = ("completed successfully", "task done", "finished successfully")
    if any(p in resp_lower for p in success_patterns):
        lessons.append({
            "type": "success_achieved",
            "step": step,
            "description": f"Success signal during '{step}': {response[:120]}",
            "timestamp": now,
        })

    if any(w in resp_lower for w in ("warning:", "caution:", "⚠")):
        lessons.append({
            "type": "warning_noted",
            "step": step,
            "description": f"Warning signal during '{step}': {response[:120]}",
            "timestamp": now,
        })

    return lessons


def build_context_block(state: AgentState, max_recent: int = 5) -> str:
    """Build a context string from memory and lessons to inject into the prompt."""
    lines = ["== AGENT MEMORY =="]
    lines.append(f"Last heartbeat: {state.last_heartbeat or 'never'}")
    lines.append(f"Total executions: {state.execution_count}")

    # Recent lessons
    recent_lessons = state.lessons_learned[-max_recent:]
    if recent_lessons:
        lines.append("\nRecent lessons:")
        for lesson in recent_lessons:
            lines.append(f"  - [{lesson.get('type')}] {lesson.get('description', '')[:100]}")

    # Recent activity
    recent_activity = state.execution_history[-max_recent:]
    if recent_activity:
        lines.append("\nRecent activity:")
        for entry in recent_activity:
            ts = entry.get("timestamp", "?")
            step = entry.get("step", "?")
            resp = entry.get("response", "")[:80]
            lines.append(f"  - [{ts}] {step}: {resp}")

    # Stored memory
    if state.memory:
        lines.append("\nStored memory:")
        for k, v in state.memory.items():
            lines.append(f"  - {k}: {str(v)[:100]}")

    return "\n".join(lines)
