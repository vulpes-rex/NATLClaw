# Brain Evolution Plan

This document turns the broad "improve the brain" goal into an implementation roadmap.
It also records the first vertical slice implemented in this change.

## Current State

The second brain already supports:

- Atomic notes
- Topic graph and note-to-note connections
- Wiki pages for consolidation
- Brain linting and daily digests
- Lesson extraction and execution history

The main limitations are:

- Notes are still too generic; they lack lifecycle and evidence metadata
- Retrieval is mostly keyword-driven
- Operator visibility is shallow; it is hard to inspect a note, trace a topic, or diagnose brain quality quickly
- Retrieval and ranking are still mostly in-memory and keyword-oriented even though the primary brain store now lives in SQLite

## Target Direction

### Functionality

- Distinguish observations, decisions, tasks, preferences, patterns, and architecture notes
- Preserve evidence and confidence with each note
- Support topic tracing and note inspection directly from the CLI
- Improve retrieval quality with ranking that combines text, recency, and graph structure

### Memory

- Track note lifecycle: active, archived, superseded, or tentative
- Track confidence and supporting evidence
- Introduce stronger dedup and freshness handling
- Eventually migrate the brain store from monolithic JSON to SQLite tables

### Visibility

- Add richer `natl brain` commands for inspection, topic maps, and traceability
- Expose brain health metrics beyond raw counts
- Make it obvious why a note exists, what supports it, and how it connects to other knowledge

## Delivery Phases

### Phase 1: Metadata + Visibility

Implemented in this change:

- Expand note schema with:
  - `note_type`
  - `status`
  - `confidence`
  - `evidence`
  - `last_accessed_at`
  - `last_confirmed_at`
- Allow capture pipeline to persist note metadata when the LLM returns it
- Add richer brain inspection commands:
  - `natl brain show <note_id>`
  - `natl brain topics`
  - `natl brain trace <topic>`
- Upgrade `natl brain stats` to show health metrics and topic density

Why this first:

- It improves functionality, memory quality, and visibility immediately
- It is backward-compatible with the existing JSON store
- It creates the schema needed for later ranking and storage work

### Phase 2: Retrieval Quality

Planned next:

- Replace simple keyword ranking with hybrid ranking
- Weight retrieval by confidence, recency, note type, and graph connectivity
- Track note access frequency and use it as a retrieval signal
- Let explicit operator feedback reinforce or demote memories over time

Status update:

- `search_notes()` now uses lexical signals plus confidence, recency, note type, and graph connectivity
- Read-only brain CLI queries (`stats`, `search`, `show`, `topics`, `trace`) now query the SQLite-backed brain store directly when available
- Chat and tool-based memory recall already benefit from the improved ranking because they still route through `search_notes()`
- Reads now persist `last_accessed_at` and `recall_count`, so retrieval can learn from actual usage
- Explicit relevance feedback is now stored and fed back into ranking and visibility surfaces
- Access-frequency preference learning is now active: `_access_frequency_bonus()` computes an access-rate signal (recalls per week with diminishing returns) and a smooth exponential-decay recency curve (half-life ~5 days), replacing the old flat step-function bonuses. Notes that are recalled often and recently rank measurably higher than stale or never-accessed notes
- `build_brain_stats()` now reports `frequently_accessed` and `never_accessed` counts for operator visibility

### Phase 3: Storage Migration

**Done.**

- Move notes, topics, pages, and edges into SQLite
- Keep a compatibility layer for loading old JSON brains
- Add queryable brain analytics and cheaper health checks

Status update:

- SQLite-backed storage is now the primary persistence layer via `brain.db`
- `brain.json` is still written as a compatibility snapshot and human-readable export
- `load_brain()` auto-migrates legacy JSON-only brains into SQLite on first load
- Schema v2 migration adds queryable columns (`confidence`, `recall_count`, `last_accessed_at`, `content`) directly on `brain_notes` — no need to parse `raw_json` for common queries
- FTS5 virtual table (`brain_notes_fts`) enables native full-text search on note content/summary
- Schema versioning via `brain_meta.schema_version` — future migrations auto-apply on startup
- Retrieval is now store-backed: `build_brain_summary_from_store()`, `get_recent_notes_from_store()`, `get_unconsolidated_notes_from_store()`, `find_duplicate_from_store()`, `decay_stale_notes_from_store()` all query SQLite directly instead of loading the full brain into memory
- Scheduler uses store-backed summary and decay — avoids the O(n) in-memory rebuild on every heartbeat
- `save_brain()` now uses incremental UPSERT (`INSERT OR REPLACE`) for notes, topics, and pages instead of DELETE all + re-INSERT — only changed rows touch disk
- Fallback: if incremental save fails, it falls back to the original full rewrite transparently

### Phase 4: Knowledge Quality

Planned after storage:

- Add contradiction detection
- Track superseded notes and stale wiki pages
- Add stronger evidence requirements for high-confidence notes

Status update:

- Notes can now be explicitly marked relevant or irrelevant from the CLI
- Notes can be marked contradicted by a newer or stronger note, which demotes them to `tentative` or `superseded`
- Brain lint now surfaces contradicted or weakly-supported memories for operator review

## Success Criteria

This effort is working if:

- A note can be inspected with its metadata, topics, related notes, and source pages
- A topic can be traced through the graph from the CLI
- The brain reports health metrics that help the operator decide what to fix
- New metadata is stored without breaking existing saved brains
