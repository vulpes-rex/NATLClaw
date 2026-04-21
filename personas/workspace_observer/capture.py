"""Post-capture hooks for the workspace_observer persona (invoked via capturePolicy)."""

from __future__ import annotations

from typing import Any


def after_note(brain: Any, note_id: str) -> None:
    """Resolve divergent summaries when evidence overlaps (brain Phase 4)."""
    from second_brain import reconcile_evidence_contradictions

    reconcile_evidence_contradictions(brain, note_id)
