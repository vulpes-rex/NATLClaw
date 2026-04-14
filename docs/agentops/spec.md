# AgentOps Middleware (Agnostic) — Technical Spec

## 1. Overview

This spec defines a standalone subproject for agent-agnostic coding middleware. The goal is to support any coding CLI/agent through adapters while preserving strict separation from NATLClaw core.

Proposed subproject root:

```text
subprojects/agentops/
```

---

## 2. Proposed Repository Layout

```text
subprojects/agentops/
  pyproject.toml
  README.md
  agentops/
    __init__.py
    config.py
    daemon.py
    api.py
    schema/
      event-v1.json
    ingest/
      validator.py
      normalizer.py
      writer.py
    store/
      sqlite.py
      files.py
    analytics/
      token_usage.py
      read_patterns.py
      write_patterns.py
      reports.py
    adapters/
      sdk.py
      natlclaw_client.py
      fixtures.py
    dashboard/
      static/
      views.py
  tests/
    unit/
    integration/
```

Notes:
- No imports from NATLClaw internals are required except optional adapter client entry points.
- The subproject is shippable on its own as a package/tool.

---

## 3. High-Level Architecture

```text
[Adapter(s)] --> [Ingest API] --> [Validator/Normalizer] --> [Event Store]
                                                    |--> [State Files]
                                                    |--> [Analytics Engine]
                                                    |--> [Reports API + Dashboard]
```

### 3.1 Component Responsibilities

- **Adapters**
  - Collect provider-specific lifecycle/tool signals.
  - Emit normalized events.
- **Ingest API**
  - Accept single and batch event payloads.
  - Apply schema validation and redaction.
- **Store**
  - Persist immutable event rows in SQLite.
  - Persist human-readable artifacts in markdown/json files.
- **Analytics**
  - Compute token usage aggregates and read/write patterns.
  - Generate actionable reports.
- **Dashboard/API**
  - Serve data for sessions, events, reports, trends.

---

## 4. Protocol Specification (`event-v1`)

### 4.1 Envelope

Required top-level fields:

- `spec_version` (string; default `"1.0"`)
- `event_id` (string; unique)
- `event_type` (string; dot notation)
- `timestamp` (ISO-8601 UTC string)
- `session` (object with at least `session_id`)
- `payload` (object; may be empty)

Recommended fields:

- `agent` (provider/name/version/model)
- `adapter` (name/version/capabilities)
- `meta` (`trace_id`, `parent_event_id`, `tags`)

### 4.2 Event Type Taxonomy

Core event types:

- `session.started`
- `session.ended`
- `prompt.submitted`
- `response.completed`
- `tool.call.started`
- `tool.call.completed`
- `tool.call.failed`
- `file.read`
- `file.write`
- `command.exec`
- `git.commit`
- `diagnostic.reported`
- `policy.warning`
- `cron.tick`

Custom extensions:

- Any `custom.*` type is valid and stored as-is.

### 4.3 Token Usage Semantics

Payload may include:

- `usage.prompt_tokens`
- `usage.completion_tokens`
- `usage.total_tokens`
- `usage.estimated_tokens`
- `usage.estimate_confidence` (`low|medium|high`)

Rules:

1. If measured token fields exist, they are canonical.
2. If measured fields are absent, estimator populates `estimated_tokens`.
3. Reports show measured and estimated values separately.

---

## 5. Storage Model

## 5.1 SQLite Tables (Core)

### `events`

- `id` INTEGER PK
- `event_id` TEXT UNIQUE NOT NULL
- `spec_version` TEXT NOT NULL
- `event_type` TEXT NOT NULL
- `ts` TEXT NOT NULL
- `session_id` TEXT NOT NULL
- `run_id` TEXT NULL
- `project_id` TEXT NULL
- `agent_provider` TEXT NULL
- `adapter_name` TEXT NULL
- `payload_json` TEXT NOT NULL
- `meta_json` TEXT NULL

Indexes:

- `(session_id, ts)`
- `(event_type, ts)`
- `(project_id, ts)`

### `session_summaries`

- `session_id` TEXT PK
- `started_at` TEXT
- `ended_at` TEXT
- `duration_ms` INTEGER
- `tool_calls` INTEGER
- `files_read` INTEGER
- `files_written` INTEGER
- `commands_run` INTEGER
- `tokens_measured` INTEGER
- `tokens_estimated` INTEGER
- `summary_json` TEXT

### `daily_metrics`

- `date` TEXT PK (`YYYY-MM-DD`)
- `project_id` TEXT
- `events_total` INTEGER
- `sessions_total` INTEGER
- `tokens_measured` INTEGER
- `tokens_estimated` INTEGER
- `warnings_total` INTEGER

### `anomaly_flags`

- `id` INTEGER PK
- `session_id` TEXT
- `flag_type` TEXT (`repeated_read`, `oversized_read`, `high_churn`, etc.)
- `severity` TEXT (`low|medium|high`)
- `details_json` TEXT
- `created_at` TEXT

## 5.2 State Directory Artifacts

Default path (configurable):

```text
data/agentops/
```

Files:

- `AGENTOPS.md` (policy and global config notes)
- `memory.md` (append-only chronological actions)
- `cerebrum.md` (learned rules and "do-not-repeat")
- `anatomy.md` (file index/summary cache)
- `token-ledger.json` (rollups)
- `reports/*.md` (generated reports)

---

## 6. API Specification (Local Service)

### 6.1 Ingest

- `POST /v1/events`
  - body: one `event-v1` envelope
  - responses: `202 accepted`, validation errors `400`
- `POST /v1/events/batch`
  - body: `{ "events": [ ... ] }`
  - partial failures return structured rejected-event list

### 6.2 Query

- `GET /v1/health`
- `GET /v1/sessions?limit=...`
- `GET /v1/sessions/{session_id}`
- `GET /v1/events?session_id=...&event_type=...`
- `GET /v1/reports/latest`
- `GET /v1/reports/{report_name}`

### 6.3 Control (Optional)

- `POST /v1/analyze/run` (manual analysis trigger)
- `POST /v1/anatomy/rebuild` (rebuild file index)

---

## 7. Adapter SDK Contract

Minimal Python interface:

```python
class AgentAdapter(Protocol):
    name: str
    version: str

    def capabilities(self) -> list[str]: ...
    async def start(self, ctx: dict) -> None: ...
    async def stop(self) -> None: ...
    def on_event(self, callback: Callable[[dict], Awaitable[None]]) -> None: ...
```

Adapter requirements:

1. Emit valid `event-v1`.
2. Maintain monotonic timestamps per local process clock.
3. Redact secrets before emitting payloads.
4. Retry transient send failures with bounded backoff.

---

## 8. Isolation Strategy (Avoid Muddying NATLClaw)

1. **Dependency Direction**
   - NATLClaw must not import AgentOps internals.
   - Optional bridge module can emit HTTP events to AgentOps if configured.

2. **Feature Flags**
   - `AGENTOPS_ENABLED=false` default in NATLClaw.
   - If disabled/unreachable, no behavior change in agent loops.

3. **No Shared Mutable State**
   - AgentOps uses its own DB/files and never mutates NATLClaw state schema.

4. **No Scheduler Entanglement**
   - No hard dependency inserted into `run_scheduler()` critical path.
   - Event emission must be non-blocking and fail-open.

---

## 9. Implementation Plan

## Phase A — Foundation

- Build `event-v1` schema and validation layer.
- Create SQLite event store and ingestion API.
- Add unit tests for schema and persistence.

## Phase B — Analytics Core

- Implement token accounting and fallback estimator.
- Implement repeated-read and oversized-read detectors.
- Generate markdown report artifacts.

## Phase C — Adapter and Integration

- Build NATLClaw adapter client (opt-in, fail-open).
- Build fixtures adapter for load/testing.
- Add integration tests for missing fields and malformed events.

## Phase D — UI and Hardening

- Add dashboard endpoints and static views.
- Add retention/pruning jobs.
- Add performance tests and crash-recovery tests.

---

## 10. Testing Strategy

### Unit Tests

- Event schema validation
- Normalizer transformations
- Token estimation functions
- Analyzer flag generation

### Integration Tests

- Ingest single/batch events end-to-end
- Session summary aggregation
- Adapter retry behavior and idempotency

### Resilience Tests

- Corrupt payload rejection
- DB restart/crash recovery
- High-volume event burst handling

---

## 11. Open Questions

1. Preferred runtime for adapters beyond Python (Node SDK parity needed now vs later)?
2. Default state path: `data/agentops/` vs `.agentops/`?
3. Should anatomy indexing be pull-based (scan job) or event-driven only?
4. What retention policy should be default for raw events (30/90/unlimited days)?

