# NATLClaw — Second Brain Research & Comparison

## 1. Context

This document surveys existing "second brain" and memory solutions for agentic
AI, compares them to NATLClaw's implementation, and captures insights from
Andrej Karpathy's LLM Knowledge Base pattern (April 2026).

---

## 2. Karpathy's LLM Knowledge Base

**Source**: [Karpathy's X post](https://x.com/karpathy/status/2039805659525644595)
(April 2, 2026) — 100K+ bookmarks, 5,000+ GitHub Gist stars in 48 hours.
Walkthrough by [@godofprompt](https://x.com/godofprompt/status/2041265656893489419).

### 2.1 Core Idea

Instead of the AI searching raw files every time (how ChatGPT uploads,
NotebookLM, and most RAG systems work), the AI reads sources **once** and
compiles a **structured markdown wiki**. Summaries, cross-references,
connections between ideas, contradictions flagged. All maintained by the AI.
All in simple markdown files.

> "The human's job is to curate sources, direct the analysis, ask good
> questions, and think about what it all means. The LLM's job is everything
> else." — Andrej Karpathy

### 2.2 Architecture

```
project/
├── raw/            ← dump sources here (articles, notes, PDFs, bookmarks)
│   └── assets/     ← images, diagrams
├── wiki/           ← AI-maintained structured knowledge base
│   ├── index.md    ← master index of all pages
│   └── *.md        ← one page per topic, cross-referenced
├── outputs/        ← generated analyses, briefings, visualizations
└── CLAUDE.md       ← schema file: rules for how the AI organizes knowledge
```

### 2.3 The 7-Step System

| Step | Action | Human effort |
|------|--------|-------------|
| 1. Folder structure | Create `raw/`, `wiki/`, `outputs/` | 2 minutes |
| 2. Schema file | Write `CLAUDE.md` — org rules, categories, citation format | 10 minutes (copy template) |
| 3. Fill raw folder | Dump sources — no organizing required | 10 minutes |
| 4. First ingest | AI processes one source at a time → creates/updates wiki pages | 10 min/source |
| 5. Query | Ask questions; AI answers from wiki, not raw docs | Ongoing |
| 6. Monthly lint | AI audits for errors, stale pages, missing citations | 10 minutes/month |
| 7. Compound | File good answers back; every source makes wiki richer | Ongoing |

### 2.4 Key Design Choices

- **No vector database, no embeddings, no RAG** — deliberate. Navigation is
  via the wiki index file, not semantic search.
- **One source at a time** — produces better results than batch ingestion.
- **Citations required** — every wiki claim must cite `[Source: filename]`.
- **Error compounding is the #1 risk** — mistakes get reinforced when outputs
  are filed back. Monthly lint is the mitigation.
- **Scale ceiling ~100 articles / ~400K words** — beyond this, the index
  approach breaks and you need real infrastructure.

### 2.5 Known Limitations (from Karpathy & community)

| Limitation | Detail |
|------------|--------|
| Context window ceiling | ~128K tokens ≈ ~96K words; AI reads selectively via index, can miss things |
| Error compounding | Subtle mistakes → bad answers → filed back → two pages reinforce same error |
| Hallucination persists | Reduced (grounded in sources) but not eliminated; wiki "looks authoritative" |
| Cost | $2–5 per source ingestion with frontier models; 50 sources ≈ $100–250 |
| No enterprise scale | Index-file approach works ≤100 articles; 10K+ breaks |
| Single-model bias | Entire wiki shaped by one LLM's interpretation |

---

## 3. Alternative Solutions

### 3.1 Mem0 (formerly EmbedChain)

Purpose-built memory layer for AI agents.

| Aspect | Detail |
|--------|--------|
| Storage | Vector DB (Qdrant, Pinecone, ChromaDB) + graph store |
| Retrieval | Semantic similarity search via embeddings |
| Memory types | Short-term (session), long-term (persistent), entity memories |
| Key feature | Automatic memory extraction — system decides what to remember |
| Deduplication | Built-in conflict resolution and memory merging |
| Graph | Entity-relationship graph for structured knowledge |

**vs. NATLClaw**: Mem0 focuses on *automatic* extraction with vector search.
NATLClaw uses *prompted* LLM extraction and tag-based retrieval. Mem0 would
find semantically related past notes without needing exact tags.

### 3.2 LangChain / LangGraph Memory

Memory modules within the LangChain ecosystem.

| Aspect | Detail |
|--------|--------|
| Types | `ConversationBufferMemory`, `ConversationSummaryMemory`, `VectorStoreRetrieverMemory`, `EntityMemory` |
| Storage backends | Redis, Postgres (pgvector), Pinecone, FAISS, SQLite |
| Retrieval | Embedding-based similarity + metadata filtering |
| Key feature | Composable memory — stack multiple types per agent |
| Graph | LangGraph adds stateful, checkpointed workflows with `MemorySaver` |

**vs. NATLClaw**: LangGraph's `MemorySaver` is conceptually similar to
`AgentState` + `BrainState` checkpointing, but adds time-travel (replay from
any checkpoint). NATLClaw's workflow modes (`second_brain`, `freeform`, `steps`)
map to LangGraph's graph-based workflow nodes.

### 3.3 CrewAI Memory

Multi-agent framework with built-in memory system.

| Aspect | Detail |
|--------|--------|
| Memory types | Short-term (task), long-term (cross-session), entity, contextual |
| Storage | RAG-based with embeddings; ChromaDB by default |
| Key feature | Crew agents share long-term memory but have private short-term memory |
| Learning | Agents learn from task outcomes across runs |

**vs. NATLClaw**: NATLClaw's persona system parallels CrewAI's crew concept,
but each persona runs alone per heartbeat. CrewAI crews collaborate on a single
task. NATLClaw's `lessons_learned` is a simpler version of CrewAI's cross-session
learning.

### 3.4 Microsoft AutoGen Memory

Part of the AutoGen multi-agent framework.

| Aspect | Detail |
|--------|--------|
| Memory | `TeachableAgent` — learns from user feedback, stores in ChromaDB |
| Retrieval | Embedding-based with configurable distance threshold |
| Key feature | Agents can be "taught" facts that persist across sessions |
| Orchestration | `GroupChat` for multi-agent coordination with shared context |

**vs. NATLClaw**: AutoGen's `TeachableAgent` mirrors the capture workflow but
uses vector retrieval instead of summary injection.

### 3.5 Letta (formerly MemGPT)

OS-inspired memory management for LLM agents.

| Aspect | Detail |
|--------|--------|
| Architecture | Tiered: core (in-context), archival (vector DB), recall (conversation) |
| Key feature | Agent *manages its own memory* — promotes/demotes between tiers |
| Retrieval | Embedding search over archival; recent-first for recall |
| Self-editing | Agent can rewrite its own system prompt (core memory) |
| Persistence | Built-in server with SQLite / Postgres |

**vs. NATLClaw**: Closest conceptual match. NATLClaw's heartbeat loop
(status → capture → connect → review) parallels MemGPT's inner monologue.
The key difference: MemGPT lets the agent *edit its own instructions* and
*choose what to archive*, while NATLClaw follows a fixed workflow.

### 3.6 Zep

Long-term memory service for AI assistants.

| Aspect | Detail |
|--------|--------|
| Features | Auto-summarization, entity extraction, temporal awareness |
| Storage | Postgres with pgvector |
| Key feature | "Memory enrichment" — auto-extracts entities, relationships, facts |
| Retrieval | Hybrid: vector similarity + graph traversal + recency weighting |
| Temporal | Time-awareness — knows when facts were learned; decays old ones |

**vs. NATLClaw**: Zep's temporal awareness and automatic entity extraction are
features NATLClaw lacks. `build_brain_summary()` does manual recency-based
injection; Zep would automatically weight recent + relevant knowledge.

---

## 4. Feature Comparison Matrix

| Feature | NATLClaw (current) | Mem0 | LangGraph | CrewAI | Letta/MemGPT | Zep | Karpathy Wiki |
|---------|-------------------|------|-----------|--------|--------------|-----|---------------|
| Vector/semantic search | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| Knowledge graph | Partial (connections) | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ (cross-refs) |
| PARA categories | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Auto memory extraction | ❌ (LLM-prompted) | ✅ | ❌ | ✅ | ✅ | ✅ | ❌ (manual ingest) |
| Heartbeat / periodic | ✅ | ❌ | Via scheduler | ❌ | ✅ (inner loop) | ❌ | ❌ |
| Multi-persona | ✅ | ❌ | Via graph nodes | ✅ (crews) | ❌ | ❌ | ❌ |
| Deduplication | ❌ | ✅ | ❌ | ❌ | ✅ | ✅ | ❌ |
| Memory decay/forgetting | ❌ | ❌ | ❌ | ❌ | ✅ (tiered) | ✅ (temporal) | ❌ |
| Self-modifying prompts | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ |
| Scalable storage | ❌ (JSON) | ✅ (vector DB) | ✅ (checkpointer) | ✅ (ChromaDB) | ✅ (DB) | ✅ (Postgres) | ❌ (files) |
| Atomic persistence | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ | ❌ |
| Workflow modes | 3 modes | N/A | Graph-based | Task pipeline | Inner/outer loop | N/A | Manual steps |
| MCP tool support | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Fully autonomous | ✅ | ❌ | Partial | Partial | ✅ | ❌ | ❌ (human-directed) |
| Error correction | Lesson extraction | Conflict resolution | ❌ | ❌ | Self-editing | Temporal decay | Monthly lint |
| Cost model | Per-heartbeat tokens | Per-operation tokens | Per-operation | Per-task | Per-interaction | SaaS / self-hosted | Per-ingest + query |

---

## 5. NATLClaw Strengths

What NATLClaw already does well relative to the field:

- **Fully autonomous operation** — heartbeat loop requires zero human
  intervention, unlike Karpathy's system which needs manual ingest/lint/query
- **Persona system** — pluggable roles with separate instructions, tools, MCP
  servers, and workflow modes; more flexible than CrewAI's static crew configs
- **Bidirectional knowledge connections** — explicit links with reasons between
  notes, creating a knowledge graph (most alternatives don't model connections)
- **Multiple workflow modes** — `second_brain`, `freeform`, `steps` with
  stepwise execution; adapts to different agent roles
- **Learning from errors** — automatic lesson extraction from agent responses
  via signal-word detection in `learning.py`
- **MCP integration** — native support for MCP tool servers, which none of the
  above alternatives provide
- **Atomic file persistence** — crash-safe writes with temp-file-then-rename

---

## 6. NATLClaw Gaps

The highest-impact improvements based on this research:

### 6.1 No Semantic / Vector Search (High Impact)

Every alternative except Karpathy's wiki uses embedding-based retrieval.
NATLClaw relies on tag/category/recency filtering via `build_brain_summary()`.
This means the agent may miss relevant older notes that don't share tags with
the current context.

**Recommendation**: Integrate ChromaDB or `sqlite-vss` for local embedding
search. Use it in `build_brain_summary()` to retrieve notes by semantic
relevance to the current task, not just recency.

### 6.2 No Tiered Memory (High Impact)

All notes are equal. There's no distinction between a fresh raw observation
and a well-established, cross-verified insight. The prompt summary grows
linearly with note count.

**Recommendation**: Implement the tiered memory architecture described in
[tiered-memory.md](tiered-memory.md) — atomic notes as short-term, wiki pages
as long-term, with a consolidation step.

### 6.3 No Deduplication (Medium Impact)

The agent can capture near-identical notes across heartbeats. Over time this
wastes context window space and dilutes the knowledge graph.

**Recommendation**: Before storing a new note, compute similarity against
recent notes (cosine distance on embeddings or simple text overlap). Skip or
merge if above threshold.

### 6.4 No Memory Decay (Medium Impact)

Old notes never lose relevance weight. A note from heartbeat #1 has the same
standing as one from heartbeat #500, even if the information is outdated.

**Recommendation**: Add a `relevance_score` field that decays over time
(e.g., halve every N heartbeats) unless the note is accessed, connected, or
consolidated into a wiki page.

### 6.5 No Lint / Error Correction (Medium Impact)

Karpathy identifies error compounding as the #1 risk of AI-maintained knowledge
bases. NATLClaw's `learning.py` catches errors in individual responses but
doesn't audit the accumulated knowledge for consistency.

**Recommendation**: Add a periodic lint step that audits wiki pages for
contradictions, stale content, and missing citations. See the lint design in
[tiered-memory.md](tiered-memory.md).

### 6.6 Flat File Storage (Low Impact, for now)

JSON works fine at the current scale (< 1,000 notes). But it won't scale
past ~10K entries due to full-file read/write on every operation.

**Recommendation**: Migrate to SQLite when note count exceeds a threshold.
This also enables indexing, transactions, and integration with vector
extensions (`sqlite-vss`).

---

## 7. Recommended Roadmap

| Phase | Work | Dependencies |
|-------|------|-------------|
| **Phase 1** | Tiered memory: wiki pages + consolidation step | None |
| **Phase 2** | Lint / health check step | Phase 1 (needs wiki pages to audit) |
| **Phase 3** | Vector search with ChromaDB or sqlite-vss | None (can parallelize) |
| **Phase 4** | Deduplication using embeddings | Phase 3 |
| **Phase 5** | Memory decay / relevance scoring | Phase 1 |
| **Phase 6** | SQLite storage migration | Phase 3 (if using sqlite-vss) |

Phase 1 and Phase 3 are independent and can be developed in parallel. Phase 1
delivers the most architectural value; Phase 3 delivers the most retrieval
quality improvement.
