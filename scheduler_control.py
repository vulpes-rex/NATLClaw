from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class SchedulerControlState:
    """Persistent operator control-plane state for the scheduler."""

    paused: bool = False
    maintenance_mode: bool = False
    drain_requested: bool = False
    drain_in_progress: bool = False
    updated_at: str = ""
    reason: str = ""


def _control_path(state_file: str) -> str:
    return os.path.join(
        os.path.dirname(os.path.abspath(state_file)),
        "scheduler_control.json",
    )


def _read_control(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_control(control: SchedulerControlState, path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(os.path.abspath(path)),
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(asdict(control), f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except BaseException:
        os.unlink(tmp_path)
        raise


def _normalize_control(data: dict) -> SchedulerControlState:
    filtered = {
        k: v
        for k, v in data.items()
        if k in SchedulerControlState.__dataclass_fields__
    }
    return SchedulerControlState(**filtered)


async def load_scheduler_control(state_file: str) -> SchedulerControlState:
    """Load scheduler control state (defaults when missing/corrupt)."""
    path = _control_path(state_file)
    if not os.path.exists(path):
        return SchedulerControlState()
    loop = asyncio.get_event_loop()
    try:
        data = await loop.run_in_executor(None, _read_control, path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        logger.warning("Failed to read scheduler control state; using defaults", exc_info=True)
        return SchedulerControlState()
    return _normalize_control(data)


async def save_scheduler_control(control: SchedulerControlState, state_file: str) -> None:
    """Persist scheduler control state atomically."""
    path = _control_path(state_file)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _write_control, control, path)


async def update_scheduler_control(
    state_file: str,
    *,
    paused: bool | None = None,
    maintenance_mode: bool | None = None,
    drain_requested: bool | None = None,
    drain_in_progress: bool | None = None,
    reason: str = "",
) -> SchedulerControlState:
    """Patch and persist scheduler control values in one call."""
    control = await load_scheduler_control(state_file)
    if paused is not None:
        control.paused = paused
    if maintenance_mode is not None:
        control.maintenance_mode = maintenance_mode
    if drain_requested is not None:
        control.drain_requested = drain_requested
    if drain_in_progress is not None:
        control.drain_in_progress = drain_in_progress
    control.updated_at = datetime.now(timezone.utc).isoformat()
    if reason:
        control.reason = reason
    await save_scheduler_control(control, state_file)
    return control
