"""Tests for CodeIntel-style FP/TP calibration in the learning module.

Covers:
  1. Confidence floor computation (ported from CodeIntel Rule.cs)
  2. Confidence bonus computation (positive reinforcement)
  3. Auto-FP detection for structured JSON responses
  4. Fingerprint-based dedup within a heartbeat
  5. Manual mark_lesson_outcome feedback loop
  6. Calibration integration with extract_lessons
  7. Backwards compatibility (no state passed)
"""
from __future__ import annotations

import json

import pytest

from learning import (
    CALIBRATION_MIN_SAMPLES,
    build_context_block,
    compute_confidence_bonus,
    compute_confidence_floor,
    extract_lessons,
    mark_lesson_outcome,
    _fingerprint,
    _is_structured_response,
    _rule_key,
)
from state import AgentState


# ═══════════════════════════════════════════════════════════════════════
# 1. Confidence floor tiers (ported from CodeIntel Rule.cs)
# ═══════════════════════════════════════════════════════════════════════

class TestConfidenceFloor:
    """Verify the floor tiers match CodeIntel exactly."""

    def test_below_min_samples_returns_zero(self):
        assert compute_confidence_floor(2, 2) == 0  # total=4 < 5

    def test_low_fp_rate_no_adjustment(self):
        assert compute_confidence_floor(0, 10) == 0  # 0% FP

    def test_20_percent_fp_returns_70(self):
        assert compute_confidence_floor(2, 8) == 70  # 20% FP

    def test_40_percent_fp_returns_80(self):
        assert compute_confidence_floor(4, 6) == 80  # 40% FP

    def test_60_percent_fp_returns_90(self):
        assert compute_confidence_floor(6, 4) == 90  # 60% FP

    def test_70_percent_fp_returns_93(self):
        assert compute_confidence_floor(7, 3) == 93  # 70% FP

    def test_80_percent_fp_returns_95(self):
        assert compute_confidence_floor(8, 2) == 95  # 80% FP

    def test_90_percent_fp_returns_97(self):
        assert compute_confidence_floor(9, 1) == 97  # 90% FP

    def test_100_percent_fp_returns_97(self):
        assert compute_confidence_floor(10, 0) == 97  # 100% FP


# ═══════════════════════════════════════════════════════════════════════
# 2. Confidence bonus (positive reinforcement)
# ═══════════════════════════════════════════════════════════════════════

class TestConfidenceBonus:
    """Verify bonus tiers match CodeIntel."""

    def test_below_min_samples_returns_zero(self):
        assert compute_confidence_bonus(2, 2) == 0

    def test_low_tp_rate_no_bonus(self):
        assert compute_confidence_bonus(5, 5) == 0  # 50% TP

    def test_80_percent_tp_returns_5(self):
        assert compute_confidence_bonus(2, 8) == 5  # 80% TP

    def test_90_percent_tp_returns_10(self):
        assert compute_confidence_bonus(1, 9) == 10  # 90% TP

    def test_95_percent_tp_returns_15(self):
        assert compute_confidence_bonus(0, 10) == 15  # 100% TP

    def test_exactly_at_threshold(self):
        """Exactly at CALIBRATION_MIN_SAMPLES total."""
        assert compute_confidence_bonus(1, 4) == 5  # 80% TP, total=5


# ═══════════════════════════════════════════════════════════════════════
# 3. Auto-FP detection for structured JSON
# ═══════════════════════════════════════════════════════════════════════

class TestAutoFPDetection:
    """Structured JSON responses should NOT trigger error/warning lessons."""

    def test_json_with_error_word_is_not_an_error(self):
        """The exact scenario from the bug: capture returns JSON containing 'error' in content."""
        state = AgentState()
        response = json.dumps({
            "topic": "Explainability Techniques in Autonomous AI Agents",
            "content": "Explainability methods, such as attention heatmaps and error: analysis "
                       "in deep learning models, help interpret agent decisions.",
            "tags": ["explainability", "agents"],
            "category": "resources",
        })
        lessons = extract_lessons("capture", "some prompt", response, state=state)

        error_lessons = [l for l in lessons if l["type"] == "error_encountered"]
        assert len(error_lessons) == 0, (
            f"Structured JSON response should not produce error lessons: {error_lessons}"
        )

    def test_json_with_failed_to_is_not_an_error(self):
        """JSON content mentioning 'failed to' in educational context."""
        state = AgentState()
        response = json.dumps({
            "topic": "Agent Failure Modes",
            "content": "Agents often failed to generalize when training data is limited.",
            "tags": ["failure", "agents"],
            "category": "resources",
        })
        lessons = extract_lessons("capture", "prompt", response, state=state)

        assert all(l["type"] != "error_encountered" for l in lessons)

    def test_json_with_success_still_detected(self):
        """Success patterns in JSON ARE genuine — the step succeeded AND returned JSON."""
        state = AgentState()
        response = json.dumps({
            "topic": "Deploy",
            "content": "Deployment completed successfully using blue-green strategy.",
            "tags": ["deploy"],
            "category": "areas",
        })
        lessons = extract_lessons("capture", "prompt", response, state=state)

        success_lessons = [l for l in lessons if l["type"] == "success_achieved"]
        assert len(success_lessons) == 1

    def test_json_with_code_fences_still_detected_as_structured(self):
        """JSON wrapped in markdown code fences is still structured."""
        state = AgentState()
        response = '```json\n{"topic": "Error handling", "content": "Error: this is fine"}\n```'
        lessons = extract_lessons("capture", "prompt", response, state=state)

        assert all(l["type"] != "error_encountered" for l in lessons)

    def test_non_json_error_still_detected(self):
        """Plain text errors are still caught normally."""
        state = AgentState()
        response = "Error: failed to connect to the database cluster"
        lessons = extract_lessons("deploy", "prompt", response, state=state)

        error_lessons = [l for l in lessons if l["type"] == "error_encountered"]
        assert len(error_lessons) == 1

    def test_auto_fp_increments_fp_counter(self):
        """Auto-FP detection records the FP in calibration state."""
        state = AgentState()
        response = json.dumps({
            "content": "Analysis of common error: patterns in distributed systems",
        })
        extract_lessons("capture", "prompt", response, state=state)

        rk = _rule_key("error_encountered", "capture")
        assert rk in state.lesson_calibration
        assert state.lesson_calibration[rk]["fp"] >= 1


# ═══════════════════════════════════════════════════════════════════════
# 4. Fingerprint dedup
# ═══════════════════════════════════════════════════════════════════════

class TestFingerprintDedup:
    """Identical lessons within the same heartbeat should be deduplicated."""

    def test_same_response_twice_deduped(self):
        """Calling extract_lessons twice with same response uses fingerprint dedup."""
        state = AgentState()
        seen: set[str] = set()
        response = "Error: failed to connect to service"

        l1 = extract_lessons("deploy", "p1", response, state=state, _seen_fps=seen)
        l2 = extract_lessons("deploy", "p2", response, state=state, _seen_fps=seen)

        assert len(l1) >= 1
        assert len(l2) == 0, "Second extraction should be deduped by fingerprint"

    def test_different_steps_not_deduped(self):
        """Same response in different steps produces distinct fingerprints."""
        state = AgentState()
        seen: set[str] = set()
        response = "Error: failed to connect to service"

        l1 = extract_lessons("deploy", "p1", response, state=state, _seen_fps=seen)
        l2 = extract_lessons("rollback", "p2", response, state=state, _seen_fps=seen)

        assert len(l1) >= 1
        assert len(l2) >= 1, "Different steps should NOT be deduped"

    def test_fingerprint_deterministic(self):
        """Same inputs always produce the same fingerprint."""
        fp1 = _fingerprint("error_encountered", "deploy", "Error: connection refused")
        fp2 = _fingerprint("error_encountered", "deploy", "Error: connection refused")
        assert fp1 == fp2

    def test_fingerprint_differs_by_type(self):
        fp1 = _fingerprint("error_encountered", "deploy", "Some text")
        fp2 = _fingerprint("warning_noted", "deploy", "Some text")
        assert fp1 != fp2


# ═══════════════════════════════════════════════════════════════════════
# 5. Confidence floor suppression
# ═══════════════════════════════════════════════════════════════════════

class TestCalibrationSuppression:
    """After enough FP observations, low-confidence lessons get suppressed."""

    def test_high_fp_rule_suppresses_lessons(self):
        """A rule with >60% FP rate gets floor=90, suppressing base_confidence=60 errors."""
        state = AgentState()
        rk = _rule_key("error_encountered", "deploy")
        # Pre-seed with 8 FP and 2 TP — 80% FP rate → floor=95
        state.lesson_calibration[rk] = {
            "fp": 8, "tp": 2,
            "confidence_floor": compute_confidence_floor(8, 2),
            "confidence_bonus": compute_confidence_bonus(8, 2),
        }

        response = "Error: connection timed out"
        lessons = extract_lessons("deploy", "prompt", response, state=state)

        # Error base_confidence=60, bonus=0, so effective=60 < floor=95 → suppressed
        error_lessons = [l for l in lessons if l["type"] == "error_encountered"]
        assert len(error_lessons) == 0, "High-FP rule should suppress error lessons"

    def test_high_tp_rule_boosts_confidence(self):
        """A rule with >95% TP gets bonus=15, boosting confidence to 75."""
        state = AgentState()
        rk = _rule_key("error_encountered", "deploy")
        # Pre-seed with 0 FP and 20 TP — 100% TP → bonus=15
        state.lesson_calibration[rk] = {
            "fp": 0, "tp": 20,
            "confidence_floor": compute_confidence_floor(0, 20),
            "confidence_bonus": compute_confidence_bonus(0, 20),
        }

        response = "Error: connection timed out"
        lessons = extract_lessons("deploy", "prompt", response, state=state)

        error_lessons = [l for l in lessons if l["type"] == "error_encountered"]
        assert len(error_lessons) == 1
        assert error_lessons[0]["confidence"] == 75  # base 60 + bonus 15

    def test_uncalibrated_rule_uses_base_confidence(self):
        """A rule with <5 samples uses base confidence (no floor/bonus)."""
        state = AgentState()
        response = "Task completed successfully"
        lessons = extract_lessons("deploy", "prompt", response, state=state)

        success_lessons = [l for l in lessons if l["type"] == "success_achieved"]
        assert len(success_lessons) == 1
        assert success_lessons[0]["confidence"] == 80  # base for success


# ═══════════════════════════════════════════════════════════════════════
# 6. Manual feedback (mark_lesson_outcome)
# ═══════════════════════════════════════════════════════════════════════

class TestManualFeedback:
    """mark_lesson_outcome mirrors CodeIntel's finding status transitions."""

    def test_mark_false_positive(self):
        state = AgentState()
        result = mark_lesson_outcome(state, "error_encountered", "capture", is_false_positive=True)

        assert result["fp"] == 1
        assert result["tp"] == 0

    def test_mark_true_positive(self):
        state = AgentState()
        result = mark_lesson_outcome(state, "error_encountered", "deploy", is_false_positive=False)

        assert result["tp"] == 1
        assert result["fp"] == 0

    def test_accumulated_marks_recalibrate(self):
        """After enough marks, the floor adjusts."""
        state = AgentState()
        for _ in range(8):
            mark_lesson_outcome(state, "warning_noted", "lint", is_false_positive=True)
        for _ in range(2):
            mark_lesson_outcome(state, "warning_noted", "lint", is_false_positive=False)

        rk = _rule_key("warning_noted", "lint")
        cal = state.lesson_calibration[rk]
        assert cal["fp"] == 8
        assert cal["tp"] == 2
        assert cal["confidence_floor"] == 95  # 80% FP rate


# ═══════════════════════════════════════════════════════════════════════
# 7. Context block includes calibration summary
# ═══════════════════════════════════════════════════════════════════════

class TestContextBlockCalibration:
    """build_context_block shows calibration data for rules with enough samples."""

    def test_calibration_summary_in_context(self):
        state = AgentState(execution_count=10)
        rk = _rule_key("error_encountered", "capture")
        state.lesson_calibration[rk] = {
            "fp": 7, "tp": 3,
            "confidence_floor": compute_confidence_floor(7, 3),
            "confidence_bonus": compute_confidence_bonus(7, 3),
        }

        context = build_context_block(state)
        assert "Lesson calibration:" in context
        assert "error_encountered_capture" in context
        assert "FP=7/10" in context

    def test_uncalibrated_rules_not_shown(self):
        """Rules below min samples don't appear in the summary."""
        state = AgentState(execution_count=1)
        rk = _rule_key("error_encountered", "deploy")
        state.lesson_calibration[rk] = {"fp": 1, "tp": 1}

        context = build_context_block(state)
        assert "Lesson calibration:" not in context

    def test_confidence_shown_in_lesson_lines(self):
        state = AgentState(
            execution_count=5,
            lessons_learned=[{
                "type": "error_encountered",
                "step": "deploy",
                "description": "Error during deploy",
                "confidence": 75,
                "timestamp": "2026-04-08T10:00:00Z",
            }],
        )
        context = build_context_block(state)
        assert "(conf=75)" in context


# ═══════════════════════════════════════════════════════════════════════
# 8. Backwards compatibility
# ═══════════════════════════════════════════════════════════════════════

class TestBackwardsCompat:
    """extract_lessons without state works exactly as before (minus description format)."""

    def test_no_state_still_extracts_errors(self):
        lessons = extract_lessons(
            "deploy", "prompt",
            "Error: failed to connect to database"
        )
        assert len(lessons) >= 1
        assert lessons[0]["type"] == "error_encountered"

    def test_no_state_still_extracts_success(self):
        lessons = extract_lessons(
            "test", "prompt",
            "All tests completed successfully"
        )
        assert len(lessons) >= 1
        assert lessons[0]["type"] == "success_achieved"

    def test_no_state_no_calibration_side_effects(self):
        """Without state, no calibration data is mutated."""
        lessons = extract_lessons(
            "capture", "prompt",
            json.dumps({"content": "error: something"})
        )
        # Without state, auto-FP still skips error lessons for structured JSON
        # but there's no state to record the FP in
        error_lessons = [l for l in lessons if l["type"] == "error_encountered"]
        assert len(error_lessons) == 0


# ═══════════════════════════════════════════════════════════════════════
# 9. Structured response detection
# ═══════════════════════════════════════════════════════════════════════

class TestStructuredResponseDetection:
    """_is_structured_response correctly identifies JSON objects/arrays."""

    def test_json_object(self):
        assert _is_structured_response('{"key": "value"}')

    def test_json_array(self):
        assert _is_structured_response('[1, 2, 3]')

    def test_json_in_code_fence(self):
        assert _is_structured_response('```json\n{"key": "val"}\n```')

    def test_plain_text(self):
        assert not _is_structured_response("This is just text")

    def test_partial_json(self):
        assert not _is_structured_response('{"key": "val')

    def test_json_string_not_object(self):
        assert not _is_structured_response('"just a string"')
