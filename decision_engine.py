"""Decision engine for NATLClaw.

Evaluates brain state, events, tasks, and goals each heartbeat to choose
the best action — before the LLM agent is created.  Most routing is
deterministic (pure scoring functions); the LLM is only invoked for
ambiguous, high-stakes decisions.

All scoring logic is persona-agnostic — persona-specific tuning is
provided through :class:`DecisionPolicy`, loaded from the persona manifest.

Public API consumed by scheduler.py
------------------------------------
::

    from decision_engine import (
        evaluate_heartbeat,          # main entry point
        build_decision_context,      # assemble context snapshot
        apply_decision,              # translate to scheduler directives
        record_decision,             # persist a decision as a brain note
        record_decision_outcome,     # close the feedback loop
        DecisionPolicy,              # persona-provided knobs
        DEFAULT_DECISION_POLICY,     # used when persona omits config
        HeartbeatDecision,           # result dataclass
    )

Design principles
-----------------
1. **Deterministic first** — every action candidate gets a numeric score
   computed from brain signals, task state, and event data.  No LLM call
   is needed for the common case.
2. **Confidence-gated** — when the top two candidates score within a
   narrow band (the *ambiguity zone*), the engine can recommend asking the
   developer instead of guessing.
3. **Brain-informed** — decision notes, preference notes, and past
   outcome feedback are first-class scoring inputs.
4. **Initiative-capable** — the engine can propose proactive actions
   (scans, flags, maintenance) even when no task or event is pending.
5. **Policy-driven** — all thresholds, weights, and enabled features
   come from a :class:`DecisionPolicy` that each persona can customize.
6. **Testable** — all scoring functions are pure and accept explicit
   inputs; no global state.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# =====================================================================
# 1. Data Model
# =====================================================================

class ActionType(str, Enum):
    """Enumeration of actions the engine can recommend."""

    # Task actions
    WORK_TASK = "work_task"               # continue or start a task
    BLOCK_TASK = "block_task"             # ask the developer for clarification

    # Workflow actions (no active task)
    RUN_HEARTBEAT = "run_heartbeat"       # normal persona heartbeat
    RUN_CONSOLIDATION = "run_consolidation"  # brain note → wiki page
    RUN_WIKI_LINT = "run_wiki_lint"       # audit wiki quality
    RUN_CONNECTION_SCAN = "run_connection_scan"  # find note relationships

    # Proactive initiative
    INITIATIVE_SECURITY_SCAN = "initiative_security_scan"
    INITIATIVE_PATTERN_CHECK = "initiative_pattern_check"
    INITIATIVE_TODO_SCAN = "initiative_todo_scan"
    INITIATIVE_STALE_REVIEW = "initiative_stale_review"
    INITIATIVE_GOAL_CHECK = "initiative_goal_check"

    # Event-driven
    HANDLE_EVENT = "handle_event"         # react to a specific event

    # Meta
    ASK_DEVELOPER = "ask_developer"       # confidence too low, escalate
    SKIP_CYCLE = "skip_cycle"             # nothing productive to do


@dataclass(frozen=True)
class ActionCandidate:
    """A scored candidate action for this heartbeat.

    Attributes
    ----------
    action : ActionType
        What the engine recommends doing.
    score : float
        Composite score (higher = more urgent/valuable).  Range is
        unbounded but typically 0–100.
    confidence : float
        How confident the engine is in this recommendation (0.0–1.0).
    rationale : str
        One-line human-readable explanation for logging/recording.
    metadata : dict
        Action-specific payload.  For ``WORK_TASK`` this includes the
        task object; for ``HANDLE_EVENT`` the event tuple; etc.
    """

    action: ActionType
    score: float
    confidence: float
    rationale: str
    metadata: dict = field(default_factory=dict)

    def __lt__(self, other: ActionCandidate) -> bool:
        """Sort by score descending (highest first in a max-heap)."""
        return self.score > other.score


_INITIATIVE_TYPES = frozenset({
    ActionType.INITIATIVE_STALE_REVIEW,
    ActionType.INITIATIVE_GOAL_CHECK,
    ActionType.INITIATIVE_PATTERN_CHECK,
    ActionType.INITIATIVE_SECURITY_SCAN,
    ActionType.INITIATIVE_TODO_SCAN,
})


@dataclass(frozen=True)
class EventRoute:
    """How a single event type maps to an action."""

    action: ActionType = ActionType.RUN_HEARTBEAT
    preempt: bool = False
    boost: float = 0.0


@dataclass(frozen=True)
class DecisionPolicy:
    """Persona-provided knobs — the engine reads these, never hardcodes them.

    Loaded from the ``decisions`` block in the persona manifest.  When a
    persona omits the block entirely, :data:`DEFAULT_DECISION_POLICY` is
    used.
    """

    # Task scoring weights
    task_priority_scores: dict[str, float] = field(default_factory=lambda: {
        "urgent": 95.0, "high": 70.0, "medium": 45.0, "low": 20.0,
    })

    # Consolidation triggers
    consolidation_threshold: int = 10
    consolidation_heartbeat_interval: int = 5

    # Connection density target
    connection_density_target: float = 0.5

    # Wiki lint
    lint_heartbeat_interval: int = 20
    lint_issue_boost: float = 15.0

    # Confidence gate
    confidence_threshold: float = 0.80
    ambiguity_margin: float = 5.0

    # Initiative config
    enabled_initiatives: frozenset[ActionType] = field(default_factory=lambda: frozenset({
        ActionType.INITIATIVE_STALE_REVIEW,
        ActionType.INITIATIVE_GOAL_CHECK,
        ActionType.INITIATIVE_PATTERN_CHECK,
    }))
    initiative_cooldown: int = 8
    initiative_ceiling: float = 60.0

    # Event routing overrides (merged on top of DEFAULT_EVENT_ROUTING)
    event_routing: dict[str, EventRoute] = field(default_factory=dict)

    # Global score biases per action type
    action_biases: dict[ActionType, float] = field(default_factory=dict)

    # Brain learning weights
    positive_outcome_boost: float = 3.0
    negative_outcome_penalty: float = 5.0


DEFAULT_DECISION_POLICY = DecisionPolicy()


@dataclass
class DecisionContext:
    """Snapshot of everything the engine needs to evaluate.

    Built by the scheduler at the top of each heartbeat iteration and
    passed into ``evaluate_heartbeat()``.  This keeps the engine a pure
    function with no I/O.
    """

    # Brain state
    note_count: int = 0
    connection_count: int = 0
    page_count: int = 0
    unconsolidated_count: int = 0
    topic_count: int = 0
    last_consolidation: str | None = None
    last_lint: str | None = None
    last_review: str | None = None
    recent_decision_notes: list[dict] = field(default_factory=list)
    recent_preference_notes: list[dict] = field(default_factory=list)
    brain_lint_issues: list[dict] = field(default_factory=list)

    # Task queue
    active_task: Any | None = None        # tasks.Task or None
    pending_tasks: list = field(default_factory=list)
    blocked_tasks: list = field(default_factory=list)

    # Events drained this cycle: (priority, seq, event_type, payload)
    events: list[tuple[int, int, str, dict]] = field(default_factory=list)

    # Goals
    active_goals: list[dict] = field(default_factory=list)

    # History
    heartbeat_number: int = 0
    last_heartbeat_iso: str | None = None
    recent_errors: int = 0                # errors in last 10 heartbeats
    consecutive_empty_heartbeats: int = 0  # heartbeats with 0 new notes

    # Persona
    persona_name: str = ""
    workflow_mode: str = "second_brain"


@dataclass
class HeartbeatDecision:
    """The engine's final recommendation for this heartbeat.

    Attributes
    ----------
    chosen : ActionCandidate
        The winning candidate.
    alternatives : list[ActionCandidate]
        Runner-up candidates (for recording in the decision note).
    should_ask : bool
        True when the confidence gap is inside the ambiguity zone and the
        developer should be consulted.
    supplementary_actions : list[ActionType]
        Additional low-cost actions to run alongside the primary one
        (e.g., run a connection scan after a consolidation).
    decision_id : str
        Deterministic hash for dedup and outcome tracking.
    """

    chosen: ActionCandidate
    alternatives: list[ActionCandidate] = field(default_factory=list)
    should_ask: bool = False
    supplementary_actions: list[ActionType] = field(default_factory=list)
    decision_id: str = ""

    def __post_init__(self) -> None:
        if not self.decision_id:
            raw = f"{self.chosen.action}:{self.chosen.score:.2f}:{self.chosen.rationale}"
            self.decision_id = "d" + hashlib.sha1(raw.encode()).hexdigest()[:8]


# =====================================================================
# 2. Event priority → score mapping (not persona-specific)
# =====================================================================

_EVENT_SCORE = {
    1: 80.0,    # high priority (git commit, CLI command, task mutation)
    2: 40.0,    # medium priority (file changes)
    3: 20.0,    # low priority (default)
}


# =====================================================================
# 3. Scoring Functions (all pure — no I/O, no globals)
# =====================================================================

def _score_task(task, ctx: DecisionContext, policy: DecisionPolicy) -> ActionCandidate:
    """Score continuing or starting work on a task."""
    base = policy.task_priority_scores.get(task.priority, 30.0)

    # Urgency bonus: tasks near their heartbeat limit get a boost
    if task.max_heartbeats > 0:
        remaining_ratio = 1.0 - (task.heartbeats_spent / task.max_heartbeats)
        if remaining_ratio < 0.3:
            base += 15.0  # running out of time
        elif remaining_ratio < 0.5:
            base += 8.0

    # Active task continuity bonus (avoid context-switching)
    if ctx.active_task and task.id == ctx.active_task.id:
        base += 10.0

    # Brain knowledge bonus: if decision notes exist about this task's
    # domain, the agent is better prepared → higher confidence
    confidence = 0.85
    domain_match_count = _count_domain_matches(
        task.title + " " + task.description,
        ctx.recent_decision_notes + ctx.recent_preference_notes,
    )
    if domain_match_count >= 2:
        confidence = min(0.95, confidence + 0.05 * domain_match_count)
        base += domain_match_count * 2.0

    # Blocked task penalty — if the task has unanswered questions, don't pick it
    if task.status == "blocked":
        return ActionCandidate(
            action=ActionType.BLOCK_TASK,
            score=base * 0.1,
            confidence=0.95,
            rationale=f"Task '{task.title}' is blocked awaiting developer answer",
            metadata={"task_id": task.id},
        )

    return ActionCandidate(
        action=ActionType.WORK_TASK,
        score=base,
        confidence=confidence,
        rationale=f"Work on task '{task.title}' (priority={task.priority}, "
                  f"{task.heartbeats_spent}/{task.max_heartbeats} heartbeats)",
        metadata={"task_id": task.id, "task_priority": task.priority},
    )


def _count_domain_matches(text: str, notes: list[dict]) -> int:
    """Count how many notes have keyword overlap with the given text."""
    if not text or not notes:
        return 0
    words = set(text.lower().split())
    if len(words) < 2:
        return 0
    count = 0
    for note in notes:
        note_text = (note.get("content", "") + " " + note.get("summary", "")).lower()
        note_words = set(note_text.split())
        overlap = len(words & note_words)
        if overlap >= 2:
            count += 1
    return count


def _score_events(ctx: DecisionContext, policy: DecisionPolicy) -> list[ActionCandidate]:
    """Score each pending event as a candidate action."""
    candidates = []
    for priority, _seq, event_type, payload in ctx.events:
        base = _EVENT_SCORE.get(priority, 20.0)

        # Task-mutation events get an extra boost
        if event_type in ("task_created", "task_answered", "task_retried"):
            base += 20.0

        # Git commit events boost based on file count
        if event_type == "git_commit":
            file_count = len(payload.get("files", []))
            base += min(file_count * 2.0, 20.0)

        candidates.append(ActionCandidate(
            action=ActionType.HANDLE_EVENT,
            score=base,
            confidence=0.90,
            rationale=f"Handle {event_type} event (priority={priority})",
            metadata={"event_type": event_type, "payload": payload, "priority": priority},
        ))
    return candidates


def _score_consolidation(ctx: DecisionContext, policy: DecisionPolicy) -> ActionCandidate | None:
    """Score whether consolidation should happen this heartbeat."""
    if ctx.page_count == 0 and ctx.note_count < 5:
        return None  # too early for consolidation

    score = 0.0
    reasons = []

    # Threshold-based trigger
    if ctx.unconsolidated_count >= policy.consolidation_threshold:
        score += 30.0 + (ctx.unconsolidated_count - policy.consolidation_threshold) * 2.0
        reasons.append(f"{ctx.unconsolidated_count} unconsolidated notes")

    # Interval-based trigger
    if (ctx.heartbeat_number > 0
            and policy.consolidation_heartbeat_interval > 0
            and ctx.heartbeat_number % policy.consolidation_heartbeat_interval == 0):
        score += 20.0
        reasons.append(f"heartbeat {ctx.heartbeat_number} is consolidation interval")

    # Staleness boost: if last consolidation was long ago
    if ctx.last_consolidation:
        try:
            last_dt = datetime.fromisoformat(ctx.last_consolidation)
            age_hours = (datetime.now(timezone.utc) - last_dt.replace(tzinfo=timezone.utc)).total_seconds() / 3600
            if age_hours > 24:
                score += min(age_hours * 0.5, 15.0)
                reasons.append(f"last consolidation {age_hours:.0f}h ago")
        except (ValueError, TypeError):
            pass

    if score <= 0:
        return None

    return ActionCandidate(
        action=ActionType.RUN_CONSOLIDATION,
        score=score,
        confidence=0.92,
        rationale=f"Consolidate: {'; '.join(reasons)}",
        metadata={"unconsolidated_count": ctx.unconsolidated_count},
    )


def _score_wiki_lint(ctx: DecisionContext, policy: DecisionPolicy) -> ActionCandidate | None:
    """Score whether a wiki lint pass should happen."""
    if ctx.page_count == 0:
        return None

    score = 0.0
    reasons = []

    # Interval-based
    if (ctx.heartbeat_number > 0
            and policy.lint_heartbeat_interval > 0
            and ctx.heartbeat_number % policy.lint_heartbeat_interval == 0):
        score += 25.0
        reasons.append(f"heartbeat {ctx.heartbeat_number} is lint interval")

    # Open lint issues boost
    if ctx.brain_lint_issues:
        issue_count = len(ctx.brain_lint_issues)
        score += min(issue_count * policy.lint_issue_boost, 60.0)
        reasons.append(f"{issue_count} open lint issues")

    if score <= 0:
        return None

    return ActionCandidate(
        action=ActionType.RUN_WIKI_LINT,
        score=score,
        confidence=0.90,
        rationale=f"Wiki lint: {'; '.join(reasons)}",
        metadata={"lint_issue_count": len(ctx.brain_lint_issues)},
    )


def _score_initiative(ctx: DecisionContext, policy: DecisionPolicy) -> list[ActionCandidate]:
    """Generate scored initiative (proactive) candidates.

    Initiative only fires when:
    - No urgent tasks or events are pending
    - The cooldown period since the last initiative has elapsed
    - The initiative type is in ``policy.enabled_initiatives``
    - Specific brain-state triggers are met
    """
    candidates = []

    # Check cooldown via heartbeat number
    last_initiative_hb = _get_last_initiative_heartbeat(ctx.recent_decision_notes)
    if ctx.heartbeat_number - last_initiative_hb < policy.initiative_cooldown:
        return candidates

    # Initiative 1: Stale note review
    # Triggered when many notes exist but connections are sparse
    if ActionType.INITIATIVE_STALE_REVIEW in policy.enabled_initiatives and ctx.note_count > 20:
        ratio = ctx.connection_count / max(ctx.note_count, 1)
        if ratio < 0.3:
            candidates.append(ActionCandidate(
                action=ActionType.INITIATIVE_STALE_REVIEW,
                score=25.0 + (0.3 - ratio) * 50.0,
                confidence=0.88,
                rationale=f"Connection density low ({ratio:.2f}); review orphan notes",
                metadata={"connection_ratio": ratio},
            ))

    # Initiative 2: Goal progress check
    # Triggered when goals are active but haven't been advanced recently
    if ActionType.INITIATIVE_GOAL_CHECK in policy.enabled_initiatives:
        stalled_goals = [
            g for g in ctx.active_goals
            if g.get("status") == "in_progress"
            and g.get("heartbeats_spent", 0) > 0
            and g.get("heartbeats_spent", 0) >= g.get("target_heartbeats", 5) * 0.8
        ]
    else:
        stalled_goals = []
    if stalled_goals:
        candidates.append(ActionCandidate(
            action=ActionType.INITIATIVE_GOAL_CHECK,
            score=30.0 + len(stalled_goals) * 5.0,
            confidence=0.85,
            rationale=f"{len(stalled_goals)} goal(s) near deadline without completion",
            metadata={"stalled_goal_ids": [g["id"] for g in stalled_goals]},
        ))

    # Initiative 3: Pattern check
    # Triggered when the brain has enough notes to find patterns but
    # few pattern-type notes exist
    if ActionType.INITIATIVE_PATTERN_CHECK in policy.enabled_initiatives and ctx.note_count > 30:
        pattern_count = sum(
            1 for n in ctx.recent_decision_notes + ctx.recent_preference_notes
            if n.get("note_type") in ("pattern", "architecture")
        )
        if pattern_count < 3:
            candidates.append(ActionCandidate(
                action=ActionType.INITIATIVE_PATTERN_CHECK,
                score=20.0,
                confidence=0.80,
                rationale="Brain has many notes but few pattern observations",
                metadata={"pattern_note_count": pattern_count},
            ))

    # Initiative 4: Security scan (periodic, low priority)
    if (ActionType.INITIATIVE_SECURITY_SCAN in policy.enabled_initiatives
            and ctx.heartbeat_number > 0 and ctx.heartbeat_number % 50 == 0):
        candidates.append(ActionCandidate(
            action=ActionType.INITIATIVE_SECURITY_SCAN,
            score=18.0,
            confidence=0.75,
            rationale="Periodic security scan (every 50 heartbeats)",
            metadata={},
        ))

    # Initiative 5: TODO scan
    if (ActionType.INITIATIVE_TODO_SCAN in policy.enabled_initiatives
            and ctx.heartbeat_number > 0 and ctx.heartbeat_number % 25 == 0):
        candidates.append(ActionCandidate(
            action=ActionType.INITIATIVE_TODO_SCAN,
            score=15.0,
            confidence=0.78,
            rationale="Periodic TODO/FIXME scan (every 25 heartbeats)",
            metadata={},
        ))

    return candidates


def _get_last_initiative_heartbeat(decision_notes: list[dict]) -> int:
    """Find the heartbeat number of the most recent initiative decision."""
    for note in reversed(decision_notes):
        action = note.get("action", "")
        if action.startswith("initiative_"):
            return note.get("heartbeat", 0)
    return 0


def _score_default_heartbeat(ctx: DecisionContext) -> ActionCandidate:
    """Score the default heartbeat action (persona workflow).

    This is the fallback when nothing else scores higher.
    """
    base = 10.0

    # Boost if there have been empty heartbeats (need productivity)
    if ctx.consecutive_empty_heartbeats >= 3:
        base += 5.0

    # Reduce if there have been many errors (slow down)
    if ctx.recent_errors >= 3:
        base -= 5.0

    return ActionCandidate(
        action=ActionType.RUN_HEARTBEAT,
        score=max(base, 1.0),
        confidence=0.85,
        rationale=f"Default {ctx.workflow_mode} heartbeat",
        metadata={"workflow_mode": ctx.workflow_mode},
    )


def _apply_brain_learning(
    candidates: list[ActionCandidate],
    decision_notes: list[dict],
    policy: DecisionPolicy,
) -> list[ActionCandidate]:
    """Adjust candidate scores based on past decision outcomes.

    For each candidate, look for past decision notes with the same
    action type.  Positive feedback boosts the score; negative feedback
    reduces it.
    """
    if not decision_notes:
        return candidates

    # Build a quick lookup: action_type → cumulative adjustment
    adjustment_map: dict[str, float] = {}
    for note in decision_notes:
        action = note.get("action", "")
        if not action:
            continue
        pos = note.get("positive_feedback", 0)
        neg = note.get("negative_feedback", 0)
        adj = (pos * policy.positive_outcome_boost) - (neg * policy.negative_outcome_penalty)
        adjustment_map[action] = adjustment_map.get(action, 0.0) + adj

    adjusted = []
    for c in candidates:
        adj = adjustment_map.get(c.action.value, 0.0)
        if adj != 0.0:
            # Cap the adjustment to avoid runaway feedback
            adj = max(-20.0, min(20.0, adj))
            adjusted.append(ActionCandidate(
                action=c.action,
                score=c.score + adj,
                confidence=c.confidence,
                rationale=c.rationale + f" [brain adj: {adj:+.1f}]",
                metadata=c.metadata,
            ))
        else:
            adjusted.append(c)
    return adjusted


def _score_connection_scan(ctx: DecisionContext, policy: DecisionPolicy) -> ActionCandidate | None:
    """Score whether a dedicated connection-discovery pass is worthwhile."""
    if ctx.note_count < 4:
        return None

    ratio = ctx.connection_count / max(ctx.note_count, 1)
    target = policy.connection_density_target
    if ratio >= target:
        return None  # already well-connected

    score = 15.0 + (target - ratio) * 30.0

    return ActionCandidate(
        action=ActionType.RUN_CONNECTION_SCAN,
        score=score,
        confidence=0.88,
        rationale=f"Connection density {ratio:.2f} — discover new relationships",
        metadata={"connection_ratio": ratio},
    )


# =====================================================================
# 4. Main Evaluation Function
# =====================================================================

def evaluate_heartbeat(
    ctx: DecisionContext,
    policy: DecisionPolicy | None = None,
) -> HeartbeatDecision:
    """Evaluate all candidate actions and return the best one.

    This is the main entry point called by the scheduler at the top of
    each heartbeat, after loading state/brain/tasks/events but BEFORE
    creating the LLM agent.

    Parameters
    ----------
    ctx : DecisionContext
        Snapshot of the current world state.
    policy : DecisionPolicy or None
        Persona-specific scoring knobs.  Falls back to
        :data:`DEFAULT_DECISION_POLICY` when omitted.

    Returns
    -------
    HeartbeatDecision
        The chosen action, alternatives, and whether to ask the developer.
    """
    if policy is None:
        policy = DEFAULT_DECISION_POLICY

    candidates: list[ActionCandidate] = []

    # --- Task candidates ---
    if ctx.active_task:
        candidates.append(_score_task(ctx.active_task, ctx, policy))
    for task in ctx.pending_tasks:
        if not ctx.active_task or task.id != ctx.active_task.id:
            candidates.append(_score_task(task, ctx, policy))

    # --- Event candidates ---
    candidates.extend(_score_events(ctx, policy))

    # --- Maintenance candidates ---
    cons = _score_consolidation(ctx, policy)
    if cons:
        candidates.append(cons)
    lint = _score_wiki_lint(ctx, policy)
    if lint:
        candidates.append(lint)
    conn_scan = _score_connection_scan(ctx, policy)
    if conn_scan:
        candidates.append(conn_scan)

    # --- Initiative candidates ---
    # Only consider initiative if no high-priority work is pending
    has_urgent = any(c.score >= policy.initiative_ceiling for c in candidates)
    if not has_urgent:
        candidates.extend(_score_initiative(ctx, policy))

    # --- Default heartbeat (always present as fallback) ---
    candidates.append(_score_default_heartbeat(ctx))

    # --- Apply persona action biases ---
    if policy.action_biases:
        biased: list[ActionCandidate] = []
        for c in candidates:
            bias = policy.action_biases.get(c.action, 0.0)
            if bias != 0.0:
                biased.append(ActionCandidate(
                    action=c.action,
                    score=c.score + bias,
                    confidence=c.confidence,
                    rationale=c.rationale,
                    metadata=c.metadata,
                ))
            else:
                biased.append(c)
        candidates = biased

    # --- Apply brain learning adjustments ---
    candidates = _apply_brain_learning(candidates, ctx.recent_decision_notes, policy)

    # --- Sort and select ---
    candidates.sort()  # ActionCandidate.__lt__ sorts by score descending
    chosen = candidates[0]
    alternatives = candidates[1:6]  # keep top 5 alternatives for the record

    # --- Ambiguity / confidence check ---
    should_ask = False
    if len(candidates) >= 2:
        gap = chosen.score - candidates[1].score
        if gap < policy.ambiguity_margin and chosen.confidence < policy.confidence_threshold:
            should_ask = True
            # Replace chosen with ASK_DEVELOPER
            ask_candidate = ActionCandidate(
                action=ActionType.ASK_DEVELOPER,
                score=chosen.score + 1.0,  # slight edge
                confidence=chosen.confidence,
                rationale=(
                    f"Ambiguous: '{chosen.action.value}' ({chosen.score:.1f}) vs "
                    f"'{candidates[1].action.value}' ({candidates[1].score:.1f}); "
                    f"confidence {chosen.confidence:.0%} < {policy.confidence_threshold:.0%}"
                ),
                metadata={
                    "original_choice": chosen.action.value,
                    "runner_up": candidates[1].action.value,
                    "gap": gap,
                },
            )
            alternatives = [chosen] + alternatives[:5]
            chosen = ask_candidate

    # --- Supplementary actions ---
    # Low-cost actions that can ride alongside the primary one
    supplementary = _pick_supplementary(chosen, candidates[1:], ctx)

    decision = HeartbeatDecision(
        chosen=chosen,
        alternatives=alternatives,
        should_ask=should_ask,
        supplementary_actions=supplementary,
    )

    logger.info(
        "Decision: %s (score=%.1f, confidence=%.0f%%) | %s",
        chosen.action.value,
        chosen.score,
        chosen.confidence * 100,
        chosen.rationale,
    )
    if alternatives:
        alt_summary = ", ".join(
            f"{a.action.value}({a.score:.0f})" for a in alternatives[:3]
        )
        logger.debug("Alternatives: %s", alt_summary)

    return decision


def _pick_supplementary(
    chosen: ActionCandidate,
    others: list[ActionCandidate],
    ctx: DecisionContext,
) -> list[ActionType]:
    """Select low-cost supplementary actions to run alongside the primary.

    Rules:
    - Connection scan can always supplement a heartbeat or consolidation
    - Wiki lint can supplement a heartbeat if it's due
    - Never supplement with task work (expensive)
    """
    supplementary: list[ActionType] = []

    cheap_types = {
        ActionType.RUN_CONNECTION_SCAN,
        ActionType.RUN_WIKI_LINT,
    }
    # Don't supplement event handling or task work with anything
    if chosen.action in (ActionType.HANDLE_EVENT, ActionType.WORK_TASK,
                         ActionType.ASK_DEVELOPER, ActionType.SKIP_CYCLE):
        return supplementary

    for candidate in others:
        if candidate.action in cheap_types and candidate.score > 15.0:
            if candidate.action != chosen.action:
                supplementary.append(candidate.action)

    return supplementary[:2]  # at most 2 supplementary actions


def collect_escalation_signals(ctx: DecisionContext) -> list[dict[str, Any]]:
    """Deterministically derive escalation alerts from heartbeat context."""
    escalations: list[dict[str, Any]] = []

    # 1) Repeated bug-fix work pattern.
    bug_tokens = ("bug", "fix", "hotfix", "regression", "incident")
    bug_signal_count = 0
    for _priority, _seq, event_type, payload in ctx.events:
        haystack = f"{event_type} {payload}".lower()
        if any(token in haystack for token in bug_tokens):
            bug_signal_count += 1
    for task in [ctx.active_task, *ctx.pending_tasks]:
        if task is None:
            continue
        text = f"{getattr(task, 'title', '')} {getattr(task, 'description', '')}".lower()
        if any(token in text for token in bug_tokens):
            bug_signal_count += 1
    if bug_signal_count >= 2:
        escalations.append({
            "type": "repeated_bug_work",
            "severity": "high",
            "title": "Repeated bug-fix pattern detected",
            "body": (
                "Recent activity is dominated by bug/fix work. Consider root-cause follow-up "
                "before shipping additional changes."
            ),
            "payload": {"bug_signal_count": bug_signal_count},
        })

    # 2) TODO unchanged despite repeated touches.
    touched_counts: dict[str, int] = {}
    explicit_unchanged = False
    for _priority, _seq, event_type, payload in ctx.events:
        if event_type not in ("file_change", "file_modified"):
            continue
        path = str(payload.get("path", "")).strip()
        if path:
            touched_counts[path] = touched_counts.get(path, 0) + 1
        if payload.get("todo_changed") is False or payload.get("todos_changed") == 0:
            explicit_unchanged = True
    noisy_todo_touch = any(
        count >= 3 and "todo" in path.lower()
        for path, count in touched_counts.items()
    )
    if explicit_unchanged or noisy_todo_touch:
        escalations.append({
            "type": "todo_stagnation",
            "severity": "normal",
            "title": "TODOs appear unchanged despite edits",
            "body": (
                "Files are being touched repeatedly without clear TODO progress. "
                "Re-check task slicing and completion criteria."
            ),
            "payload": {
                "explicit_unchanged": explicit_unchanged,
                "touched_paths": touched_counts,
            },
        })

    # 3) Long inactivity.
    inactivity_hours: float | None = None
    if ctx.last_heartbeat_iso:
        try:
            last = datetime.fromisoformat(ctx.last_heartbeat_iso)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            inactivity_hours = (
                datetime.now(timezone.utc) - last.astimezone(timezone.utc)
            ).total_seconds() / 3600.0
        except (TypeError, ValueError):
            inactivity_hours = None
    if inactivity_hours is not None and inactivity_hours >= 6:
        escalations.append({
            "type": "long_inactivity",
            "severity": "high" if inactivity_hours >= 24 else "normal",
            "title": "Extended inactivity detected",
            "body": (
                f"No heartbeat activity for approximately {inactivity_hours:.1f} hour(s). "
                "Validate scheduler health and unblock next work item."
            ),
            "payload": {"inactivity_hours": round(inactivity_hours, 1)},
        })

    return escalations


# =====================================================================
# 5. Decision Recording (brain notes)
# =====================================================================

def record_decision(
    brain,
    decision: HeartbeatDecision,
    ctx: DecisionContext,
    *,
    add_note_fn=None,
) -> str | None:
    """Record a decision as a brain note with type='decision'.

    Parameters
    ----------
    brain : BrainState
        The brain to write the note to.
    decision : HeartbeatDecision
        The decision to record.
    ctx : DecisionContext
        The context at decision time (for metadata).
    add_note_fn : callable, optional
        Override for ``second_brain.add_note`` (for testing).

    Returns
    -------
    str or None
        The note ID if recorded, None if skipped (e.g., for trivial
        default heartbeats that don't need recording).
    """
    # Don't record trivial default heartbeats
    if (decision.chosen.action == ActionType.RUN_HEARTBEAT
            and decision.chosen.score < 15.0
            and not decision.alternatives):
        return None

    if add_note_fn is None:
        from second_brain import add_note as add_note_fn

    chosen = decision.chosen
    alternatives_text = "; ".join(
        f"{a.action.value}({a.score:.0f})" for a in decision.alternatives[:3]
    )

    content = (
        f"Decision: {chosen.action.value}\n"
        f"Score: {chosen.score:.1f} | Confidence: {chosen.confidence:.0%}\n"
        f"Rationale: {chosen.rationale}\n"
        f"Alternatives considered: {alternatives_text or 'none'}\n"
        f"Heartbeat: {ctx.heartbeat_number}"
    )
    if decision.should_ask:
        content += "\nEscalated to developer (low confidence / ambiguous)"
    if decision.supplementary_actions:
        content += f"\nSupplementary: {', '.join(a.value for a in decision.supplementary_actions)}"

    note_id = add_note_fn(
        brain,
        content,
        summary=f"Decision: {chosen.action.value} (score={chosen.score:.0f})",
        source={
            "type": "decision_engine",
            "heartbeat": ctx.heartbeat_number,
            "decision_id": decision.decision_id,
        },
        note_type="decision",
        status="active",
        confidence=int(chosen.confidence * 100),
        tags=["decision", chosen.action.value],
        category="areas",
    )

    logger.info("Recorded decision as note %s", note_id)
    return note_id


def record_decision_outcome(
    brain,
    decision_note_id: str,
    outcome: str,
    positive: bool,
) -> None:
    """Record the outcome of a past decision, closing the feedback loop.

    Parameters
    ----------
    brain : BrainState
        The brain containing the decision note.
    decision_note_id : str
        ID of the decision note to update.
    outcome : str
        Short description of what happened.
    positive : bool
        True if the outcome was good, False if the decision was wrong
        or produced a bad result.
    """
    note = brain.notes.get(decision_note_id)
    if not note:
        logger.warning("Decision note %s not found for outcome recording", decision_note_id)
        return

    # Update feedback counters
    if positive:
        note["positive_feedback"] = note.get("positive_feedback", 0) + 1
    else:
        note["negative_feedback"] = note.get("negative_feedback", 0) + 1

    # Append to feedback log
    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "outcome": outcome[:300],
        "positive": positive,
    }
    if "feedback_log" not in note:
        note["feedback_log"] = []
    note["feedback_log"].append(log_entry)
    # Keep log bounded
    if len(note["feedback_log"]) > 12:
        note["feedback_log"] = note["feedback_log"][-12:]

    note["updated_at"] = datetime.now(timezone.utc).isoformat()
    logger.info(
        "Recorded %s outcome for decision %s: %s",
        "positive" if positive else "negative",
        decision_note_id,
        outcome[:80],
    )


# =====================================================================
# 6. Event Routing Rules
# =====================================================================

# Maps event types to recommended ActionType and whether they should
# pre-empt the current heartbeat.
EVENT_ROUTING: dict[str, dict] = {
    # Task lifecycle events → wake and work on the task
    "task_created": {
        "action": ActionType.WORK_TASK,
        "preempt": True,
        "score_boost": 25.0,
    },
    "task_answered": {
        "action": ActionType.WORK_TASK,
        "preempt": True,
        "score_boost": 30.0,    # unblocked tasks are high value
    },
    "task_retried": {
        "action": ActionType.WORK_TASK,
        "preempt": True,
        "score_boost": 20.0,
    },
    "task_cancelled": {
        "action": ActionType.RUN_HEARTBEAT,
        "preempt": False,
        "score_boost": 0.0,
    },

    # Git events → knowledge capture opportunity
    "git_commit": {
        "action": ActionType.RUN_HEARTBEAT,
        "preempt": False,
        "score_boost": 15.0,
    },

    # CLI commands → respond promptly
    "cli_command": {
        "action": ActionType.HANDLE_EVENT,
        "preempt": True,
        "score_boost": 20.0,
    },

    # File changes → background awareness (no preemption)
    "file_change": {
        "action": ActionType.RUN_HEARTBEAT,
        "preempt": False,
        "score_boost": 5.0,
    },
    "file_created": {
        "action": ActionType.RUN_HEARTBEAT,
        "preempt": False,
        "score_boost": 8.0,
    },
    "file_modified": {
        "action": ActionType.RUN_HEARTBEAT,
        "preempt": False,
        "score_boost": 5.0,
    },
    "file_deleted": {
        "action": ActionType.RUN_HEARTBEAT,
        "preempt": False,
        "score_boost": 5.0,
    },
}


def route_event(event_type: str, payload: dict) -> dict:
    """Look up routing info for an event type.

    Returns a dict with ``action``, ``preempt``, and ``score_boost``
    keys.  Falls back to a default non-preempting heartbeat for unknown
    event types.
    """
    return EVENT_ROUTING.get(event_type, {
        "action": ActionType.RUN_HEARTBEAT,
        "preempt": False,
        "score_boost": 0.0,
    })


def has_preempting_event(
    events: list[tuple[int, int, str, dict]],
    policy: DecisionPolicy | None = None,
) -> bool:
    """Check whether any event in the batch should pre-empt the sleep timer."""
    merged = {**EVENT_ROUTING}
    if policy and policy.event_routing:
        for k, v in policy.event_routing.items():
            merged[k] = {"action": v.action, "preempt": v.preempt, "score_boost": v.boost}
    for _priority, _seq, event_type, _payload in events:
        routing = merged.get(event_type, {"preempt": False})
        if routing.get("preempt") or routing.get("preempt") is True:
            return True
    return False


# =====================================================================
# 7. Context Builder (scheduler integration helper)
# =====================================================================

def build_decision_context(
    state,
    brain,
    tasks: list,
    outbox: list,
    events: list[tuple[int, int, str, dict]],
    persona,
    *,
    get_active_task_fn=None,
    get_pending_tasks_fn=None,
    get_blocked_tasks_fn=None,
    get_active_goals_fn=None,
    search_notes_fn=None,
) -> DecisionContext:
    """Build a DecisionContext from the loaded scheduler state.

    This is a convenience function that the scheduler calls to assemble
    the context struct.  Each dependency can be overridden for testing.

    Parameters
    ----------
    state : AgentState
    brain : BrainState
    tasks : list[Task]
    outbox : list[Message]
    events : list of (priority, seq, event_type, payload)
    persona : Persona
    """
    # Lazy imports to avoid circular deps at module level
    if get_active_task_fn is None:
        from tasks import get_active_task as get_active_task_fn
    if get_pending_tasks_fn is None:
        from tasks import get_pending_tasks as get_pending_tasks_fn
    if get_blocked_tasks_fn is None:
        from tasks import get_blocked_tasks as get_blocked_tasks_fn
    if get_active_goals_fn is None:
        from goals import get_active_goals as get_active_goals_fn
    if search_notes_fn is None:
        from second_brain import search_notes as search_notes_fn

    # Fetch decision notes and preference notes from the brain
    recent_decisions = search_notes_fn(brain, "decision", max_results=15)
    recent_preferences = search_notes_fn(brain, "preference", max_results=10)

    # Count recent errors from execution log (lightweight heuristic)
    recent_errors = 0
    try:
        from execution_log import recent_entries
        entries = recent_entries(limit=10)
        recent_errors = sum(
            1 for e in entries
            if "ERROR" in (e.get("response", "") or "")[:200].upper()
        )
    except Exception:
        pass

    # Consecutive empty heartbeats — tracked in state.context
    consecutive_empty = state.context.get("_consecutive_empty_heartbeats", 0)

    # Latest lint issues
    lint_issues = []
    if brain.lint_log:
        latest_lint = brain.lint_log[-1]
        lint_issues = latest_lint.get("issues", [])

    from second_brain import get_unconsolidated_notes
    unconsolidated = get_unconsolidated_notes(brain)

    return DecisionContext(
        note_count=len(brain.notes),
        connection_count=len(brain.connections),
        page_count=len(brain.pages),
        unconsolidated_count=len(unconsolidated),
        topic_count=len(brain.topics),
        last_consolidation=brain.last_consolidation,
        last_lint=brain.last_lint,
        last_review=brain.last_review,
        recent_decision_notes=recent_decisions,
        recent_preference_notes=recent_preferences,
        brain_lint_issues=lint_issues,
        active_task=get_active_task_fn(tasks, persona.name),
        pending_tasks=get_pending_tasks_fn(tasks, persona.name),
        blocked_tasks=get_blocked_tasks_fn(tasks),
        events=events,
        active_goals=get_active_goals_fn(state),
        heartbeat_number=state.execution_count,
        last_heartbeat_iso=state.last_heartbeat,
        recent_errors=recent_errors,
        consecutive_empty_heartbeats=consecutive_empty,
        persona_name=persona.name,
        workflow_mode=getattr(persona, "workflow", "second_brain"),
    )


# =====================================================================
# 8. Scheduler Integration Hooks
# =====================================================================

def apply_decision(
    decision: HeartbeatDecision,
    state,
    brain,
    tasks: list,
    outbox: list,
    persona,
    config,
    ctx: DecisionContext | None = None,
) -> dict:
    """Translate a HeartbeatDecision into scheduler-actionable directives.

    Returns a dict consumed by the scheduler's heartbeat loop:

    .. code-block:: python

        {
            "action": "work_task" | "run_heartbeat" | ...,
            "active_task": Task | None,
            "workflow_override": str | None,
            "skip_agent": bool,
            "extra_context": str,         # injected into agent instructions
            "outbox_messages": [Message],  # messages to append
        }
    """
    from messaging import emit_alert, emit_escalation_alert

    result = {
        "action": decision.chosen.action.value,
        "active_task": None,
        "workflow_override": None,
        "skip_agent": False,
        "extra_context": "",
        "outbox_messages": [],
    }

    chosen = decision.chosen

    if chosen.action == ActionType.WORK_TASK:
        # Find the task to work on
        task_id = chosen.metadata.get("task_id", "")
        if task_id:
            from tasks import find_task, assign_task, start_task
            task = find_task(tasks, task_id)
            if task:
                if task.status == "pending":
                    assign_task(task, persona.name)
                    start_task(task)
                    from messaging import emit_task_started
                    result["outbox_messages"].append(
                        emit_task_started(task, persona=persona.name,
                                          heartbeat=state.execution_count)
                    )
                result["active_task"] = task

    elif chosen.action == ActionType.ASK_DEVELOPER:
        # Emit a question to the outbox
        result["outbox_messages"].append(emit_alert(
            title="Decision engine: need developer input",
            body=chosen.rationale,
            urgency="high",
            persona=persona.name,
            heartbeat=state.execution_count,
            payload=chosen.metadata,
        ))
        # Fall through to default heartbeat
        result["action"] = ActionType.RUN_HEARTBEAT.value

    elif chosen.action == ActionType.SKIP_CYCLE:
        result["skip_agent"] = True

    elif chosen.action in (ActionType.RUN_CONSOLIDATION, ActionType.RUN_WIKI_LINT,
                           ActionType.RUN_CONNECTION_SCAN):
        # These are handled as workflow overrides
        result["workflow_override"] = chosen.action.value

    elif chosen.action.value.startswith("initiative_"):
        # Initiative actions get injected as extra context for the heartbeat
        result["extra_context"] = _build_initiative_prompt(chosen)
        result["action"] = ActionType.RUN_HEARTBEAT.value

    # Add supplementary action hints to extra_context
    if decision.supplementary_actions:
        supp_text = ", ".join(a.value for a in decision.supplementary_actions)
        result["extra_context"] += (
            f"\n\n[Decision engine supplementary: also consider {supp_text} if time permits]"
        )

    if ctx is not None:
        for escalation in collect_escalation_signals(ctx):
            result["outbox_messages"].append(
                emit_escalation_alert(
                    escalation_type=escalation["type"],
                    title=escalation["title"],
                    body=escalation["body"],
                    severity=escalation["severity"],
                    persona=persona.name,
                    heartbeat=state.execution_count,
                    payload=escalation.get("payload", {}),
                )
            )

    return result


def _build_initiative_prompt(candidate: ActionCandidate) -> str:
    """Build an initiative prompt to inject into the agent instructions."""
    prompts = {
        ActionType.INITIATIVE_SECURITY_SCAN: (
            "\n\n== INITIATIVE: SECURITY SCAN ==\n"
            "While reviewing the codebase, flag any security concerns, "
            "missing error handling, or code that contradicts known conventions. "
            "Record findings as notes with tag 'security'."
        ),
        ActionType.INITIATIVE_PATTERN_CHECK: (
            "\n\n== INITIATIVE: PATTERN CHECK ==\n"
            "Check if recently changed files broke existing patterns "
            "or introduced inconsistencies with the architecture notes in the brain. "
            "Record findings as notes with tag 'pattern'."
        ),
        ActionType.INITIATIVE_TODO_SCAN: (
            "\n\n== INITIATIVE: TODO SCAN ==\n"
            "Look for TODO/FIXME/HACK comments added in the last 24 hours "
            "and assess whether any are urgent enough to raise to the developer. "
            "Record findings as notes with tag 'todo'."
        ),
        ActionType.INITIATIVE_STALE_REVIEW: (
            "\n\n== INITIATIVE: STALE NOTE REVIEW ==\n"
            "Review orphan notes (no connections) and stale notes (older than 30 days). "
            "For each, decide: connect to another note, update, or archive. "
            "Focus on improving connection density."
        ),
        ActionType.INITIATIVE_GOAL_CHECK: (
            "\n\n== INITIATIVE: GOAL PROGRESS CHECK ==\n"
            "Active goals are approaching their deadline without completion. "
            "Assess whether each stalled goal should be: advanced, re-planned, "
            "or abandoned. Record your assessment."
        ),
    }
    return prompts.get(candidate.action, "")


def update_consecutive_empty(state, notes_created: int) -> None:
    """Track consecutive empty heartbeats in state.context for the engine."""
    if notes_created == 0:
        state.context["_consecutive_empty_heartbeats"] = (
            state.context.get("_consecutive_empty_heartbeats", 0) + 1
        )
    else:
        state.context["_consecutive_empty_heartbeats"] = 0
