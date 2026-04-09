# Knowledge Schema: Codebase Learner

## Domain
Source code architecture, patterns, conventions, and dependencies
of the target codebase. Out of scope: general programming tutorials,
language documentation, opinions not grounded in observed code.

## Categories
- **projects**: active features or refactors being tracked
- **areas**: recurring concerns (e.g., "error handling", "state management")
- **resources**: patterns, conventions, architecture notes, dependency maps
- **archive**: deprecated patterns, removed modules

## Tags
Use lowercase, hyphenated tags derived from the codebase:
  architecture, pattern, convention, dependency,
  naming, error-handling, imports, testing, types,
  performance, security, api, state-management

Include language/framework tags as observed:
  python, typescript, react, node, docker, sql

## Citation Rules
Every note MUST include file path evidence:
  [Evidence: src/hooks/useApi.ts, src/pages/Dashboard.tsx#L14]

For pattern notes, cite at least 2 files where the pattern appears.
For architecture notes, cite the module paths involved.
For dependency notes, cite both the importer and importee.

## Quality Standards
- Pattern notes require 2+ file references (one occurrence is not a pattern)
- Convention notes must be specific: "named exports" not "good style"
- Architecture notes must name concrete modules, not abstract layers
- Dependency notes must specify the relationship direction and what is imported
- Confidence tagging: [HIGH] = 5+ occurrences, [MEDIUM] = 2-4, [LOW] = inferred

## Connection Rules
Primary relationships for code knowledge:
- **implements**: this pattern implements that architectural decision
- **depends_on**: this module calls/imports that module
- **contradicts**: this convention conflicts with that pattern
- **extends**: this observation adds detail to that architecture note

## Wiki Page Guidelines
- One page per major module or cross-cutting concern
- Structure: Purpose > Key Files > Patterns Used > Dependencies > Notes
- Update when new evidence is found rather than creating new pages
- Always include file paths — a wiki page without paths is useless
