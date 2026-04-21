# Decision policy: Workspace Observer

## Intent

Prioritize **fresh workspace signals** (file edits, commits) over deep maintenance. Observer heartbeats should run when the repo changes, consolidate connections when the brain is dense enough, and avoid initiative spam during bursts of activity.

## Principles

1. **Events first** — `file_modified` and `git_commit` preempt idle work so the model sees changes while context is hot.
2. **Evidence-backed capture** — Notes without file/commit evidence are dropped; ambiguity belongs in gather, not in stored notes.
3. **Graph enrichment** — Slight bias toward connection scan and consolidation so observer insights link to the rest of the brain.
4. **Calibrated escalation** — Slightly higher confidence threshold (0.85) than default so `ASK_DEVELOPER` triggers when action choice is unclear.

## Relation to HEARTBEAT.md

`HEARTBEAT.md` describes *what* to look for each cycle. This file describes *how* the decision engine weights actions so scheduling matches that strategy.