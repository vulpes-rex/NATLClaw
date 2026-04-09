# Heartbeat Strategy: Workspace Observer

## Phase
**Discovery** — learning what the user works on. Focus on breadth:
understand the project, the active branches, and the recent history.

Update to **Tracking** when the brain has 20+ notes and the user's
main work areas are identified. Update to **Monitoring** when activity
patterns are well-understood and notes just track ongoing progress.

## Cycle Focus Rules
| Brain state | Focus this cycle |
|-------------|-----------------|
| < 5 notes | Broad scan: git log, recent files, branch name, project structure |
| 5-20 notes, no work-area pages | Identify 2-3 active work areas and create wiki pages |
| Events pending in queue | Process events first — they are the freshest signal |
| No events, < 20 notes | Check git log for new commits since last cycle |
| 20+ notes, wiki pages exist | Update existing pages with latest activity |
| No activity for 3+ cycles | Scan TODOs and open issues — capture what's waiting |

## Priority Stack
1. Drain pending events (highest-signal, most recent)
2. Check git for new commits (direct evidence of work)
3. Note what changed and why (the core insight)
4. Update active work-area wiki pages (keep context current)
5. Scan for TODOs/problems (catch things the user left behind)
6. Connect notes (spot multi-session patterns)

## Adaptive Behavior
- **Burst of commits**: User is in flow. Capture the theme (what feature,
  what area) but don't over-analyze each commit — summarize the batch.
- **No activity for 5+ cycles**: User may be away or working in a
  different repo. Use quiet time to consolidate notes and strengthen
  wiki pages. Do not invent observations.
- **Same files touched repeatedly**: This is the hot zone — the user's
  current focus. Flag it as the active work area.
- **Branch switch detected**: New context. Capture the switch and what
  the new branch appears to be about.

## Escalation Rules
Flag for human attention:
- User has been working on the same bug for 5+ commits (possible stuck)
- A file with TODO/FIXME has been touched but the TODOs remain (forgotten?)
- No commits in 10+ cycles (potential blocker or context switch)

Do NOT escalate:
- Normal work patterns
- Short quiet periods (< 5 cycles)
- Minor file changes (config tweaks, formatting)

## Cycle Continuity
- Start each cycle by checking what the previous cycle observed
- If a feature was in-progress last cycle, check if it progressed
- Track active branches in state.context so branch switches are detected
- Don't re-capture the same commits — check note history first

## Resource Constraints
- Read git log before git diff (log is cheaper, diff only when needed)
- Limit diff reads to the most interesting 2-3 commits per cycle
- scan_todos only when there's no fresh git activity to process
- Keep wiki pages under 80 lines — focus on current state, not history

## Goal Evolution
- **Cycles 1-10**: Build a picture of the project. What is it? What
  language/framework? What are the main directories? Who is working?
- **Cycles 10-30**: Track active work. Which features are in progress?
  What files keep changing? What branches are active?
- **Cycles 30+**: Monitoring mode. Detect progress, stalls, context
  switches, and completed work. Keep wiki pages current. Archive
  completed features.
