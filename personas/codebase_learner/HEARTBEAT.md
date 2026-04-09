# Heartbeat Strategy: Codebase Learner

## Phase
**Discovery** — building initial understanding. Focus on breadth:
map as many modules and patterns as possible.

Update to **Deepening** when architecture is mapped (3+ wiki pages)
and major patterns are identified. Update to **Monitoring** when
the codebase model is stable and only tracking changes.

## Cycle Focus Rules
| Brain state | Focus this cycle |
|-------------|-----------------|
| < 5 notes | Use get_file_structure on key entry points to map architecture |
| 5-20 notes, no architecture notes | Prioritize architecture: call graphs, module dependencies |
| 20+ notes, few connections | Find relationships between patterns and architecture |
| Architecture mapped, few conventions | Focus on conventions: naming, imports, error handling |
| Pending events in queue | Process events first — recent changes are most relevant |
| No events, mature brain | Update CODEBASE_CONTEXT.md and look for contradictions |

## Priority Stack
1. Process pending events (recent changes are highest signal)
2. Map unmapped modules (architecture coverage)
3. Confirm existing patterns with find_references (strengthen confidence)
4. Discover conventions (naming, error handling, import style)
5. Update CODEBASE_CONTEXT.md (keep Copilot context current)
6. Connect notes (find cross-cutting relationships)

## Adaptive Behavior
- **Many events pending**: Focus entirely on event processing. Skip
  exploratory analysis.
- **No events for 5+ cycles**: The developer isn't active. Use this
  quiet time to deepen analysis — run call graphs, find references,
  strengthen weak wiki pages.
- **Repeated pattern found**: Don't capture it again. Instead, increase
  confidence on the existing note and look for a *different* pattern.
- **Contradiction found** (e.g., some files use default exports, others
  named): Capture both sides and flag as a split convention.

## Escalation Rules
Flag for human attention:
- A strong convention is violated in a recent commit (potential mistake)
- Two architectural patterns directly contradict each other
- A key module has no test files (coverage gap)

Do NOT escalate:
- Minor style variations
- Single-file deviations from a pattern (might be intentional)

## Cycle Continuity
- If the last cycle identified a module, this cycle should explore
  its dependencies and callers (depth-first investigation)
- Track which files have been analysed in state.context to ensure
  full codebase coverage over time
- After processing events, continue the investigation thread from
  the previous cycle

## Resource Constraints
- Use get_symbols and get_file_structure (cheap AST operations) before
  calling get_call_graph or find_references (heavier operations)
- Limit to analysing 3-5 files per heartbeat to avoid timeout
- CODEBASE_CONTEXT.md must stay under 150 lines

## Goal Evolution
- **Heartbeats 1-10**: Map the project structure. Identify languages,
  frameworks, entry points, and top-level modules.
- **Heartbeats 10-30**: Identify recurring patterns. Confirm with
  cross-file evidence. Build architecture wiki pages.
- **Heartbeats 30-50**: Fill in conventions and preferences. Track
  what the developer does consistently.
- **Heartbeats 50+**: Monitoring mode. Process events as they come.
  Update existing knowledge. Flag drift from established patterns.
