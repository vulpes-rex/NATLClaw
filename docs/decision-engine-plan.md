# Decision Engine for NATLClaw

## Problem

The brain stores memories but never reasons over them to make decisions. Every heartbeat runs the same fixed cycle regardless of what the brain knows, what events arrived, or what worked before. Task selection is FIFO, workflow dispatch is static, and the agent can't take initiative.

## Approach: Deterministic Scoring Layer + Brain-Backed Learning

Add a single new module `decision_engine.py` that runs at the top of each heartbeat, **before** the LLM agent is created. It evaluates all possible actions as scored candidates, picks the winner, and returns a structured directive the scheduler acts on. Past decisions are stored as brain notes and their outcomes feed back into future scoring.

No new LLM calls. No replacement of the persona system. The engine is a pure-function scoring layer that makes the scheduler smarter.

### Separation of Concerns: Engine vs. Persona

The decision engine is **generic** -- it contains scoring algorithms, the evaluation loop, brain learning math, and the confidence gate. All persona-specific tuning lives outside the engine in a `DecisionPolicy` that each persona provides through its manifest config.

```
persona config (mcp.json / persona.json)
    ↓
load_decision_policy(persona) → DecisionPolicy
    ↓
evaluate_heartbeat(ctx, policy) → HeartbeatDecision
```

The engine never imports persona-specific logic. It reads the policy struct.

---

## Data Model

### ActionType (enum)
```
WORK_TASK           # Pick up or continue a task
RUN_HEARTBEAT       # Normal persona heartbeat cycle
RUN_CONSOLIDATION   # Promote notes to wiki pages
RUN_WIKI_LINT       # Audit wiki quality
RUN_CONNECTION_SCAN # Discover note relationships
INITIATIVE_STALE_REVIEW    # Proactive: review low-connectivity notes
INITIATIVE_GOAL_CHECK      # Proactive: check stalled goals
INITIATIVE_PATTERN_CHECK   # Proactive: flag missing patterns
INITIATIVE_SECURITY_SCAN   # Proactive: periodic security audit
INITIATIVE_TODO_SCAN       # Proactive: triage TODO items
HANDLE_EVENT        # React to a specific event
ASK_DEVELOPER       # Confidence too low, escalate
SKIP_CYCLE          # Nothing productive to do
```

### ActionCandidate (frozen dataclass)
```python
action: ActionType
score: float          # 0-100, higher = more urgent
confidence: float     # 0.0-1.0, how sure the engine is
rationale: str        # human-readable explanation
metadata: dict        # action-specific (task_id, event_type, etc.)
```

### DecisionPolicy (frozen dataclass) -- persona provides this
```python
# Scoring weights
task_priority_scores: dict[str, float]    # {"urgent": 95, "high": 70, ...}
consolidation_threshold: int               # unconsolidated notes before trigger
connection_density_target: float           # below this, connection scan fires

# Confidence gate
confidence_threshold: float                # ask-vs-decide gate (default 0.80)
ambiguity_margin: float                    # top-2 score gap (default 5.0)

# Initiative config
enabled_initiatives: frozenset[ActionType] # which initiatives this persona supports
initiative_cooldown: int                   # heartbeats between initiatives
initiative_ceiling: float                  # only fire when no candidate > this

# Event routing overrides
event_routing: dict[str, EventRoute]       # override defaults per event type

# Scoring biases
action_biases: dict[ActionType, float]     # global boost/penalize per action type
```

### DecisionContext (dataclass) -- read-only snapshot
```python
# Brain signals
note_count: int
connection_count: int
page_count: int
unconsolidated_count: int
connection_density: float    # connections / notes
recent_decision_notes: list[dict]  # past decisions with feedback

# Task signals
active_task: Task | None
pending_tasks: list[Task]
blocked_tasks: list[Task]

# Event signals
pending_events: list[tuple[int, str, dict]]

# History signals
heartbeat_number: int
consecutive_empty_heartbeats: int  # tracked in state.context
recent_errors: int
last_consolidation_age_hours: float
last_lint_age_hours: float
last_initiative_heartbeat: int     # cooldown tracking

# Persona info
persona_name: str
workflow_mode: str

# Goals
active_goals: list[dict]
stalled_goals: list[dict]  # goals near heartbeat limit without completion
```

### HeartbeatDecision (dataclass) -- output
```python
chosen: ActionCandidate
alternatives: list[ActionCandidate]  # top 5 runners-up
should_ask: bool                     # confidence gate triggered
supplementary_actions: list[ActionType]  # low-cost add-ons
decision_id: str                     # deterministic hash
```

---

## Public API (5 functions)

```python
def build_decision_context(
    state: AgentState,
    brain: BrainState,
    tasks: list[Task],
    outbox: list[Message],
    events: list[tuple[int, str, dict]],
    persona: Persona,
) -> DecisionContext:
    """Snapshot everything the engine needs. Pure read, no side effects."""

def evaluate_heartbeat(ctx: DecisionContext, policy: DecisionPolicy) -> HeartbeatDecision:
    """Score all candidates, apply brain learning, pick winner. Pure function."""

def apply_decision(
    decision: HeartbeatDecision,
    state: AgentState,
    brain: BrainState,
    tasks: list,
    outbox: list,
    persona: Persona,
    config: AppConfig,
) -> dict:
    """Translate decision into scheduler directives dict."""

def record_decision(
    brain: BrainState,
    decision: HeartbeatDecision,
    ctx: DecisionContext,
) -> str | None:
    """Store decision as a note_type='decision' brain note. Returns note ID."""

def record_decision_outcome(
    brain: BrainState,
    decision_note_id: str,
    outcome: str,
    positive: bool,
) -> None:
    """Record whether the decision worked out. Feeds learning loop."""
```

---

## DecisionPolicy: Persona-Specific Configuration

### Default policy (used when persona omits `decisions` config)

```python
DEFAULT_DECISION_POLICY = DecisionPolicy(
    task_priority_scores={"urgent": 95, "high": 70, "medium": 45, "low": 20},
    consolidation_threshold=10,
    connection_density_target=0.5,
    confidence_threshold=0.80,
    ambiguity_margin=5.0,
    enabled_initiatives=frozenset({
        INITIATIVE_STALE_REVIEW,
        INITIATIVE_GOAL_CHECK,
        INITIATIVE_PATTERN_CHECK,
    }),
    initiative_cooldown=8,
    initiative_ceiling=60.0,
    event_routing={},       # fall back to ENGINE_DEFAULT_ROUTING
    action_biases={},       # no persona bias
)
```

### Persona manifest example (mcp.json / persona.json)

```json
{
  "name": "workspace_observer",
  "workflow": "steps",
  "decisions": {
    "confidence_threshold": 0.85,
    "consolidation_threshold": 15,
    "connection_density_target": 0.4,
    "enabled_initiatives": ["stale_review", "pattern_check"],
    "initiative_cooldown": 12,
    "action_biases": {
      "run_connection_scan": 10,
      "run_consolidation": 5
    },
    "event_routing": {
      "file_modified": {"action": "run_heartbeat", "preempt": true, "boost": 15}
    }
  }
}
```

A persona that omits `decisions` entirely gets `DEFAULT_DECISION_POLICY`. Partial overrides merge with defaults.

### Loading in persona_loader.py

```python
# New field on Persona dataclass
decision_policy: DecisionPolicy | None = None

# Resolver merges persona config with defaults
def _resolve_decision_policy(raw: dict | None) -> DecisionPolicy:
    """Merge persona decision config with defaults."""
    if raw is None:
        return DEFAULT_DECISION_POLICY
    defaults = DEFAULT_DECISION_POLICY
    return DecisionPolicy(
        task_priority_scores={**defaults.task_priority_scores,
                              **raw.get("task_priority_scores", {})},
        consolidation_threshold=raw.get("consolidation_threshold",
                                        defaults.consolidation_threshold),
        # ... merge each field, falling back to default
    )
```

### DECISIONS.md governance doc (optional, per persona)

Like `HEARTBEAT.md` (strategy) and `BRAIN.md` (knowledge schema), each persona can include a `DECISIONS.md` that describes its decision-making philosophy. This is injected into the prompt when the engine chooses `ASK_DEVELOPER` and into initiative prompts.

```
personas/
  workspace_observer/
    instructions.md
    HEARTBEAT.md
    BRAIN.md
    DECISIONS.md    ← "prioritize coverage over depth, scan broadly"
```

### What lives where

| In `decision_engine.py` (generic) | In persona config (specific) |
|---|---|
| Scoring algorithm structure | Scoring weights and thresholds |
| Brain learning adjustment math | Which initiatives are enabled |
| Confidence gate logic | Confidence threshold value |
| Event routing framework | Event routing overrides |
| Decision recording format | Action biases |
| Candidate evaluation loop | Custom action candidates |

---

## Scoring Logic

Each action category has a dedicated scorer. All are pure functions of DecisionContext + DecisionPolicy.

| Scorer | Key Signals | Score Range |
|--------|------------|-------------|
| `_score_task(task, ctx, policy)` | Priority rank (from policy.task_priority_scores), remaining budget (near-timeout +15), active continuity (+10), brain domain-match (+5 per relevant decision/preference note) | 20-100 |
| `_score_events(event, ctx, policy)` | Event priority (1=80, 2=40, 3=20), task-mutation boost (+20), policy event_routing overrides | 20-100 |
| `_score_consolidation(ctx, policy)` | unconsolidated_count vs policy.consolidation_threshold, time since last consolidation | 0-60 |
| `_score_wiki_lint(ctx, policy)` | Time since last lint, open lint issues | 0-50 |
| `_score_connection_scan(ctx, policy)` | connection_density < policy.connection_density_target, scaled by gap | 0-45 |
| `_score_initiative_*(ctx, policy)` | Per-type triggers, gated by policy.initiative_cooldown, filtered by policy.enabled_initiatives | 0-45 |
| `_score_default_heartbeat(ctx, policy)` | Base 10, +5 per consecutive empty heartbeat, -3 per recent error | 5-30 |

After scoring, `policy.action_biases` are applied to each candidate, then `_apply_brain_learning()` adjusts based on past decision outcomes.

### Brain Learning Adjustment

After individual scoring, `_apply_brain_learning(candidates, ctx)` adjusts scores:
- Scan `ctx.recent_decision_notes` for decisions matching each candidate's ActionType
- +3.0 per note with positive_feedback > negative_feedback (capped at +20 total)
- -5.0 per note with negative_feedback > positive_feedback (capped at -20 total)

This is the core "brain informs decisions" mechanism.

### Confidence Gate

When the top two candidates are within `policy.ambiguity_margin` points AND the winner's confidence < `policy.confidence_threshold`:
- Replace chosen with `ASK_DEVELOPER`
- Emit an alert to outbox explaining the ambiguity
- Record the decision with `should_ask=True`

### Initiative Rules (gated by policy.initiative_cooldown, only when no candidate > policy.initiative_ceiling)

Only initiatives in `policy.enabled_initiatives` are evaluated.

| Initiative | Trigger | Score |
|-----------|---------|-------|
| Stale review | density < 0.3, notes > 20 | 25 + (0.3 - density) * 50 |
| Goal check | goals at 80%+ budget without completion | 30 + 5 * stalled_count |
| Pattern check | 30+ notes, < 3 pattern/architecture notes | 20 |
| Security scan | Every 50 heartbeats | 18 |
| TODO scan | Every 25 heartbeats | 15 |

---

## Integration Points

### scheduler.py -- insert after loading, before agent creation (~line 200)

```python
# BEFORE (current):
#   active_task = get_active_task(tasks, persona.name)
#   if active_task is None: pending = get_pending_tasks(tasks) ...

# AFTER (with decision engine):
from decision_engine import (
    build_decision_context, evaluate_heartbeat,
    apply_decision, record_decision,
)

events_batch = drain_pending_events(event_queue)  # already exists
ctx = build_decision_context(state, brain, tasks, outbox, events_batch, persona)
policy = persona.decision_policy or DEFAULT_DECISION_POLICY
decision = evaluate_heartbeat(ctx, policy)
decision_note_id = record_decision(brain, decision, ctx)

directives = apply_decision(decision, state, brain, tasks, outbox, persona, config)
active_task = directives.get("active_task")  # replaces naive FIFO pick
```

### scheduler.py -- after heartbeat completes (~line 340, in finally block)

```python
# Record outcome based on what happened
if decision_note_id:
    productive = (new_notes + new_conns) > 0
    if active_task and active_task.status == "completed":
        record_decision_outcome(brain, decision_note_id, "task_completed", True)
    elif productive:
        record_decision_outcome(brain, decision_note_id, "productive", True)
    elif not productive and decision.chosen.action != ActionType.SKIP_CYCLE:
        record_decision_outcome(brain, decision_note_id, "empty", False)
```

### persona_loader.py -- resolve DecisionPolicy

Add `decision_policy: DecisionPolicy | None` to the `Persona` dataclass. In `load_persona()`, call `_resolve_decision_policy(raw.get("decisions"))` to merge persona config with defaults.

### workflow.py -- no changes

The `apply_decision()` directives dict includes:
- `workflow_override`: if set to `"consolidation"` or `"lint"` or `"connection_scan"`, the scheduler calls the appropriate workflow function directly instead of the normal dispatch
- `extra_context`: initiative prompt text appended to `enriched_instructions`
- `outbox_messages`: messages to append (ASK_DEVELOPER alerts)

No changes to the workflow functions themselves.

### state.py -- no changes (uses existing state.context dict)

Track in `state.context`:
- `consecutive_empty_heartbeats` (int, incremented when score=0, reset otherwise)
- `last_initiative_heartbeat` (int, set when an initiative action is chosen)

---

## Decision Notes Format

Stored as brain notes with `note_type="decision"`, `category="areas"`:

```
content: |
  Decision: work_task
  Score: 70.0 | Confidence: 90%
  Rationale: Work on task 'Fix auth module' (priority=high, 3/10 heartbeats)
  Alternatives: run_consolidation (35.0), run_heartbeat (10.0)
  Heartbeat: #15
summary: "Decision: work_task -- Fix auth module (score=70, confidence=90%)"
tags: ["decision", "work_task", "task:t0a3f2"]
```

Trivial decisions (default heartbeat, score < 15, no alternatives above 10) are NOT recorded to avoid brain noise.

---

## Event Routing

Default routing (overridable per persona via `policy.event_routing`):

```python
DEFAULT_EVENT_ROUTING = {
    "task_created":   {"action": WORK_TASK,     "preempt": True,  "boost": 20},
    "task_answered":  {"action": WORK_TASK,     "preempt": True,  "boost": 25},
    "task_retried":   {"action": WORK_TASK,     "preempt": True,  "boost": 15},
    "task_cancelled": {"action": SKIP_CYCLE,    "preempt": False, "boost": 0},
    "git_commit":     {"action": RUN_HEARTBEAT, "preempt": False, "boost": 10},
    "cli_command":    {"action": RUN_HEARTBEAT, "preempt": True,  "boost": 5},
    "file_change":    {"action": RUN_HEARTBEAT, "preempt": False, "boost": 0},
    "file_created":   {"action": RUN_HEARTBEAT, "preempt": False, "boost": 3},
    "file_modified":  {"action": RUN_HEARTBEAT, "preempt": False, "boost": 0},
    "file_deleted":   {"action": RUN_HEARTBEAT, "preempt": False, "boost": 0},
}
```

---

## Files to Create/Modify

| File | Action | Lines (est.) |
|------|--------|-------------|
| `decision_engine.py` | **Create** | ~550 |
| `tests/unit/test_decision_engine.py` | **Create** | ~450 |
| `persona_loader.py` | **Modify** | ~20 lines (add DecisionPolicy field + resolver) |
| `scheduler.py` | **Modify** | ~30 lines (insert decision eval, outcome recording) |
| `docs/brain-evolution.md` | **Modify** | Update status |

No changes to: `workflow.py`, `second_brain.py`, `tasks.py`, `event_config.py`, `state.py`, `messaging.py`, `config.py`.

---

## Verification

1. **Unit tests**: `pytest tests/unit/test_decision_engine.py` -- all pure-function tests, no LLM or I/O
   - Each scorer returns expected ranges for known inputs
   - Policy overrides change scoring behavior
   - Brain learning adjustment boosts/penalizes correctly
   - Confidence gate triggers when top-2 are close and confidence is low
   - Initiative rules respect cooldowns and enabled_initiatives filter
   - Decision recording produces correct note format
   - Outcome recording updates feedback counters
   - Event routing maps correctly, with persona overrides
   - Default policy is used when persona omits decisions config

2. **Integration test**: `pytest tests/unit/test_scheduler.py` -- existing tests still pass after scheduler changes

3. **Manual smoke test**: Run `python -m scheduler` for a few heartbeats, verify:
   - Decisions appear in logs (`"Decision: work_task (score=70, confidence=0.9)"`)
   - Decision notes appear in brain (`natl brain search decision`)
   - Initiative actions fire after the cooldown period with no tasks queued
   - Confidence gate fires when you create two equal-priority tasks

---

## Implementation Order

1. Create `decision_engine.py` with data model (ActionType, ActionCandidate, DecisionPolicy, DecisionContext, HeartbeatDecision) + DEFAULT_DECISION_POLICY + all scorers + evaluate_heartbeat + apply_decision + record functions
2. Create `tests/unit/test_decision_engine.py` with full coverage
3. Add DecisionPolicy to persona_loader.py (Persona dataclass + resolver)
4. Modify `scheduler.py` to call the engine (insert context build, evaluate, apply, outcome recording)
5. Run full test suite to verify no regressions
6. Update docs
