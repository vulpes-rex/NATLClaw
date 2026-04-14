# AgentOps Middleware (Agnostic) — Requirements

## 1. Project Goal

Create a new, standalone subproject that provides OpenWolf-like middleware capabilities for coding CLIs/agents, without coupling the implementation to any single provider (Claude Code, Codex CLI, Aider, etc.) and without muddying NATLClaw's core runtime.

This subproject must:

- Ingest normalized agent/tool events from multiple adapters.
- Maintain durable workspace intelligence (memory, anatomy index, token ledger, reports).
- Expose a clean API and optional dashboard for observability.
- Remain optional and non-invasive to NATLClaw.

---

## 2. Product Scope

### In Scope (MVP)

1. Event ingestion protocol (`event-v1`) and schema validation.
2. Local daemon/service for ingestion, storage, analysis, and report generation.
3. Adapter SDK and at least one working adapter implementation.
4. Persistent state directory for memory/index/ledger/report artifacts.
5. Token usage accounting (actual when available, estimated fallback).
6. Read/write behavior analytics (repeated reads, oversized reads, churn).
7. Simple dashboard and HTTP API for sessions/events/reports.

### Out of Scope (MVP)

- Rich visual design QA capture pipeline.
- Deep IDE integration per editor.
- Full cloud sync/multi-user tenancy.
- Mandatory integration into NATLClaw scheduler/runtime loops.

---

## 3. Functional Requirements

### FR-1: Agent-Agnostic Event Model

- The system must define a provider-neutral event envelope with versioning (`spec_version`).
- The model must support common lifecycle categories:
  - session events
  - prompt/response events
  - tool events
  - file events
  - command/git events
  - diagnostics/policy events
- Unknown/custom event types must be accepted and stored without crashing ingestion.

### FR-2: Adapter-Based Integration

- Integrations must be implemented as adapters, not hardcoded provider paths.
- Each adapter must declare capabilities (for example: pre-tool hooks, token usage, file diff metadata).
- The platform must degrade gracefully when an adapter cannot provide certain signals.

### FR-3: Durable Local State

- The system must store state under an isolated subproject directory (see architecture constraints).
- It must persist:
  - chronological memory log
  - learned preferences/rules
  - anatomy/file index
  - token ledger and session summaries
  - generated reports
- Storage must be resilient to crashes (atomic writes or transactional DB operations).

### FR-4: Token and Cost Visibility

- Record provider token metrics when available.
- Estimate tokens when unavailable using deterministic fallback heuristics.
- Compute aggregate metrics per session/day/week and per project.

### FR-5: Workspace Intelligence

- Detect repeated reads and likely waste patterns.
- Track file-read/file-write hotspots.
- Provide actionable report outputs (for example: "anatomy would have sufficed", "read same file N times in 10 minutes").

### FR-6: Service/API Layer

- Expose local API endpoints for:
  - health
  - event ingestion
  - session/event query
  - report retrieval
- Provide an optional dashboard that only consumes this API.

### FR-7: NATLClaw Compatibility Without Coupling

- NATLClaw can emit events to this system via an adapter or lightweight client.
- NATLClaw must remain fully functional if this subproject is absent or disabled.

---

## 4. Non-Functional Requirements

### NFR-1: Isolation

- The subproject must live in a dedicated top-level path and package namespace.
- No direct edits to NATLClaw core loops are required for core functionality.
- Integration points are explicit, minimal, and optional.

### NFR-2: Performance

- Event ingestion path should be low-latency (target p95 < 50ms for single-event ingest on local machine).
- Analysis jobs should run incrementally and not block ingestion.

### NFR-3: Reliability

- Service must tolerate malformed adapter payloads (reject event, continue running).
- Crash-safe persistence for event store and state files.
- Backpressure strategy for burst events (batch endpoint, queue, or bounded buffer).

### NFR-4: Extensibility

- New event types and adapters can be added without schema-breaking changes.
- Protocol changes are versioned and backward compatible for at least one minor version.

### NFR-5: Security & Privacy

- Local-first by default.
- No outbound network calls required for core operation.
- Configurable redaction of sensitive paths/args/content.

---

## 5. Architecture and Boundary Constraints

To avoid muddying NATLClaw:

1. Implement this as a new subproject, for example:
   - `subprojects/agentops/`
2. Keep runtime/storage isolated, for example:
   - `data/agentops/` or `.agentops/` (configurable)
3. Restrict NATLClaw integration to:
   - a thin event emitter client
   - optional startup/teardown hooks
4. Do not embed AgentOps persistence, business logic, or dashboards in:
   - `scheduler.py`
   - `workflow.py`
   - `second_brain.py`
5. Treat provider-specific behavior as adapter concerns, not core concerns.

---

## 6. Success Criteria (MVP Exit)

1. A single daemon ingests events from at least two different adapters (one can be NATLClaw CLI adapter + one generic test adapter).
2. Session and token reports are queryable via API and visible in dashboard.
3. Repeated-read and oversized-read warnings are generated for sample workloads.
4. NATLClaw runs unchanged when AgentOps is disabled.
5. A "drop-in adapter" guide allows adding a new CLI in under one day.

---

## 7. Risks and Mitigations

- Risk: Adapter fragmentation and inconsistent fields.
  - Mitigation: strict required envelope + adapter certification checklist.
- Risk: Tight coupling sneaks into NATLClaw modules.
  - Mitigation: enforce dependency direction (NATLClaw -> adapter client only).
- Risk: Token estimates are noisy when providers hide usage.
  - Mitigation: expose confidence level and separate measured vs estimated metrics.

---

## 8. Delivery Phases

### Phase 1 — Protocol and Store
- Define `event-v1` JSON schema.
- Build ingestion API and event persistence.

### Phase 2 — Analytics
- Implement token ledger and behavior analyzers.
- Add report generation.

### Phase 3 — Integrations
- Build NATLClaw adapter client.
- Build one additional adapter proof (fixture/simulator or second CLI).

### Phase 4 — Dashboard and Hardening
- Add dashboard views.
- Add load/malformed-input testing and packaging.

---

## 9. Definition of Done (PoC)

PoC is complete only when all sections below are satisfied.

### A. Boundary Integrity (must pass)

1. Core NATLClaw loops remain free of provider/channel/persona-specific task logic.
2. At least one architecture boundary test enforces no forbidden imports/writes between adapter/core domains.
3. Persona behavior differences are represented in persona configuration (`workflow`, `steps`, `instructions`, `tools`) rather than core conditionals.

### B. Functional Outcomes (must pass)

1. One daemon ingests normalized `event-v1` payloads from at least two adapters.
2. Session/event/token reports are queryable through API and visible in dashboard endpoints.
3. Repeated-read and oversized-read signals are generated for sample workloads.
4. NATLClaw behavior is unchanged when AgentOps is disabled.

### C. Reliability and Operations (must pass)

1. Malformed payload rejection is non-fatal and observable.
2. Crash/restart tests demonstrate durable event/state recovery.
3. Backpressure behavior is defined and validated under burst traffic.
4. Operator runbook includes enable/disable/rollback and incident response actions.

### D. Extensibility Proof (must pass)

1. A drop-in adapter path is validated by building one additional adapter in under one day.
2. New persona onboarding is validated without core runtime edits.

## 10. Required Evidence Package for PoC Exit

The MVP/PoC exit decision requires a bundled evidence package:

1. **Boundary integrity evidence**
   - Architecture boundary test output (forbidden import/write checks).
   - Review checklist confirming no persona/provider hardcoding in NATLClaw core loops.
2. **Functional evidence**
   - API query examples for sessions/events/reports.
   - Sample report artifacts showing token and repeated-read/oversized-read outputs.
   - End-to-end adapter ingest traces for at least two adapters.
3. **Reliability evidence**
   - Malformed input rejection logs.
   - Crash/restart consistency test output.
   - Burst/backpressure test output with bounded behavior.
4. **Operational evidence**
   - Runbook procedures for enable/disable/rollback.
   - Incident drill notes showing fail-open behavior.
5. **Extensibility evidence**
   - "Drop-in adapter" onboarding record completed in under one day.
   - Persona onboarding record with no core runtime edits.
