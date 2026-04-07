from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class AgentState:
    """Persistent agent state between heartbeats."""

    last_heartbeat: str | None = None
    execution_count: int = 0
    memory: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)
    execution_history: list[dict] = field(default_factory=list)
    lessons_learned: list[dict] = field(default_factory=list)


def load_state(path: str) -> AgentState:
    """Load agent state from a JSON file. Returns default state if missing."""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return AgentState(**{
            k: v for k, v in data.items() if k in AgentState.__dataclass_fields__
        })
    return AgentState()


def save_state(state: AgentState, path: str, max_history: int = 100) -> None:
    """Save agent state to JSON atomically (write tmp then rename)."""
    # Trim history
    if len(state.execution_history) > max_history:
        state.execution_history = state.execution_history[-max_history:]
    if len(state.lessons_learned) > max_history:
        state.lessons_learned = state.lessons_learned[-max_history:]

    # Ensure directory exists
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    # Atomic write
    dir_name = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(asdict(state), f, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        os.unlink(tmp_path)
        raise
