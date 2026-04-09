"""Lesson extraction with CodeIntel-style FP/TP calibration.

Mirrors CodeIntel's ``Rule.cs`` approach:
- Each (lesson_type, step) pair is a **rule** with FP/TP counters.
- After ``CALIBRATION_MIN_SAMPLES`` acknowledgements the confidence
  floor rises for noisy rules and a bonus is added for reliable ones.
- Structured JSON responses are auto-classified as false positives
  when they match error patterns only on *content* words, not on
  actual execution failures.
- Fingerprint-based dedup prevents the same lesson from being recorded
  twice in a single heartbeat.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from state import AgentState

logger = logging.getLogger(__name__)

# ── Pattern definitions ────────────────────────────────────────────────

LESSON_PATTERNS: dict[str, dict] = {
    "error_encountered": {
        "patterns": ("failed to", "exception occurred", "error:", "traceback", "crash"),
        "base_confidence": 60,
    },
    "success_achieved": {
        "patterns": ("completed successfully", "task done", "finished successfully"),
        "base_confidence": 80,
    },
    "warning_noted": {
        "patterns": ("warning:", "caution:", "\u26a0"),
        "base_confidence": 50,
    },
}

# ── Calibration constants (ported from CodeIntel Rule.cs) ──────────────

CALIBRATION_MIN_SAMPLES = 5
"""Minimum total FP+TP acknowledgements before calibration kicks in."""


def compute_confidence_floor(fp_count: int, tp_count: int) -> int:
    """Compute the calibrated confidence floor from cumulative FP/TP counts.

    Port of ``Rule.ComputeConfidenceFloor`` in CodeIntel.  Higher FP rates
    produce higher floors, suppressing low-confidence lessons from noisy rules.

    Tiers (applied when total >= CALIBRATION_MIN_SAMPLES):
        FP rate < 20%  -> 0  (no adjustment)
        FP rate 20-39% -> 70
        FP rate 40-59% -> 80
        FP rate 60-69% -> 90
        FP rate 70-79% -> 93
        FP rate 80-89% -> 95
        FP rate >= 90%  -> 97
    """
    total = fp_count + tp_count
    if total < CALIBRATION_MIN_SAMPLES:
        return 0

    fp_rate = fp_count / total

    if fp_rate >= 0.90:
        return 97
    if fp_rate >= 0.80:
        return 95
    if fp_rate >= 0.70:
        return 93
    if fp_rate >= 0.60:
        return 90
    if fp_rate >= 0.40:
        return 80
    if fp_rate >= 0.20:
        return 70
    return 0


def compute_confidence_bonus(fp_count: int, tp_count: int) -> int:
    """Compute a confidence bonus for rules with high true-positive rates.

    Port of ``Rule.ComputeConfidenceBonus`` in CodeIntel — rewards rules
    that users (or auto-detection) consistently confirm as genuine.

    Tiers (applied when total >= CALIBRATION_MIN_SAMPLES):
        TP rate < 80%  -> 0
        TP rate 80-89% -> 5
        TP rate 90-94% -> 10
        TP rate >= 95%  -> 15
    """
    total = fp_count + tp_count
    if total < CALIBRATION_MIN_SAMPLES:
        return 0

    tp_rate = tp_count / total

    if tp_rate >= 0.95:
        return 15
    if tp_rate >= 0.90:
        return 10
    if tp_rate >= 0.80:
        return 5
    return 0


# ── Helpers ────────────────────────────────────────────────────────────

def _rule_key(lesson_type: str, step: str) -> str:
    """Build the calibration dict key for a (type, step) pair."""
    return f"{lesson_type}_{step}"


def _fingerprint(lesson_type: str, step: str, description: str) -> str:
    """SHA-256 fingerprint of (type + step + first 80 chars of description).

    Used for within-heartbeat dedup — same idea as CodeIntel's
    ``Finding.Fingerprint`` of (RuleId + FilePath + LineStart).
    """
    payload = f"{lesson_type}|{step}|{description[:80]}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _is_structured_response(text: str) -> bool:
    """Return True if *text* looks like a valid JSON object/array.

    Structured responses (capture notes, consolidation updates, connection
    JSON) can contain words like "error" or "failed to" in their *content*
    without indicating an actual execution failure.
    """
    stripped = text.strip()
    # Strip markdown code fences if present
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else stripped[3:]
        stripped = stripped.rsplit("```", 1)[0].strip()
    try:
        obj = json.loads(stripped)
        return isinstance(obj, (dict, list))
    except (json.JSONDecodeError, ValueError):
        return False


# ── Core extraction ────────────────────────────────────────────────────

def extract_lessons(
    step: str,
    prompt: str,
    response: str,
    *,
    state: AgentState | None = None,
    _seen_fps: set[str] | None = None,
) -> list[dict]:
    """Extract lessons from an agent interaction, with calibration.

    Parameters
    ----------
    step:
        Workflow step name (e.g. "capture", "consolidate", "review").
    prompt:
        The prompt that was sent (unused for now, reserved for future
        context-aware extraction).
    response:
        The raw LLM response text.
    state:
        If provided, calibration data is read and auto-FP detections are
        recorded.  Pass ``None`` for uncalibrated extraction (backwards
        compat with existing callers and tests).
    _seen_fps:
        Internal set of fingerprints already emitted this heartbeat.
        Used for dedup across multiple calls within the same cycle.
    """
    lessons: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()
    resp_lower = response.lower()

    # Auto-FP: if the response is structured JSON, pattern matches on
    # content words are false positives — the step succeeded.
    is_structured = _is_structured_response(response)

    calibration = state.lesson_calibration if state else {}
    seen = _seen_fps if _seen_fps is not None else set()

    for lesson_type, rule_def in LESSON_PATTERNS.items():
        if not any(p in resp_lower for p in rule_def["patterns"]):
            continue

        rk = _rule_key(lesson_type, step)
        cal = calibration.get(rk, {"fp": 0, "tp": 0})
        fp_count = cal.get("fp", 0)
        tp_count = cal.get("tp", 0)

        # ── Auto-FP classification ──────────────────────────────────
        # Error/warning patterns inside structured JSON content are FP.
        # Success patterns inside structured JSON are still genuine
        # (the step both succeeded AND returned valid JSON).
        if is_structured and lesson_type in ("error_encountered", "warning_noted"):
            if state is not None:
                fp_count += 1
                calibration[rk] = _recalibrate(fp_count, tp_count)
                logger.debug(
                    "[%s] Auto-FP: '%s' pattern in structured JSON response (fp=%d)",
                    step, lesson_type, fp_count,
                )
            continue  # skip emitting this lesson

        # ── Confidence scoring ──────────────────────────────────────
        floor = compute_confidence_floor(fp_count, tp_count)
        bonus = compute_confidence_bonus(fp_count, tp_count)
        confidence = min(rule_def["base_confidence"] + bonus, 100)

        if confidence < floor:
            logger.debug(
                "[%s] Suppressed '%s': confidence %d < floor %d (fp=%d, tp=%d)",
                step, lesson_type, confidence, floor, fp_count, tp_count,
            )
            continue

        # ── Fingerprint dedup ───────────────────────────────────────
        desc = f"{lesson_type.replace('_', ' ').title()} during '{step}': {response[:120]}"
        fp_hash = _fingerprint(lesson_type, step, desc)
        if fp_hash in seen:
            continue
        seen.add(fp_hash)

        # ── Emit lesson ─────────────────────────────────────────────
        lessons.append({
            "type": lesson_type,
            "step": step,
            "description": desc,
            "confidence": confidence,
            "fingerprint": fp_hash,
            "timestamp": now,
        })

        # Auto-TP: the lesson passed all filters, record as true positive
        if state is not None:
            tp_count += 1
            calibration[rk] = _recalibrate(fp_count, tp_count)

    return lessons


def _recalibrate(fp_count: int, tp_count: int) -> dict:
    """Build the calibration entry dict for a rule."""
    return {
        "fp": fp_count,
        "tp": tp_count,
        "confidence_floor": compute_confidence_floor(fp_count, tp_count),
        "confidence_bonus": compute_confidence_bonus(fp_count, tp_count),
    }


# ── Manual feedback ────────────────────────────────────────────────────

def mark_lesson_outcome(
    state: AgentState,
    lesson_type: str,
    step: str,
    is_false_positive: bool,
) -> dict:
    """Record a manual FP/TP acknowledgement for a lesson rule.

    Returns the updated calibration entry.  Mirrors the flow in CodeIntel
    where a user marks a finding as ``FalsePositive`` or ``Resolved``.
    """
    rk = _rule_key(lesson_type, step)
    cal = state.lesson_calibration.get(rk, {"fp": 0, "tp": 0})
    if is_false_positive:
        cal["fp"] = cal.get("fp", 0) + 1
    else:
        cal["tp"] = cal.get("tp", 0) + 1
    state.lesson_calibration[rk] = _recalibrate(cal["fp"], cal["tp"])
    logger.info(
        "Lesson rule '%s' marked %s (fp=%d, tp=%d, floor=%d, bonus=%d)",
        rk,
        "FP" if is_false_positive else "TP",
        state.lesson_calibration[rk]["fp"],
        state.lesson_calibration[rk]["tp"],
        state.lesson_calibration[rk]["confidence_floor"],
        state.lesson_calibration[rk]["confidence_bonus"],
    )
    return state.lesson_calibration[rk]


# ── Context block builder ─────────────────────────────────────────────

def build_context_block(state: AgentState, max_recent: int = 5) -> str:
    """Build a context string from memory and lessons to inject into the prompt."""
    lines = ["== AGENT MEMORY =="]
    lines.append(f"Last heartbeat: {state.last_heartbeat or 'never'}")
    lines.append(f"Total executions: {state.execution_count}")

    # Recent lessons (only those above the confidence floor)
    recent_lessons = state.lessons_learned[-max_recent:]
    if recent_lessons:
        lines.append("\nRecent lessons:")
        for lesson in recent_lessons:
            conf = lesson.get("confidence", "?")
            lines.append(
                f"  - [{lesson.get('type')}] (conf={conf}) "
                f"{lesson.get('description', '')[:100]}"
            )

    # Calibration summary — show rules with enough data
    noisy_rules = [
        (rk, cal)
        for rk, cal in state.lesson_calibration.items()
        if cal.get("fp", 0) + cal.get("tp", 0) >= CALIBRATION_MIN_SAMPLES
    ]
    if noisy_rules:
        lines.append("\nLesson calibration:")
        for rk, cal in noisy_rules:
            total = cal["fp"] + cal["tp"]
            fp_pct = round(cal["fp"] / total * 100, 1) if total else 0
            lines.append(
                f"  - {rk}: FP={cal['fp']}/{total} ({fp_pct}%) "
                f"floor={cal.get('confidence_floor', 0)} "
                f"bonus={cal.get('confidence_bonus', 0)}"
            )

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
