"""Capture behavior for heartbeat/task notes — data-driven, not persona-name checks.

Personas declare optional ``capturePolicy`` in ``mcp.json``. Core workflow code
uses :class:`CapturePolicy` only; persona-specific hooks live under
``personas/<name>/``.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CapturePolicy:
    """Rules for parsing agent JSON and persisting notes."""

    reject_if_no_json: bool = False
    """If True, do not create a fallback note when output is not JSON with content."""

    reject_if_missing_evidence: bool = False
    """If True, drop captures that lack substantive file/commit evidence."""

    reject_on_parse_failure: bool = False
    """If True, drop note when JSON extraction raises."""

    evidence_burst_merge_window_minutes: int = 0
    """If > 0, merge into a recent note from the same persona with overlapping evidence."""

    after_capture: str | None = None
    """``module.submodule:callable`` invoked as ``callable(brain, note_id)`` after a note is stored."""


DEFAULT_CAPTURE_POLICY = CapturePolicy()


def capture_policy_from_dict(raw: dict | None) -> CapturePolicy:
    """Build policy from ``mcp.json`` ``capturePolicy`` object."""
    if not raw:
        return DEFAULT_CAPTURE_POLICY
    return CapturePolicy(
        reject_if_no_json=bool(raw.get("reject_if_no_json", raw.get("rejectIfNoJson", False))),
        reject_if_missing_evidence=bool(
            raw.get("reject_if_missing_evidence", raw.get("rejectIfMissingEvidence", False))
        ),
        reject_on_parse_failure=bool(
            raw.get("reject_on_parse_failure", raw.get("rejectOnParseFailure", False))
        ),
        evidence_burst_merge_window_minutes=int(
            raw.get(
                "evidence_burst_merge_window_minutes",
                raw.get("evidenceBurstMergeWindowMinutes", 0),
            )
            or 0
        ),
        after_capture=_norm_hook(
            raw.get("after_capture", raw.get("afterCapture")),
        ),
    )


def _norm_hook(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def has_substantive_evidence(evidence: list[str]) -> bool:
    """True if evidence looks like a file path or commit (used for quality tagging)."""
    for item in evidence:
        text = item.strip()
        if not text:
            continue
        lowered = text.lower()
        if "commit" in lowered:
            return True
        compact = text.replace(" ", "")
        if len(compact) >= 7 and all(c in "0123456789abcdefABCDEF" for c in compact[:12]):
            return True
        if "/" in text or "\\" in text:
            return True
        if lowered.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".md", ".json", ".yml", ".yaml")):
            return True
    return False


def run_after_capture_hook(spec: str | None, brain: Any, note_id: str) -> None:
    """Import and call ``module:callable`` with ``(brain, note_id)``."""
    if not spec or not note_id:
        return
    mod_name, sep, func_name = spec.partition(":")
    if not sep or not mod_name.strip() or not func_name.strip():
        logger.warning("Invalid after_capture hook %r (expected 'module:callable')", spec)
        return
    try:
        mod = importlib.import_module(mod_name.strip())
        fn = getattr(mod, func_name.strip(), None)
        if not callable(fn):
            logger.warning("after_capture hook %r is not callable", spec)
            return
        fn(brain, note_id)
    except Exception as exc:
        logger.warning("after_capture hook %r failed: %s", spec, exc)
