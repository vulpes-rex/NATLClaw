# Knowledge Schema: Workspace Observer

## Domain
The user's actual day-to-day work: what they are building, changing,
debugging, and deciding. Out of scope: general programming knowledge,
tutorials, opinions not grounded in observed workspace activity.

## Categories
- **projects**: features, bugs, or tasks the user is actively working on
- **areas**: recurring concerns (e.g., "auth module", "test coverage", "CI pipeline")
- **resources**: observed patterns, conventions, tool preferences, workflow habits
- **archive**: completed features, resolved bugs, abandoned approaches

## Tags
Use lowercase, hyphenated tags derived from observed activity:
  feature, bugfix, refactor, config, testing, docs,
  dependency, performance, security, migration

Include project-specific tags as observed:
  branch names, module names, framework names

## Citation Rules
Every note MUST reference concrete evidence:
  [Evidence: commit abc1234 "fix auth timeout", src/auth.py#L42]

For pattern notes, cite at least 2 commits or files.
For progress notes, cite the commit(s) and branch.
For problem notes, cite the file and line where the issue appears.

## Quality Standards
- Notes must describe **what the user did**, not abstract concepts
- Every note must include at least one file path or commit hash
- Bad: "Testing is important for code quality"
- Good: "User added 3 tests for auth.py token validation (commit a1b2c3)"
- Confidence: [HIGH] = directly observed in diff, [MEDIUM] = inferred from
  file changes, [LOW] = guessed from file names or branch name
- Maximum 3 sentences per note

## Connection Rules
Primary relationships for work observations:
- **continues**: this work session continues a previous task
- **fixes**: this commit fixes an issue noted earlier
- **related**: these changes touch the same module/feature
- **blocked_by**: this task seems stalled, possibly due to another issue

## Wiki Page Guidelines
- One page per active feature or work area (not per heartbeat)
- Structure: Status > Recent Activity > Key Files > Open Issues > Notes
- Update when new commits or changes are observed
- Remove or archive pages for completed work
- Always include dates and commit refs — a page without them is stale
