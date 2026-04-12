Here's the full audit:

NATLClaw Workspace Audit
Critical Bugs (fix immediately)
Issue	File	Details
Missing imports	project_context.py	Uses datetime and subprocess without importing them. detect_and_save_project() will crash at runtime
No API auth	api.py + api_server.py	Both API layers have zero authentication. Anyone with network access can create tasks, manipulate the brain, trigger heartbeats
Features NOT Started
Semantic search (improvements.md 1.1) — No FAISS/ChromaDB embedding index. Brain retrieval is recency-based only, wastes tokens on large brains
Coordinator-MCP integration (coworker-vision Phase 5) — Multi-persona task dependency graphs, inter-persona messaging, file locks. No code exists
CodeIntel advanced integration (codeintel-integration.md Phases 3-4) — Only codenav-mcp is wired up. Quality metrics and multi-turn tool loops missing
Partially Implemented (started but incomplete)
Coordinator mode — Round-robin and all-at-once work, but no task dependency graphs, no inter-persona messaging, no expertise-based routing, no file locks
Brain Phase 2: Retrieval Quality — Hybrid ranking works but access-frequency preference learning is tracked (last_accessed_at) but never used for ranking
Brain Phase 3: Storage Migration — SQLite is primary store, but retrieval still rebuilds the full in-memory graph every heartbeat (O(n))
Brain Phase 4: Knowledge Quality — Contradiction detection done, but evidence requirements and quality gates not implemented
Event-driven scheduler (Move 6) — asyncio.Queue infrastructure in place but file watcher writes to NDJSON file, never flows into the queue. File changes don't trigger immediate wake-up
Code Quality Issues
Massive duplication — Agent/persona/brain initialization is copy-pasted across cli.py, scheduler.py, api_server.py, and workflow.py. Should be a factory
Two API servers — Both api.py (439 lines) and api_server.py exist. api.py has inbox/watch endpoints that api_server.py doesn't, and vice versa for the OpenAI-compatible chat endpoint. They should be unified
Sync I/O in async contexts — Brain/state JSON is read synchronously inside run_in_executor which works, but persona loading (load_persona()) is purely sync and called on every heartbeat without caching
Missing Tests
API security (injection, malformed input)
project_context.py (the import bug would be caught)
Event watcher -> scheduler integration
Coordinator mode multi-persona scenarios
api_server.py endpoints
Completed Feature Scorecard
Feature	Status
Task queue + delegation (Move 4)	Done
Outbox + notifications (Move 5)	Done
HTTP API layer (Move 5.5)	Done (needs auth)
Tiered memory / wiki pages	Done
Note dedup + decay	Done
Adaptive heartbeat interval	Done
Goal/task awareness	Done
External ingestion	Done
Brain lint	Done
Source citation	Done
Multi-persona (basic)	Done
Topic graph recall	Done
CLI (12+ subcommands)	Done
Structured logging / metrics	Done
Config validation + hot reload	Done
Prompt templates	Done
FP/TP calibration (CodeIntel port)	Done
OpenAI-compatible API + dashboard	Done
Recommended Priority Order
Fix project_context.py missing imports (5 min)
Unify api.py and api_server.py into one server (1-2 hrs)
Add API authentication middleware (30 min)
Implement semantic search with embeddings (1-2 days)
Wire file watcher events into asyncio scheduler queue (2-3 hrs)
Refactor agent init into a shared factory (2-3 hrs)
Optimize brain retrieval to query SQLite directly instead of in-memory rebuild (4-6 hrs)
Want me to start working through these in priority order?

