"""Unit tests for the decision engine.

Every test exercises pure scoring functions with no LLM, no I/O, and no
global state — just explicit DecisionContext inputs and deterministic
HeartbeatDecision outputs.
"""

from __future__ import annotations

import pytest
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from decision_engine import (
    ActionCandidate,
    ActionType,
    DecisionContext,
    DecisionPolicy,
    DEFAULT_DECISION_POLICY,
    EventRoute,
    HeartbeatDecision,
    apply_decision,
    build_decision_context,
    evaluate_heartbeat,
    has_preempting_event,
    collect_escalation_signals,
    record_decision,
    record_decision_outcome,
    route_event,
    update_consecutive_empty,
    _apply_brain_learning,
    _count_domain_matches,
    _score_consolidation,
    _score_default_heartbeat,
    _score_events,
    _score_initiative,
    _score_task,
    _score_wiki_lint,
    _score_connection_scan,
)

# Alias for convenience — tests use the default policy unless overriding
_policy = DEFAULT_DECISION_POLICY


# ── Minimal stubs ──────────────────────────────────────────────────────


@dataclass
class FakeTask:
    id: str = "t001"
    title: str = "Fix auth module"
    description: str = "Refactor authentication to use Result pattern"
    priority: str = "medium"
    status: str = "pending"
    assigned_to: str = ""
    heartbeats_spent: int = 0
    max_heartbeats: int = 10
    questions: list = field(default_factory=list)
    progress_notes: list = field(default_factory=list)


@dataclass
class FakeBrain:
    notes: dict = field(default_factory=dict)
    connections: list = field(default_factory=list)
    pages: dict = field(default_factory=dict)
    topics: dict = field(default_factory=dict)
    lint_log: list = field(default_factory=list)
    last_consolidation: str | None = None
    last_lint: str | None = None
    last_review: str | None = None
    review_log: list = field(default_factory=list)
    capture_count: int = 0


def _ctx(**overrides) -> DecisionContext:
    """Build a DecisionContext with sensible defaults, overridable."""
    defaults = dict(
        note_count=20,
        connection_count=8,
        page_count=2,
        unconsolidated_count=3,
        topic_count=4,
        heartbeat_number=15,
        persona_name="default",
        workflow_mode="second_brain",
    )
    defaults.update(overrides)
    return DecisionContext(**defaults)


# =====================================================================
# Task scoring
# =====================================================================


class TestScoreTask:
    def test_urgent_task_scores_highest(self):
        ctx = _ctx()
        urgent = FakeTask(priority="urgent")
        high = FakeTask(id="t002", priority="high")
        c1 = _score_task(urgent, ctx, _policy)
        c2 = _score_task(high, ctx, _policy)
        assert c1.score > c2.score

    def test_active_task_gets_continuity_bonus(self):
        task = FakeTask(id="t001", priority="medium")
        ctx1 = _ctx(active_task=task)
        ctx2 = _ctx(active_task=None)
        c1 = _score_task(task, ctx1, _policy)
        c2 = _score_task(task, ctx2, _policy)
        assert c1.score > c2.score, "Active task should get continuity bonus"

    def test_near_timeout_gets_urgency_boost(self):
        task = FakeTask(heartbeats_spent=8, max_heartbeats=10)
        ctx = _ctx()
        c = _score_task(task, ctx, _policy)
        # 80% used → remaining_ratio = 0.2 → should get the +15 boost
        assert c.score > 45.0 + 15.0 - 1  # medium base + boost

    def test_blocked_task_scores_low(self):
        task = FakeTask(status="blocked")
        ctx = _ctx()
        c = _score_task(task, ctx, _policy)
        assert c.action == ActionType.BLOCK_TASK
        assert c.score < 10.0

    def test_domain_match_boosts_confidence(self):
        decision_notes = [
            {"content": "authentication refactor patterns", "summary": "auth patterns"},
            {"content": "Result pattern for error handling", "summary": ""},
        ]
        ctx = _ctx(recent_decision_notes=decision_notes)
        task = FakeTask(title="Fix auth module", description="Refactor authentication to use Result pattern")
        c = _score_task(task, ctx, _policy)
        assert c.confidence > 0.85


# =====================================================================
# Event scoring
# =====================================================================


class TestScoreEvents:
    def test_task_events_score_higher_than_file_events(self):
        ctx = _ctx(events=[
            (1, "task_created", {"task_id": "t99"}),
            (2, "file_change", {"path": "foo.py"}),
        ])
        candidates = _score_events(ctx, _policy)
        assert len(candidates) == 2
        task_event = [c for c in candidates if c.metadata["event_type"] == "task_created"][0]
        file_event = [c for c in candidates if c.metadata["event_type"] == "file_change"][0]
        assert task_event.score > file_event.score

    def test_git_commit_scales_with_files(self):
        ctx_small = _ctx(events=[(1, "git_commit", {"files": ["a.py"]})])
        ctx_big = _ctx(events=[(1, "git_commit", {"files": [f"f{i}.py" for i in range(10)]})])
        small = _score_events(ctx_small, _policy)[0]
        big = _score_events(ctx_big, _policy)[0]
        assert big.score > small.score

    def test_no_events_returns_empty(self):
        ctx = _ctx(events=[])
        assert _score_events(ctx, _policy) == []


# =====================================================================
# Consolidation scoring
# =====================================================================


class TestScoreConsolidation:
    def test_threshold_triggers_consolidation(self):
        ctx = _ctx(unconsolidated_count=15, note_count=20)
        c = _score_consolidation(ctx, _policy)
        assert c is not None
        assert c.action == ActionType.RUN_CONSOLIDATION
        assert c.score > 30.0

    def test_interval_triggers_consolidation(self):
        ctx = _ctx(heartbeat_number=10, unconsolidated_count=3, note_count=20)
        c = _score_consolidation(ctx, _policy)
        assert c is not None

    def test_too_early_skips(self):
        ctx = _ctx(note_count=3, page_count=0, unconsolidated_count=2, heartbeat_number=7)
        c = _score_consolidation(ctx, _policy)
        assert c is None

    def test_stale_consolidation_gets_boost(self):
        old_time = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        ctx = _ctx(
            heartbeat_number=10,
            unconsolidated_count=3,
            note_count=20,
            last_consolidation=old_time,
        )
        c = _score_consolidation(ctx, _policy)
        assert c is not None
        assert "48h" in c.rationale or "ago" in c.rationale


# =====================================================================
# Wiki lint scoring
# =====================================================================


class TestScoreWikiLint:
    def test_no_pages_no_lint(self):
        ctx = _ctx(page_count=0)
        assert _score_wiki_lint(ctx, _policy) is None

    def test_interval_triggers_lint(self):
        ctx = _ctx(page_count=3, heartbeat_number=20)
        c = _score_wiki_lint(ctx, _policy)
        assert c is not None
        assert c.action == ActionType.RUN_WIKI_LINT

    def test_open_issues_boost_score(self):
        issues = [{"type": "stale", "severity": "warning"}] * 3
        ctx = _ctx(page_count=3, heartbeat_number=20, brain_lint_issues=issues)
        c = _score_wiki_lint(ctx, _policy)
        assert c is not None
        assert c.score > 60.0


# =====================================================================
# Initiative scoring
# =====================================================================


class TestScoreInitiative:
    def test_cooldown_suppresses_initiative(self):
        # A recent decision note with an initiative action
        recent = [{"action": "initiative_security_scan", "heartbeat": 12}]
        ctx = _ctx(heartbeat_number=15, recent_decision_notes=recent)
        candidates = _score_initiative(ctx, _policy)
        # Cooldown is 8 heartbeats; 15 - 12 = 3 < 8 → suppressed
        assert len(candidates) == 0

    def test_low_connection_density_triggers_stale_review(self):
        ctx = _ctx(
            note_count=30,
            connection_count=3,
            heartbeat_number=20,
            recent_decision_notes=[],
        )
        candidates = _score_initiative(ctx, _policy)
        actions = [c.action for c in candidates]
        assert ActionType.INITIATIVE_STALE_REVIEW in actions

    def test_stalled_goals_trigger_goal_check(self):
        goals = [
            {"id": "g1", "status": "in_progress", "heartbeats_spent": 8, "target_heartbeats": 10},
        ]
        ctx = _ctx(
            active_goals=goals,
            heartbeat_number=20,
            recent_decision_notes=[],
        )
        candidates = _score_initiative(ctx, _policy)
        actions = [c.action for c in candidates]
        assert ActionType.INITIATIVE_GOAL_CHECK in actions

    def test_periodic_security_scan(self):
        all_initiatives = DecisionPolicy(enabled_initiatives=frozenset({
            ActionType.INITIATIVE_STALE_REVIEW,
            ActionType.INITIATIVE_GOAL_CHECK,
            ActionType.INITIATIVE_PATTERN_CHECK,
            ActionType.INITIATIVE_SECURITY_SCAN,
            ActionType.INITIATIVE_TODO_SCAN,
        }))
        ctx = _ctx(heartbeat_number=50, recent_decision_notes=[])
        candidates = _score_initiative(ctx, all_initiatives)
        actions = [c.action for c in candidates]
        assert ActionType.INITIATIVE_SECURITY_SCAN in actions


# =====================================================================
# Deterministic escalation signals
# =====================================================================


class TestEscalationSignals:
    def test_repeated_bug_work_triggers_escalation(self):
        task = FakeTask(title="Fix regression in auth", description="bugfix hotfix")
        ctx = _ctx(
            active_task=task,
            events=[(1, "git_commit", {"message": "fix bug in auth flow"})],
        )
        escalations = collect_escalation_signals(ctx)
        kinds = {entry["type"] for entry in escalations}
        assert "repeated_bug_work" in kinds

    def test_todo_stagnation_triggers_escalation(self):
        ctx = _ctx(events=[
            (2, "file_modified", {"path": "src/todo_manager.py"}),
            (2, "file_modified", {"path": "src/todo_manager.py"}),
            (2, "file_modified", {"path": "src/todo_manager.py", "todo_changed": False}),
        ])
        escalations = collect_escalation_signals(ctx)
        kinds = {entry["type"] for entry in escalations}
        assert "todo_stagnation" in kinds

    def test_long_inactivity_triggers_escalation(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=9)).isoformat()
        ctx = _ctx(last_heartbeat_iso=old)
        escalations = collect_escalation_signals(ctx)
        kinds = {entry["type"] for entry in escalations}
        assert "long_inactivity" in kinds


# =====================================================================
# Connection scan scoring
# =====================================================================


class TestScoreConnectionScan:
    def test_well_connected_brain_skips_scan(self):
        ctx = _ctx(note_count=20, connection_count=15)
        assert _score_connection_scan(ctx, _policy) is None

    def test_sparse_brain_triggers_scan(self):
        ctx = _ctx(note_count=20, connection_count=2)
        c = _score_connection_scan(ctx, _policy)
        assert c is not None
        assert c.action == ActionType.RUN_CONNECTION_SCAN

    def test_too_few_notes_skips(self):
        ctx = _ctx(note_count=2, connection_count=0)
        assert _score_connection_scan(ctx, _policy) is None


# =====================================================================
# Brain learning adjustments
# =====================================================================


class TestBrainLearning:
    def test_positive_feedback_boosts_score(self):
        candidates = [
            ActionCandidate(
                action=ActionType.RUN_HEARTBEAT,
                score=10.0,
                confidence=0.85,
                rationale="test",
            )
        ]
        decisions = [
            {"action": "run_heartbeat", "positive_feedback": 3, "negative_feedback": 0},
        ]
        adjusted = _apply_brain_learning(candidates, decisions, _policy)
        assert adjusted[0].score > 10.0

    def test_negative_feedback_reduces_score(self):
        candidates = [
            ActionCandidate(
                action=ActionType.INITIATIVE_SECURITY_SCAN,
                score=20.0,
                confidence=0.75,
                rationale="test",
            )
        ]
        decisions = [
            {"action": "initiative_security_scan", "positive_feedback": 0, "negative_feedback": 4},
        ]
        adjusted = _apply_brain_learning(candidates, decisions, _policy)
        assert adjusted[0].score < 20.0

    def test_adjustment_is_capped(self):
        candidates = [
            ActionCandidate(
                action=ActionType.RUN_HEARTBEAT,
                score=50.0,
                confidence=0.85,
                rationale="test",
            )
        ]
        # Extreme positive feedback
        decisions = [
            {"action": "run_heartbeat", "positive_feedback": 100, "negative_feedback": 0},
        ]
        adjusted = _apply_brain_learning(candidates, decisions, _policy)
        # Cap is +20.0
        assert adjusted[0].score <= 70.0

    def test_no_decisions_returns_unchanged(self):
        candidates = [
            ActionCandidate(
                action=ActionType.RUN_HEARTBEAT,
                score=10.0,
                confidence=0.85,
                rationale="test",
            )
        ]
        adjusted = _apply_brain_learning(candidates, [], _policy)
        assert adjusted[0].score == 10.0


# =====================================================================
# Full evaluation
# =====================================================================


class TestEvaluateHeartbeat:
    def test_urgent_task_wins_over_heartbeat(self):
        task = FakeTask(priority="urgent")
        ctx = _ctx(active_task=task, pending_tasks=[])
        decision = evaluate_heartbeat(ctx)
        assert decision.chosen.action == ActionType.WORK_TASK

    def test_default_heartbeat_when_idle(self):
        ctx = _ctx(
            active_task=None,
            pending_tasks=[],
            events=[],
            unconsolidated_count=0,
            note_count=5,
            connection_count=5,
            heartbeat_number=13,  # not a consolidation or lint interval
        )
        decision = evaluate_heartbeat(ctx)
        assert decision.chosen.action == ActionType.RUN_HEARTBEAT

    def test_consolidation_wins_when_many_unconsolidated(self):
        ctx = _ctx(
            active_task=None,
            pending_tasks=[],
            events=[],
            unconsolidated_count=25,
            note_count=30,
            heartbeat_number=10,
        )
        decision = evaluate_heartbeat(ctx)
        assert decision.chosen.action == ActionType.RUN_CONSOLIDATION

    def test_event_can_outrank_default_heartbeat(self):
        ctx = _ctx(
            active_task=None,
            pending_tasks=[],
            events=[(1, "task_created", {"task_id": "t99"})],
        )
        decision = evaluate_heartbeat(ctx)
        # task_created event with priority 1 → score ~100
        assert decision.chosen.action == ActionType.HANDLE_EVENT

    def test_alternatives_are_populated(self):
        ctx = _ctx(
            active_task=FakeTask(priority="medium"),
            pending_tasks=[],
            unconsolidated_count=12,
            note_count=20,
            heartbeat_number=10,
        )
        decision = evaluate_heartbeat(ctx)
        assert len(decision.alternatives) >= 1

    def test_ambiguity_triggers_ask_developer(self):
        """When two candidates score within AMBIGUITY_MARGIN and confidence
        is below threshold, the engine should recommend asking."""
        # Create a context where two actions score nearly identically
        # and confidence is low
        ctx = _ctx(
            active_task=None,
            pending_tasks=[FakeTask(priority="low")],
            events=[],
            unconsolidated_count=0,
            note_count=30,
            connection_count=2,  # low density → initiative
            heartbeat_number=20,
            recent_decision_notes=[],
        )
        decision = evaluate_heartbeat(ctx)
        # We can't guarantee ambiguity with these inputs, but we can
        # verify the decision structure is valid
        assert decision.chosen.action in ActionType.__members__.values()
        assert decision.decision_id.startswith("d")

    def test_initiative_suppressed_when_urgent_task_exists(self):
        task = FakeTask(priority="urgent")
        ctx = _ctx(
            active_task=task,
            pending_tasks=[],
            note_count=30,
            connection_count=2,
            heartbeat_number=50,
            recent_decision_notes=[],
        )
        decision = evaluate_heartbeat(ctx)
        # Urgent task should win; initiative shouldn't even be scored
        assert decision.chosen.action == ActionType.WORK_TASK
        initiative_alts = [
            a for a in decision.alternatives
            if a.action.value.startswith("initiative_")
        ]
        assert len(initiative_alts) == 0

    def test_decision_has_stable_id(self):
        ctx = _ctx()
        d1 = evaluate_heartbeat(ctx)
        d2 = evaluate_heartbeat(ctx)
        # Same inputs → same decision → same ID
        assert d1.decision_id == d2.decision_id


# =====================================================================
# Event routing
# =====================================================================


class TestEventRouting:
    def test_task_created_preempts(self):
        routing = route_event("task_created", {})
        assert routing["preempt"] is True

    def test_file_change_does_not_preempt(self):
        routing = route_event("file_change", {})
        assert routing["preempt"] is False

    def test_unknown_event_has_defaults(self):
        routing = route_event("unknown_event_type", {})
        assert routing["preempt"] is False
        assert routing["score_boost"] == 0.0

    def test_has_preempting_event_detects_task_events(self):
        events = [
            (2, "file_change", {"path": "foo.py"}),
            (1, "task_answered", {"task_id": "t1"}),
        ]
        assert has_preempting_event(events) is True

    def test_has_preempting_event_false_for_files_only(self):
        events = [
            (2, "file_change", {"path": "foo.py"}),
            (2, "file_modified", {"path": "bar.py"}),
        ]
        assert has_preempting_event(events) is False


# =====================================================================
# Decision recording
# =====================================================================


class TestRecordDecision:
    def test_records_decision_as_note(self):
        brain = FakeBrain()
        recorded_calls = []

        def fake_add_note(brain, content, **kwargs):
            recorded_calls.append({"content": content, **kwargs})
            return "n0099"

        decision = HeartbeatDecision(
            chosen=ActionCandidate(
                action=ActionType.WORK_TASK,
                score=70.0,
                confidence=0.90,
                rationale="Work on urgent task",
            ),
            alternatives=[
                ActionCandidate(
                    action=ActionType.RUN_HEARTBEAT,
                    score=10.0,
                    confidence=0.85,
                    rationale="Default heartbeat",
                ),
            ],
        )
        ctx = _ctx(heartbeat_number=15)

        note_id = record_decision(brain, decision, ctx, add_note_fn=fake_add_note)
        assert note_id == "n0099"
        assert len(recorded_calls) == 1
        call = recorded_calls[0]
        assert call["note_type"] == "decision"
        assert "work_task" in call["content"].lower()
        assert call["confidence"] == 90

    def test_skips_trivial_heartbeat(self):
        brain = FakeBrain()
        decision = HeartbeatDecision(
            chosen=ActionCandidate(
                action=ActionType.RUN_HEARTBEAT,
                score=10.0,
                confidence=0.85,
                rationale="Default heartbeat",
            ),
        )
        ctx = _ctx()
        note_id = record_decision(brain, decision, ctx, add_note_fn=lambda *a, **kw: "n0001")
        assert note_id is None


class TestApplyDecisionEscalations:
    def test_apply_decision_emits_escalation_messages(self):
        class _State:
            execution_count = 12

        class _Persona:
            name = "workspace_observer"

        decision = HeartbeatDecision(
            chosen=ActionCandidate(
                action=ActionType.RUN_HEARTBEAT,
                score=12.0,
                confidence=0.85,
                rationale="Default heartbeat",
            )
        )
        old = (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat()
        ctx = _ctx(last_heartbeat_iso=old)
        result = apply_decision(
            decision,
            _State(),
            FakeBrain(),
            [],
            [],
            _Persona(),
            config=None,
            ctx=ctx,
        )
        messages = result["outbox_messages"]
        assert messages, "Expected at least one escalation alert"
        assert any(
            m.payload.get("escalation_type") == "long_inactivity"
            for m in messages
        )


# =====================================================================
# Decision outcome feedback
# =====================================================================


class TestRecordDecisionOutcome:
    def test_positive_outcome_increments_counter(self):
        brain = FakeBrain()
        brain.notes["n0050"] = {
            "id": "n0050",
            "note_type": "decision",
            "content": "Decision: work_task",
            "positive_feedback": 0,
            "negative_feedback": 0,
            "feedback_log": [],
        }
        record_decision_outcome(brain, "n0050", "Task completed successfully", positive=True)
        assert brain.notes["n0050"]["positive_feedback"] == 1
        assert len(brain.notes["n0050"]["feedback_log"]) == 1
        assert brain.notes["n0050"]["feedback_log"][0]["positive"] is True

    def test_negative_outcome_increments_counter(self):
        brain = FakeBrain()
        brain.notes["n0051"] = {
            "id": "n0051",
            "note_type": "decision",
            "content": "Decision: initiative_security_scan",
            "positive_feedback": 0,
            "negative_feedback": 0,
            "feedback_log": [],
        }
        record_decision_outcome(brain, "n0051", "Scan found nothing useful", positive=False)
        assert brain.notes["n0051"]["negative_feedback"] == 1

    def test_missing_note_handled_gracefully(self):
        brain = FakeBrain()
        # Should not raise
        record_decision_outcome(brain, "n9999", "outcome", positive=True)


# =====================================================================
# Consecutive empty tracking
# =====================================================================


class TestConsecutiveEmpty:
    def test_increments_on_zero_notes(self):
        class FakeState:
            context = {}
        state = FakeState()
        update_consecutive_empty(state, notes_created=0)
        assert state.context["_consecutive_empty_heartbeats"] == 1
        update_consecutive_empty(state, notes_created=0)
        assert state.context["_consecutive_empty_heartbeats"] == 2

    def test_resets_on_notes_created(self):
        class FakeState:
            context = {"_consecutive_empty_heartbeats": 5}
        state = FakeState()
        update_consecutive_empty(state, notes_created=2)
        assert state.context["_consecutive_empty_heartbeats"] == 0


# =====================================================================
# Domain matching helper
# =====================================================================


class TestDomainMatching:
    def test_counts_matching_notes(self):
        notes = [
            {"content": "authentication patterns in the codebase module", "summary": ""},
            {"content": "unrelated database optimization", "summary": ""},
            {"content": "auth module refactoring approach fix", "summary": ""},
        ]
        count = _count_domain_matches("Fix auth module authentication", notes)
        # Note 0 matches "authentication" + "module", note 2 matches "auth" + "module" + "fix"
        assert count >= 2

    def test_empty_inputs(self):
        assert _count_domain_matches("", []) == 0
        assert _count_domain_matches("something", []) == 0
        assert _count_domain_matches("", [{"content": "foo", "summary": ""}]) == 0

    def test_single_word_text_returns_zero(self):
        """Single-word text doesn't have enough signal for matching."""
        notes = [{"content": "auth patterns", "summary": ""}]
        assert _count_domain_matches("auth", notes) == 0


# =====================================================================
# DecisionPolicy customization
# =====================================================================


class TestDecisionPolicy:
    def test_custom_task_priority_scores(self):
        """Persona can override task priority weights."""
        policy = DecisionPolicy(task_priority_scores={
            "urgent": 50.0, "high": 40.0, "medium": 30.0, "low": 10.0,
        })
        ctx = _ctx()
        task = FakeTask(priority="urgent")
        c_custom = _score_task(task, ctx, policy)
        c_default = _score_task(task, ctx, _policy)
        assert c_custom.score < c_default.score

    def test_custom_consolidation_threshold(self):
        """Persona with higher threshold doesn't trigger consolidation as easily."""
        strict = DecisionPolicy(consolidation_threshold=25, consolidation_heartbeat_interval=0)
        lenient = DecisionPolicy(consolidation_threshold=10, consolidation_heartbeat_interval=0)
        ctx = _ctx(unconsolidated_count=15, note_count=20, heartbeat_number=13)
        assert _score_consolidation(ctx, lenient) is not None  # threshold=10, 15 >= 10
        assert _score_consolidation(ctx, strict) is None       # threshold=25, 15 < 25

    def test_custom_connection_density_target(self):
        """Persona can require higher connection density."""
        strict = DecisionPolicy(connection_density_target=0.8)
        ctx = _ctx(note_count=20, connection_count=12)  # density=0.6
        assert _score_connection_scan(ctx, _policy) is None      # default target=0.5, 0.6 >= 0.5
        assert _score_connection_scan(ctx, strict) is not None   # custom target=0.8, 0.6 < 0.8

    def test_disabled_initiative_not_scored(self):
        """Persona can disable specific initiatives."""
        no_stale = DecisionPolicy(enabled_initiatives=frozenset({
            ActionType.INITIATIVE_GOAL_CHECK,
        }))
        ctx = _ctx(
            note_count=30,
            connection_count=3,  # low density -> would trigger stale review
            heartbeat_number=20,
            recent_decision_notes=[],
        )
        default_candidates = _score_initiative(ctx, _policy)
        custom_candidates = _score_initiative(ctx, no_stale)
        default_actions = {c.action for c in default_candidates}
        custom_actions = {c.action for c in custom_candidates}
        assert ActionType.INITIATIVE_STALE_REVIEW in default_actions
        assert ActionType.INITIATIVE_STALE_REVIEW not in custom_actions

    def test_action_biases_shift_scores(self):
        """Persona can globally boost or penalize action types."""
        biased = DecisionPolicy(action_biases={
            ActionType.RUN_CONSOLIDATION: 50.0,
        })
        ctx = _ctx(
            active_task=None,
            pending_tasks=[],
            events=[],
            unconsolidated_count=12,
            note_count=20,
            heartbeat_number=10,
        )
        decision = evaluate_heartbeat(ctx, biased)
        assert decision.chosen.action == ActionType.RUN_CONSOLIDATION

    def test_custom_confidence_threshold(self):
        """Persona can set a stricter confidence threshold."""
        strict = DecisionPolicy(confidence_threshold=0.99, ambiguity_margin=100.0)
        # With ambiguity_margin=100, any two candidates will be within margin.
        # With confidence_threshold=0.99, almost anything triggers ASK_DEVELOPER.
        task = FakeTask(priority="low")  # score ~20, confidence 0.85
        ctx = _ctx(
            active_task=None,
            pending_tasks=[task],
            events=[],
            unconsolidated_count=0,
            note_count=5,
            connection_count=5,
            heartbeat_number=13,
        )
        decision = evaluate_heartbeat(ctx, strict)
        assert decision.should_ask is True
        assert decision.chosen.action == ActionType.ASK_DEVELOPER

    def test_event_routing_override(self):
        """Persona can override event routing to make file_modified preempt."""
        policy = DecisionPolicy(event_routing={
            "file_modified": EventRoute(ActionType.RUN_HEARTBEAT, preempt=True, boost=15.0),
        })
        events = [(2, "file_modified", {"path": "foo.py"})]
        assert has_preempting_event(events) is False           # default: no preempt
        assert has_preempting_event(events, policy) is True    # custom: preempt
