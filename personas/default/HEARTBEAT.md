# Heartbeat Strategy: Default

## Phase
**Discovery** — the brain is young. Focus on breadth: cover as many
sub-topics as possible before diving deep into any single one.

Update this to **Deepening** when the brain has 50+ notes and 3+
wiki pages. Update to **Maintenance** when the brain has 200+ notes
and the primary domain feels well-covered.

## Cycle Focus Rules
Choose ONE focus per heartbeat based on brain state:

| Brain state | Focus this cycle |
|-------------|-----------------|
| < 10 notes | Capture a foundational concept in the domain |
| 10-50 notes, few connections | Prioritize connecting existing notes |
| 50+ notes, < 3 wiki pages | Trigger consolidation — promote to long-term |
| 50+ notes, 3+ wiki pages | Alternate: deepen an existing page one cycle, explore a new angle the next |
| Lint flagged issues | Resolve the highest-severity lint issue before capturing new knowledge |

## Priority Stack
When in doubt, prioritize in this order:
1. Fix flagged lint issues (errors compound if left)
2. Consolidate pending notes (prevent context bloat)
3. Deepen a weak wiki page (strengthen existing knowledge)
4. Capture something new (expand coverage)
5. Explore connections (find non-obvious relationships)

## Adaptive Behavior
- **High error rate** (3+ error lessons in last 10 heartbeats):
  Slow down. Focus on reviewing and correcting existing knowledge
  before adding more.
- **Repetitive captures** (last 3 notes have overlapping tags):
  The agent is stuck in a loop. Force it to explore a different
  category or sub-topic.
- **Empty review** (review step said "nothing new"):
  Skip the next capture step and spend the cycle on connections
  or consolidation instead.

## Escalation Rules
Flag for human attention (log at WARNING level):
- A contradiction between two high-confidence wiki pages
- The agent has captured the same topic 3+ times without resolution
- A tool call fails 3 consecutive heartbeats
- The brain hasn't grown (0 new notes) in 5+ heartbeats
- A lint issue has been flagged but unresolved for 10+ heartbeats

Do NOT escalate:
- Normal operational status
- Minor stale-note warnings
- Single tool call failures (retry first)

## Cycle Continuity
Each heartbeat should begin by reading the previous cycle's review
summary (already injected via agent state). Use it to:
- Avoid repeating what was just captured
- Follow up on "next areas to explore" suggestions
- Continue multi-cycle investigations

If the previous review suggested a specific topic, prioritize it
unless a higher-priority item (lint fix, consolidation) takes precedence.

## Resource Constraints
- **Status check**: keep to 2-3 sentences (don't waste tokens on verbose status)
- **Capture**: 2-3 sentence content maximum per note
- **Connect**: only attempt if there are 2+ recent notes; skip otherwise
- **Review**: 2-3 sentences maximum; focus on actionable next steps

## Goal Evolution
- **Weeks 1-2** (heartbeats 1-100 at 2min interval):
  Cast a wide net. Cover the major sub-topics of the domain.
  Accept lower-quality notes — volume matters more than polish.
- **Weeks 3-4** (heartbeats 100-200):
  Shift to depth. Pick the 3 weakest wiki pages and systematically
  strengthen them.
- **Month 2+** (heartbeats 200+):
  Maintenance mode. Focus on connections, contradiction detection,
  and periodic review. New captures should be genuinely novel, not
  incremental.
