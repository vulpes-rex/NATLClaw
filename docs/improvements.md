# NATLClaw — Improvement Roadmap

## Overview

This document catalogues improvements to the NATLClaw agent, grouped by impact and effort. Each item includes the problem, proposed solution, affected files, and rough effort estimate.

---

## 1  High Impact — Architecture

### 1.1  Semantic search over notes

**Problem:** `build_brain_summary()` dumps the N most recent notes into every prompt. As the brain grows, recent ≠ relevant. The agent wastes context window on notes unrelated to the current heartbeat topic.

**Proposal:** Add a lightweight embedding index using `sentence-transformers` with a local FAISS or ChromaDB store. At capture time, embed each note. At prompt-build time, embed the current task/topic and retrieve the top-K most similar notes instead of the most recent.

**Affected files:** `second_brain.py`, `scheduler.py`, `config.py`

**Effort:** 1–2 days

**Implementation sketch:**

```python
# second_brain.py additions
from sentence_transformers import SentenceTransformer
import faiss
import numpy as np

class BrainIndex:
    """Semantic search index over brain notes."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model = SentenceTransformer(model_name)
        self.index: faiss.IndexFlatIP | None = None
        self.note_ids: list[str] = []

    def rebuild(self, brain: BrainState) -> None:
        texts = [n.get("content", "") for n in brain.notes.values()]
        self.note_ids = list(brain.notes.keys())
        embeddings = self.model.encode(texts, normalize_embeddings=True)
        self.index = faiss.IndexFlatIP(embeddings.shape[1])
        self.index.add(embeddings.astype(np.float32))

    def query(self, text: str, k: int = 5) -> list[str]:
        if not self.index or self.index.ntotal == 0:
            return []
        vec = self.model.encode([text], normalize_embeddings=True).astype(np.float32)
        _, indices = self.index.search(vec, min(k, self.index.ntotal))
        return [self.note_ids[i] for i in indices[0] if i >= 0]
```

The `build_brain_summary()` function would accept an optional query string and use the index to select relevant notes instead of calling `get_recent_notes()`.

---

### 1.2  Tiered memory (designed, not implemented)

**Problem:** Notes accumulate without consolidation. The brain gets noisier over time — hundreds of atomic notes with overlapping content but no synthesis.

**Proposal:** Implement the two-tier model from `docs/tiered-memory.md`:
- **Tier 1 (short-term):** Atomic notes (current `Note` dataclass) — captured each heartbeat, TTL-based expiry.
- **Tier 2 (long-term):** Wiki pages — consolidated summaries created by periodically merging related atomic notes.

A consolidation step runs every N heartbeats: the agent reviews clusters of connected/similar notes and produces a wiki page that replaces them.

**Affected files:** `second_brain.py`, `workflow.py`, `scheduler.py`

**Effort:** 2–3 days

**Key additions:**

```python
@dataclass
class WikiPage:
    """Long-term consolidated knowledge page."""
    id: str
    title: str
    content: str
    source_notes: list[str]   # IDs of atomic notes that were merged
    tags: list[str]
    category: str
    created_at: str
    updated_at: str

@dataclass
class BrainState:
    notes: dict[str, dict] = field(default_factory=dict)
    pages: dict[str, dict] = field(default_factory=dict)  # NEW
    connections: list[dict] = field(default_factory=list)
    review_log: list[dict] = field(default_factory=list)
    capture_count: int = 0
    page_count: int = 0                                    # NEW
    last_review: str | None = None
    last_consolidation: str | None = None                  # NEW
```

---

### 1.3  Note deduplication and decay

**Problem:** Nothing prevents the agent from capturing the same insight repeatedly. Older notes that are never connected or referenced continue consuming context budget forever.

**Proposal:**
1. **Dedup at capture time:** Before storing a new note, compute cosine similarity against the last 20 notes. If similarity > 0.85, merge into the existing note instead of creating a new one.
2. **Decay to archive:** Notes older than N days with zero connections and zero references in review logs are automatically moved to `category: "archive"`. Archived notes are excluded from `build_brain_summary()`.

**Affected files:** `second_brain.py`, `workflow.py`

**Effort:** 0.5–1 day

**Implementation sketch:**

```python
def _is_duplicate(brain: BrainState, content: str, threshold: float = 0.85) -> str | None:
    """Return the ID of a near-duplicate note, or None."""
    recent = get_recent_notes(brain, 20)
    for note in recent:
        similarity = _cosine_sim(content, note.get("content", ""))
        if similarity > threshold:
            return note["id"]
    return None

def decay_stale_notes(brain: BrainState, max_age_days: int = 30) -> int:
    """Move orphan notes older than max_age_days to archive. Returns count."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    connected_ids = {c["from"] for c in brain.connections} | {c["to"] for c in brain.connections}
    archived = 0
    for nid, note in brain.notes.items():
        if (note.get("category") != "archive"
            and note.get("created_at", "") < cutoff
            and nid not in connected_ids):
            note["category"] = "archive"
            archived += 1
    return archived
```

---

### 1.4  Adaptive heartbeat interval

**Problem:** The heartbeat fires every 120s regardless of productivity. Low-value cycles waste tokens; high-value discoveries don't get follow-up soon enough.

**Proposal:** After each heartbeat, score the cycle's productivity:
- +1 for each new note captured
- +2 for each new connection created
- +1 if the review identified a knowledge gap
- −1 if no new note was stored
- −2 if the capture was a near-duplicate

Scale the interval: `next_interval = base_interval * (1.5 if score <= 0 else 0.7)`, clamped to `[60s, 600s]`.

**Affected files:** `scheduler.py`, `config.py`

**Effort:** 0.5 day

**Implementation sketch:**

```python
# scheduler.py — after run_heartbeat()
notes_before = initial_note_count
notes_after = len(brain.notes)
connections_before = initial_conn_count
connections_after = len(brain.connections)

score = (notes_after - notes_before) + 2 * (connections_after - connections_before)
if score <= 0:
    interval = min(config.heartbeat_interval_sec * 1.5, 600)
else:
    interval = max(config.heartbeat_interval_sec * 0.7, 60)
```

---

## 2  High Impact — Reliability

### 2.1  `save_state` is not async (latent bug)

**Problem:** `save_state()` in `state.py` is a synchronous function, but `scheduler.py` wraps it with the `retry()` decorator that `await`s the wrapped function. This works by accident when no retries are needed (awaiting a sync function returns the result), but fails under retry conditions because the decorator calls `await func(...)` on each attempt.

**Fix:** Make `save_state` async, matching `save_brain`:

```python
async def save_state(state: AgentState, path: str, max_history: int = 100) -> None:
    # ... same logic, wrapped in run_in_executor for the I/O ...
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _write_state, state, path, max_history)
```

**Affected files:** `state.py`

**Effort:** 10 minutes

---

### 2.2  File handle leak in `load_brain`

**Problem:** `second_brain.py` line 56 uses `json.load(open(path, "r", ...))` without a `with` statement. The file handle is never explicitly closed. Under retry conditions (up to 3 attempts), this could leak multiple handles.

**Fix:**

```python
# Before (leaks handle)
data = await loop.run_in_executor(
    None, lambda: json.load(open(path, "r", encoding="utf-8"))
)

# After (safe)
def _read_json(p: str) -> dict:
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

data = await loop.run_in_executor(None, _read_json, path)
```

**Affected files:** `second_brain.py`

**Effort:** 2 minutes

---

### 2.3  `elapsed` referenced before assignment on timeout

**Problem:** In `scheduler.py`, the `except asyncio.TimeoutError` block at line 109 references `elapsed`, but `elapsed` is only assigned at line 103 *after* the heartbeat completes. If the timeout fires before the heartbeat finishes, `elapsed` is unbound and raises `NameError`.

**Fix:** Compute elapsed directly from `cycle_start`:

```python
except asyncio.TimeoutError:
    logger.error(
        "Heartbeat timed out after %.1f seconds",
        time.monotonic() - cycle_start,
    )
```

**Affected files:** `scheduler.py`

**Effort:** 2 minutes

---

## 3  Medium Impact — Features

### 3.1  Goal / task awareness

**Problem:** The agent has no concept of goals. Each heartbeat runs the same persona task regardless of what happened before. There's no way to say "investigate X over the next 5 heartbeats" — each cycle is effectively stateless beyond the brain contents.

**Proposal:** Add a goal stack to `state.context`:

```python
state.context["active_goals"] = [
    {
        "id": "g001",
        "description": "Map the React component hierarchy in workspace/",
        "status": "in_progress",       # pending | in_progress | completed | abandoned
        "created_at": "...",
        "target_heartbeats": 5,
        "heartbeats_spent": 2,
        "progress_notes": ["Found 12 components", "Missing prop types in 4"],
    }
]
```

The review step at the end of each heartbeat would evaluate goal progress and decide whether to continue, pivot, or mark complete. The status check at the start of the next heartbeat would inject active goals into the prompt.

**Affected files:** `state.py`, `workflow.py`, `scheduler.py`

**Effort:** 1–2 days

---

### 3.2  External knowledge ingestion

**Problem:** The brain only grows from the agent's own LLM outputs. It can't ingest existing documents, articles, or codebases.

**Proposal:** Add an ingestion pipeline:

```python
async def ingest_file(brain: BrainState, path: str, agent) -> list[str]:
    """Read a file, chunk it, and store each chunk as a brain note."""
    content = Path(path).read_text(encoding="utf-8")
    chunks = _chunk_text(content, max_tokens=500)
    note_ids = []
    for i, chunk in enumerate(chunks):
        # Ask the agent to summarise each chunk
        summary = await agent.run(f"Summarise this in one sentence:\n{chunk}")
        nid = add_note(
            brain,
            content=chunk,
            summary=summary.text,
            source=f"file:{path}#chunk{i}",
            tags=["ingested"],
            category="resources",
        )
        note_ids.append(nid)
    return note_ids
```

Sources could include local files, RSS feeds, URLs, or clipboard contents. The `source` field on each note would track provenance.

**Affected files:** `second_brain.py` (new function), new `ingest.py` module

**Effort:** 1–2 days

---

### 3.3  Brain health / lint

**Problem:** The knowledge-quality spec in `docs/knowledge-quality.md` describes a lint step (orphan detection, stale notes, duplicate tagging, connection density) but it's not implemented.

**Proposal:** Run a health check every N heartbeats (e.g. every 10th cycle):

```python
def lint_brain(brain: BrainState) -> list[dict]:
    """Return a list of issues found in the brain."""
    issues = []

    # Orphan notes (no connections)
    connected = {c["from"] for c in brain.connections} | {c["to"] for c in brain.connections}
    for nid in brain.notes:
        if nid not in connected:
            issues.append({"type": "orphan", "note_id": nid, "severity": "info"})

    # Duplicate tags
    tag_counts: dict[str, int] = {}
    for note in brain.notes.values():
        for tag in note.get("tags", []):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    # ...stale notes, empty content, etc.

    return issues
```

The lint results would be injected into the next heartbeat's prompt so the agent can self-correct (merge orphans, archive stale notes, fix tags).

**Affected files:** `second_brain.py` (new function), `workflow.py` (conditional lint step)

**Effort:** 0.5–1 day

---

### 3.4  Source citation tracking

**Problem:** Notes have a `source` field but it's always `"agent"` or `"heartbeat"`. There's no provenance chain.

**Proposal:** Enrich every note's source with structured metadata:

```python
{
    "source": {
        "type": "heartbeat",
        "persona": "researcher",
        "heartbeat_number": 42,
        "step": "capture",
        "timestamp": "2026-04-07T10:30:00Z",
        "llm_model": "claude-sonnet-4",
    }
}
```

For ingested content, the source would track the file path, URL, or feed entry. This makes the brain fully auditable — you can trace any note back to exactly when, how, and by which persona it was created.

**Affected files:** `second_brain.py` (`add_note` signature), `workflow.py` (pass source metadata)

**Effort:** 0.5 day

---

### 3.5  Multi-persona orchestration

**Problem:** Only one persona runs per session. Switching requires editing `.env` and restarting.

**Proposal:** Add a `coordinator` workflow mode that runs multiple personas in sequence within a single heartbeat:

```jsonc
// mcp.json
"coordinator": {
    "description": "Multi-persona orchestrator",
    "workflow": "coordinator",
    "roster": ["researcher", "python_developer", "project_manager"],
    "schedule": "round_robin"  // or "all" or "priority"
}
```

Each heartbeat would load and run one (round-robin) or all (parallel) personas, with each contributing to the shared brain. The coordinator's review step would synthesise outputs from all personas.

**Affected files:** `workflow.py` (new mode), `persona_loader.py`, `scheduler.py`

**Effort:** 2–3 days

---

### 3.6  Topic-graph memory recall ✅ IMPLEMENTED

**Problem:** Notes have flat `tags` (string labels) and `connections` (note-to-note edges), but there's no way to ask "give me everything the brain knows about React." Retrieval is either by recency or PARA category — neither is topic-aware. As the brain grows, dumping recent notes into the prompt wastes context on irrelevant content.

**Proposal:** Promote tags into first-class **Topic** nodes in a knowledge graph. Each tag on a captured note becomes a topic node; notes link to topics (many-to-many). Co-occurring tags on the same note create topic-to-topic edges. Querying a topic traverses the graph — related topics up to N hops, then expands via note-to-note connections — returning a relevance-ranked subgraph instead of a flat list.

**Implementation (done):**
- `Topic` dataclass and `topics` / `topic_count` fields on `BrainState` (`second_brain.py`)
- `find_or_create_topic()` — case-insensitive upsert of topic nodes
- `assign_note_to_topic()` — link a note to a topic
- `relate_topics()` — bidirectional topic-to-topic edges
- `recall_by_topic(brain, name, depth=1)` — BFS through related topics + note-to-note connections
- `get_topic_map()` — lightweight summary for prompt injection
- `build_brain_summary(query_topic=...)` — selects notes by topic graph when a topic is provided, falls back to recency
- `_store_capture()` in `workflow.py` auto-assigns topics from tags and relates co-occurring tags

**Graph structure:**

```
    Topic("React") ── related ── Topic("hooks")
         │                            │
      note_ids                     note_ids
         │                            │
    ┌────▼───┐   note-connection  ┌───▼────┐
    │ n0012  │───────────────────▶│ n0015  │
    └────────┘                    └────────┘
```

**Affected files:** `second_brain.py`, `workflow.py`

**Effort:** 0.5 day (implemented)

---

## 4  Medium Impact — Developer Experience

### 4.1  CLI with subcommands

**Problem:** `main.py` only starts the scheduler loop. There's no way to interact with the agent or brain from the command line.

**Proposal:** Add a CLI using `click` or `typer`:

```
natl run                    # Start the heartbeat scheduler (current behavior)
natl run --once             # Run a single heartbeat and exit
natl brain stats            # Show brain note count, categories, connections
natl brain search "React"   # Full-text or semantic search over notes
natl brain add "..."        # Manually add a note
natl brain export           # Dump brain to markdown
natl brain lint             # Run health check
natl persona list           # Show available personas
natl persona switch <name>  # Switch persona without editing .env
natl config show            # Print resolved config
natl config validate        # Check for missing/invalid settings
```

**Affected files:** New `cli.py`, refactor `main.py`

**Effort:** 1–2 days

---

### 4.2  Structured logging / metrics

**Problem:** All logging is unstructured `logger.info()` strings. Parsing logs for trends (tokens used, notes created per cycle, step durations) requires regex.

**Proposal:** Add JSON structured logging with consistent fields:

```python
logger.info(
    "heartbeat_complete",
    extra={
        "heartbeat": state.execution_count,
        "elapsed_sec": elapsed,
        "notes_created": notes_after - notes_before,
        "connections_created": conns_after - conns_before,
        "persona": persona.name,
        "workflow": persona.workflow,
    },
)
```

Optionally emit metrics to a local SQLite database for dashboarding:

```sql
CREATE TABLE heartbeat_metrics (
    id INTEGER PRIMARY KEY,
    timestamp TEXT,
    heartbeat_number INTEGER,
    persona TEXT,
    elapsed_sec REAL,
    notes_created INTEGER,
    connections_created INTEGER,
    tokens_used INTEGER
);
```

**Affected files:** `scheduler.py`, `workflow.py`, new `metrics.py`

**Effort:** 1 day

---

### 4.3  Configuration validation

**Problem:** `load_config()` reads env vars with no validation. Invalid values (negative interval, unknown provider, missing API key for selected provider) fail deep in the stack with cryptic errors.

**Proposal:** Add a validation pass at startup:

```python
def validate_config(config: AppConfig) -> list[str]:
    """Return a list of validation errors, empty if config is valid."""
    errors = []
    if config.heartbeat_interval_sec < 10:
        errors.append(f"heartbeat_interval_sec={config.heartbeat_interval_sec} is too low (min 10)")
    if config.provider not in ("copilot", "foundry", "openai", "ollama"):
        errors.append(f"Unknown provider: {config.provider!r}")
    if config.provider == "foundry" and not config.project_endpoint:
        errors.append("AZURE_AI_PROJECT_ENDPOINT is required for provider=foundry")
    if config.provider == "openai" and not config.openai_api_key:
        errors.append("OPENAI_API_KEY is required for provider=openai")
    if config.max_history < 1:
        errors.append(f"max_history={config.max_history} must be >= 1")
    return errors
```

Called in `main.py` before starting the scheduler, with clear error messages printed to stderr.

**Affected files:** `config.py`, `main.py`

**Effort:** 20 minutes

---

### 4.4  Hot reload for personas

**Problem:** Changing a persona or its instructions requires restarting the agent.

**Proposal:** At the start of each heartbeat in `run_scheduler()`, check if `mcp.json` or the persona's `instructions.md` has been modified (via `os.path.getmtime`). If so, reload the persona and rebuild the agent with the new instructions.

```python
# scheduler.py — inside the while loop, before creating the agent
mcp_mtime = os.path.getmtime("mcp.json")
if mcp_mtime != last_mcp_mtime:
    logger.info("mcp.json changed, reloading persona")
    persona = load_persona(config.persona)
    last_mcp_mtime = mcp_mtime
```

**Affected files:** `scheduler.py`

**Effort:** 30 minutes

---

## 5  Code Quality — Quick Fixes

### 5.1  Duplicated retry logic

**Problem:** `second_brain.py` has internal retry loops in both `load_brain()` and `save_brain()`. `scheduler.py` also wraps these functions with its own `retry()` decorator. This means failures are retried 3 × 3 = 9 times, with independent backoff timers.

**Fix:** Remove the internal retry loops from `load_brain()` and `save_brain()`, relying solely on the `scheduler.py` retry decorator. Or remove the decorator wrapping and keep only the internal retries.

**Affected files:** `second_brain.py` or `scheduler.py`

**Effort:** 15 minutes

---

### 5.2  Inconsistent async patterns

**Problem:** `save_state` is sync, `load_state` is async, `save_brain` is async, `load_brain` is async. The `retry` decorator in `scheduler.py` assumes all are async.

**Fix:** Make everything consistently async. For I/O operations, use `run_in_executor`:

```python
async def save_state(state: AgentState, path: str, max_history: int = 100) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _sync_save_state, state, path, max_history)
```

**Affected files:** `state.py`

**Effort:** 15 minutes

---

### 5.3  Prompt templates as strings in code

**Problem:** All prompts are f-strings embedded in `workflow.py`. Changing a prompt requires editing Python code, which risks syntax errors and makes A/B testing difficult.

**Proposal:** Move prompts to a `prompts/` directory as Jinja2 or simple `.txt` templates:

```
prompts/
  second_brain/
    status_check.txt
    capture.txt
    connect.txt
    review.txt
  freeform/
    status_check.txt
    task.txt
    capture.txt
    review.txt
```

Load with a simple `load_prompt(mode, step, **context)` function that reads the file and substitutes variables.

**Affected files:** `workflow.py`, new `prompts.py`, new `prompts/` directory

**Effort:** 1 day

---

### 5.4  Missing test coverage for workflow modes

**Problem:** The test files focus on state persistence, scheduler retry logic, and shell security. The three workflow modes (`second_brain`, `freeform`, `steps`) and the brain helpers (`_store_capture`, `_store_connection`, `_distil_to_brain`) have no dedicated unit tests.

**Proposal:** Add `test_workflow_modes.py` with:
- Mock agent returning canned JSON responses
- Test `_store_capture` with valid JSON, malformed JSON, markdown-wrapped JSON
- Test `_store_connection` with valid/invalid note IDs
- Test `_run_second_brain_heartbeat` end-to-end with mock agent
- Test `_run_steps_heartbeat` with stepwise=True (verify pointer advances)
- Test `_run_steps_heartbeat` pointer reset after all steps complete

**Affected files:** New `test_workflow_modes.py`

**Effort:** 1 day

---

## 6  Priority Matrix

| # | Improvement | Impact | Effort | Priority |
|---|---|---|---|---|
| 2.1 | Fix `save_state` async bug | High | 10 min | **P0** |
| 2.2 | Fix file handle leak | High | 2 min | **P0** |
| 2.3 | Fix `elapsed` NameError | High | 2 min | **P0** |
| 5.1 | Remove duplicated retries | Medium | 15 min | **P0** |
| 4.3 | Config validation | Medium | 20 min | **P1** |
| 1.3 | Note deduplication/decay | High | 0.5–1 day | **P1** |
| 3.3 | Brain lint/health check | Medium | 0.5–1 day | **P1** |
| 3.4 | Source citation tracking | Medium | 0.5 day | **P1** |
| 1.4 | Adaptive heartbeat interval | High | 0.5 day | **P1** |
| 4.4 | Hot reload for personas | Medium | 30 min | **P1** |
| 3.6 | Topic-graph memory recall | High | 0.5 day | **Done** |
| 1.2 | Tiered memory | High | 2–3 days | **P2** |
| 1.1 | Semantic search | High | 1–2 days | **P2** |
| 3.1 | Goal/task awareness | Medium | 1–2 days | **P2** |
| 4.1 | CLI with subcommands | Medium | 1–2 days | **P2** |
| 3.2 | External ingestion | Medium | 1–2 days | **P2** |
| 4.2 | Structured logging | Medium | 1 day | **P2** |
| 3.5 | Multi-persona orchestration | Medium | 2–3 days | **P3** |
| 5.3 | Prompt templates | Low | 1 day | **P3** |
| 5.4 | Workflow test coverage | Low | 1 day | **P3** |
| 5.2 | Consistent async patterns | Low | 15 min | **P3** |

---

## 7  Suggested Implementation Order

### Phase 1: Bug fixes and quick wins (1 session)
1. Fix `save_state` async bug
2. Fix file handle leak in `load_brain`
3. Fix `elapsed` NameError in scheduler
4. Remove duplicated retry logic
5. Add config validation

### Phase 2: Knowledge quality (1–2 sessions)
6. Note deduplication at capture time
7. Note decay to archive
8. Brain lint/health check step
9. Source citation tracking
10. Adaptive heartbeat interval

### Phase 3: Knowledge architecture (2–3 sessions)
11. Tiered memory (atomic notes → wiki pages)
12. Semantic search over notes
13. Goal/task awareness

### Phase 4: Developer experience (2–3 sessions)
14. CLI with subcommands
15. Hot reload for personas
16. Structured logging/metrics
17. External knowledge ingestion

### Phase 5: Advanced features (3+ sessions)
18. Multi-persona orchestration
19. Prompt templates
20. Workflow test coverage
