# CodeIntel Integration Opportunities

Findings from exploring `C:\Users\kvwul\source\repos\CodeIntel` and how its
components map to NATLClaw improvements.

---

## 1  CodeIntel Overview

CodeIntel is a self-hosted code intelligence platform combining deterministic
static analysis (Roslyn, ESLint, Ruff) with LLM-powered semantic review. It
exposes three MCP servers and a CLI orchestrator for multi-agent workflows.

**Tech stack:** .NET 8 backend, React 18 dashboard, TypeScript MCP servers,
Node.js orchestrator, SQL Server + SQLite.

---

## 2  Components Relevant to NATLClaw

### 2.1  codenav-mcp — Code Navigation MCP Server

**Location:** `CodeIntel/src/codenav-mcp/`

**What it does:** Multi-language code navigation via Tree-sitter AST parsing,
exposed as 9 MCP tools for AI agents.

**Supported languages (10):** C#, TypeScript, TSX, JavaScript, Python, Java,
Go, Rust, Ruby, C, C++

**MCP tools:**

| Tool | Purpose |
|------|---------|
| `clone_repository` | Clone/pull a git repo and index all source files |
| `list_files` | List source files with glob/language filters |
| `get_symbols` | Extract classes, functions, methods, properties from a file or repo |
| `get_definition` | Get the full source body of a named symbol |
| `get_implementations` | Find all implementations of an interface/base type |
| `find_references` | Find every usage of a symbol across the codebase (AST-based) |
| `search_symbols` | Fuzzy search symbols by name substring |
| `get_file_structure` | Hierarchical outline: classes > methods > nested types |
| `get_call_graph` | Bidirectional: what a function calls and what calls it |

**Key files:**
- `src/index.ts` — MCP server entry point, tool definitions
- `src/git-manager.ts` — Clone/pull repos, track local paths
- `src/parser-registry.ts` — Load Tree-sitter WASM grammars, cache parsers
- `src/symbol-extractor.ts` — AST to symbol definitions
- `src/symbol-index.ts` — In-memory symbol index (fast lookup by name/file/kind)
- `src/reference-finder.ts` — Cross-file identifier scan
- `src/call-graph.ts` — Bidirectional call graph builder

**Config:**
- `CODENAV_GRAMMARS_DIR` — directory with `.wasm` grammar files
- `CODENAV_REPOS_DIR` — base directory for cloned repos
- `GIT_PAT` — personal access token for private repos

**Status:** Built and wired into NATLClaw as the `codenav` MCP server
in `mcp.json`. Used by the `codebase_learner` persona.

---

### 2.2  coordinator-mcp — Multi-Agent Orchestration MCP Server

**Location:** `CodeIntel/src/coordinator-mcp/`

**What it does:** Enables multiple AI agents to coordinate on shared codebases
via a SQLite-backed task board, messaging, decisions, and file locks.

**Tech stack:** TypeScript + `@modelcontextprotocol/sdk`, `sql.js` (SQLite
compiled to WASM — zero native dependencies).

**Database schema (9 tables):**
- `agents` — registered agents with roles
- `tasks` — task board with status/assignee/dependencies
- `task_deps` — task dependency graph
- `messages` — inter-agent messaging
- `decisions` — architectural decision log with tags
- `file_locks` — exclusive edit rights (prevents conflicts)
- `context_store` — shared key-value pairs
- `agent_cursors` — message read cursors per agent

**MCP tools (16):**

| Category | Tools |
|----------|-------|
| Task management | `register_agent`, `create_task`, `list_tasks`, `claim_task`, `update_task`, `get_task` |
| Messaging | `send_message`, `read_messages`, `poll_updates` |
| Decisions | `log_decision`, `search_decisions`, `set_context`, `get_context` |
| File coordination | `lock_file`, `unlock_file`, `list_locks` |

**Config:**
- `COORDINATOR_DB` — path to shared SQLite database (default: `.coordinator.db`)
- `AGENT_ID` / `AGENT_ROLE` — auto-register on startup

**NATLClaw relevance:** Replaces the basic round-robin coordinator workflow
(`workflow.py` Mode 4) with proper task-based orchestration. The task
dependency graph, inter-agent messaging, and file locks are the missing
pieces for multi-persona orchestration (improvement 3.5).

---

### 2.3  codeintel-mcp — Static Analysis MCP Server

**Location:** `CodeIntel/src/codeintel-mcp/`

**What it does:** Wraps the CodeIntel REST API as 12 MCP tools for agent
auto-fix workflows.

**MCP tools (12):**

| Tool | Purpose |
|------|---------|
| `list_repositories` | List repos with IDs and health scores |
| `list_scans` | List recent scans for a repo |
| `trigger_scan` | Start full or incremental scan |
| `get_scan_status` | Poll scan progress; returns AI summary when done |
| `get_systemic_issues` | AI-identified issue clusters (architectural) |
| `list_findings` | Query findings with filters |
| `get_finding` | Full detail + snippet + AI reasoning |
| `explain_finding` | Plain-English explanation |
| `suggest_fix` | AI-generated fix description |
| `get_fix_diff` | Unified diff patch |
| `mark_finding` | Update status: Resolved / FalsePositive / Snoozed |
| `get_rules` | List rules with calibration stats |

**Config:** `CODEINTEL_API_URL` (default: `http://host.docker.internal:5243`)

**NATLClaw relevance:** The codebase learner could call CodeIntel's scan API
to get real static analysis findings. Findings map to brain notes:

| CodeIntel output | Brain note type |
|-----------------|----------------|
| Rule violations | `pattern` (anti-patterns) |
| Calibrated rules (FP rates) | `preference` (developer agreement) |
| Systemic issue clusters | `architecture` notes |
| Fix suggestions | `convention` notes |

---

### 2.4  orchestrator-cli — Multi-Agent Orchestrator CLI

**Location:** `CodeIntel/src/orchestrator-cli/`

**What it does:** Spawns, coordinates, and monitors parallel AI agent
workflows via command-line interface.

**Subcommands:**
- `init` — seed coordinator DB with objective, agents, and tasks
- `launch` — spawn N agent worker processes in parallel
- `monitor` — live terminal dashboard
- `status` — one-shot status check
- `worker` — (internal) autonomous agent loop

**Workflow templates:**
- `review` — 6 agents (lead + 5 domain reviewers) + 6 tasks with dependencies
- `quick` — 3 agents + 3 tasks (security + design + synthesis)

**Key files:**
- `orchestrate.mjs` — CLI subcommands + agent execution logic
- `copilot-agent-prompt.mjs` — per-agent mega-prompt builder with
  FP-calibrated confidence floors from CodeIntel Rules API
- `mcp-config-builder.mjs` — generates MCP server configs with absolute paths

**NATLClaw relevance:** Patterns worth adopting:
- Workflow templates as predefined agent+task configurations
- Worker loop: register > claim task > execute > report
- Dynamic prompt building with calibrated parameters
- MCP config generation for persona tool setup

---

## 3  Patterns Worth Adopting

### 3.1  Fingerprint-based deduplication

NATLClaw uses token overlap (Jaccard similarity) in `second_brain.py`.
CodeIntel uses SHA-256 fingerprints of `(RuleId + FilePath + LineStart)` —
deterministic and O(1) lookup. For brain notes, a fingerprint of
`(note_type + normalized_content_hash)` would be faster and more reliable.

**Where:** `CodeIntel.Domain/Entities/Finding.cs` — `Fingerprint` property

### 3.2  Confidence calibration from user feedback

CodeIntel auto-adjusts confidence floors based on FP/TP ratios. After 5+
user acknowledgements, it raises the bar for rules that get frequently
marked as false positives:
- <20% FP rate: no change
- 20-39%: confidence floor = 70
- 40-59%: confidence floor = 80
- >=60%: confidence floor = 90

NATLClaw's brain notes have a `confidence` field but no calibration loop.
The codebase learner's preference model could use the same mechanism.

**Where:** `CodeIntel.Domain/Entities/Rule.cs` — `CalibratedConfidenceFloor`

### 3.3  SQLite for brain persistence

coordinator-mcp uses `sql.js` (SQLite to WASM) — zero native dependencies,
proper queries, atomic writes. NATLClaw's brain is a single JSON file that
gets fully serialized/deserialized every heartbeat. A SQLite backend would
let `recall_by_topic` do indexed queries instead of in-memory BFS.

**Where:** `CodeIntel/src/coordinator-mcp/src/store.ts`

### 3.4  Multi-turn agentic tool loop

CodeIntel's agentic loop does multi-turn tool calling: the LLM calls
`search_symbol`, reads results, calls `read_file`, then synthesizes.
NATLClaw's workflows are fixed-step sequences. Adding a tool-calling loop
to the `steps` workflow would let the codebase learner do real investigation.

**Where:** `CodeIntel.Infrastructure/Llm/LlmSkillRunner.cs` — agentic loop
with `search_symbol`, `read_file`, `list_files`, `summarize_findings` tools

### 3.5  Cross-file symbol resolution

CodeIntel's `SymbolContextProvider` builds call-graphs and resolves imports
across files. This is what codenav-mcp exposes via `get_call_graph` and
`find_references` — already wired into the codebase learner persona.

**Where:** `CodeIntel.Infrastructure/Llm/SymbolContextProvider.cs`

### 3.6  Embedding-based semantic enrichment

CodeIntel's `EmbeddingContextProvider` uses cosine similarity (0.75 threshold)
to find semantically related files. This is the approach proposed in
NATLClaw improvement 1.1 (semantic search).

**Where:** `CodeIntel.Infrastructure/Llm/EmbeddingContextProvider.cs`

### 3.7  Static analysis grounding

CodeIntel's `StaticAnalysisBroker` injects Phase 1 (deterministic) findings
into Phase 2 (LLM) prompts as `[STATIC ANALYSIS]` blocks. This prevents the
LLM from re-reporting known issues and reduces hallucination. NATLClaw could
do the same: inject lint results into the capture prompt so the agent doesn't
re-discover what lint already found.

**Where:** `CodeIntel.Infrastructure/Llm/StaticAnalysisBroker.cs`

---

## 4  Implementation Roadmap

### Phase 1: codenav-mcp in codebase learner (DONE)

- Built codenav-mcp, downloaded 11 Tree-sitter WASM grammars
- Added `codenav` MCP server to `mcp.json`
- Created `codebase_learner` persona with 4-step workflow using codenav tools
- AST-aware code navigation replaces naive grep-based tools

### Phase 2: coordinator-mcp for multi-persona

- Add `coordinator` MCP server to `mcp.json`
- Replace round-robin coordinator workflow with task-based orchestration
- Personas register as agents, claim tasks, report results via coordinator
- File locks prevent two personas from analysing the same file

### Phase 3: codeintel-mcp for code quality

- Add `codeintel` MCP server to `mcp.json`
- Codebase learner calls `trigger_scan` + `list_findings`
- Static analysis findings become brain notes with structured evidence
- FP/TP calibration feeds into preference model

### Phase 4: Adopt advanced patterns

- Fingerprint-based dedup for brain notes
- Confidence calibration from user feedback
- SQLite backend for brain persistence
- Multi-turn agentic tool loop in steps workflow

---

## 5  CodeIntel Analysis Pipeline Reference

For context on how CodeIntel's own analysis works:

```
Phase 1  — Roslyn (C#), ESLint (JS/TS), Ruff (Python) — deterministic
    |
    v  (findings -> StaticAnalysisBroker)
Phase 2  — LLM skill runners x5 (concurrent, domain-specific)
    |      Security, Design, Robustness, Maintainability, Performance
    v      Each skill: focused prompt + ensemble voting + confidence gate
Health Score — Deterministic (100 base, deductions per finding)
    |
    v
Agentic Loop — Multi-turn tool-calling exploration
    |
    v
Output — Narrative summary, systemic clusters, recommendations
```

**5 LLM skills:**

| Skill | Ensemble | Min Confidence |
|-------|----------|----------------|
| Security | 2-pass | 70 |
| Design | 2-pass (upgraded) | 65 |
| Robustness | 2-pass | 70 |
| Maintainability | 1-pass | 60 |
| Performance | 1-pass | 65 |

**Health scoring:** Starts at 100, deductions per finding severity:
- Critical: -10 (cap -50)
- High: -3 (cap -40)
- Medium: -1 (cap -30)
- Low: -0.1 (cap -10)

---

## 6  Key File References

| Component | Key File | Purpose |
|-----------|----------|---------|
| codenav-mcp | `src/codenav-mcp/src/index.ts` | 9 code navigation tools |
| codenav-mcp | `src/codenav-mcp/src/symbol-index.ts` | In-memory symbol index |
| codenav-mcp | `src/codenav-mcp/src/call-graph.ts` | Call graph builder |
| codeintel-mcp | `src/codeintel-mcp/src/index.ts` | 12 static analysis tools |
| coordinator-mcp | `src/coordinator-mcp/src/index.ts` | 16 orchestration tools |
| coordinator-mcp | `src/coordinator-mcp/src/store.ts` | SQLite schema + CRUD |
| orchestrator | `src/orchestrator-cli/orchestrate.mjs` | CLI + agent workflows |
| orchestrator | `src/orchestrator-cli/copilot-agent-prompt.mjs` | Prompt builder |
| LLM pipeline | `src/CodeIntel.Infrastructure/Llm/LlmSkillCatalog.cs` | 5 skill prompts |
| LLM pipeline | `src/CodeIntel.Infrastructure/Llm/StaticAnalysisBroker.cs` | Grounding |
| LLM pipeline | `src/CodeIntel.Infrastructure/Llm/SymbolContextProvider.cs` | Cross-file resolution |
| LLM pipeline | `src/CodeIntel.Infrastructure/Llm/EmbeddingContextProvider.cs` | Semantic enrichment |
| Domain | `src/CodeIntel.Domain/Entities/Finding.cs` | Fingerprint dedup |
| Domain | `src/CodeIntel.Domain/Entities/Rule.cs` | Confidence calibration |
