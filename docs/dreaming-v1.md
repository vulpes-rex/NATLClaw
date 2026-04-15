# Dreaming V1 (Sleep/Dream State)

## Goal

Add a safe, deterministic "sleep/dream" pass that runs when the agent is idle,
so memory quality improves without expanding runtime permissions.

## V1 Behavior

Dreaming runs four phases:

1. **Orient**: snapshot baseline metrics (`notes`, `orphans`, `unconsolidated`)
2. **Gather**: count unconsolidated short-term notes
3. **Consolidate**: archive exact duplicate non-archived notes (keep newest)
4. **Prune**: archive stale orphan notes + produce a lint snapshot

Dream metadata is tracked in brain state:

- `last_dream`
- `last_dream_heartbeat`

## Triggering

### Manual

`natl brain dream` (dry-run by default):

- `natl brain dream` -> no writes, prints projected phase outcomes
- `natl brain dream --apply` -> applies and persists dream changes
- `natl brain dream --apply --heartbeat 123` -> stamps heartbeat metadata
- `natl brain dream --json` -> prints machine-readable report JSON
- `natl brain dream --json --compact` -> single-line JSON for pipelines
- `natl brain dream --policy` -> shows effective dream policy for active persona

### Automatic (Scheduler Idle Hook)

The scheduler runs dream automatically when all are true for 3 consecutive cycles:

- no errors in cycle
- no active task
- no pending decision events
- no queue spillover
- no queued runtime events

After an automatic dream run, the idle streak resets.

Phase 2 makes this persona-configurable via `mcp.json` / `persona.json`:

```json
"dream": {
  "enabled": true,
  "idleStreakMin": 3,
  "maxAgeDays": 30
}
```

The operator snapshot (`natl status`) now includes the active dream policy.
The API status snapshot (`GET /api/status`) also includes a `dream` block for
dashboard and automation consumers.

API control endpoints:

- `GET /api/brain/dream/policy` -> effective dream policy for active persona
- `POST /api/brain/dream/run` -> run dream cycle (`{"apply": false}` by default)

Dashboard support:

- The embedded dashboard now includes a **Dreaming** panel under **Second Brain**
with policy visibility, last-run metadata, **Run Dry / Run Apply** buttons,
and a recent run history list.
- History supports trigger filtering (`all`, `auto idle`, `api`, `cli`) and
per-entry **Copy JSON** actions.

## Safety Guardrails

- Duplicate compaction is exact-text canonical dedup only (no fuzzy merge edits)
- Non-archived note content is never rewritten
- Archive decisions are explicit and reversible from persisted history
- Dry-run mode is the CLI default

## Expected Outcome

- lower orphan/stale noise
- reduced duplicate short-term notes
- stable and inspectable dream metadata in `brain stats`

