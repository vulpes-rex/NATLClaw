# NATLClaw — Tiered Memory Architecture

## 1. Overview

NATLClaw's second brain currently uses **atomic notes** as its sole knowledge
unit. Every heartbeat captures a short note, links it to related notes, and
injects the most recent entries into the agent's prompt.

This document proposes a **two-tier memory model** — atomic notes as short-term
memory and wiki-style pages as long-term memory — with a **consolidation step**
that promotes short-term captures into durable, synthesized knowledge.

The design is inspired by:

- **MemGPT / Letta** — three-tier memory (core / recall / archival) where the
  agent manages its own memory hierarchy
- **Karpathy's LLM Knowledge Base** — AI-maintained structured wiki compiled
  from raw sources, updated in-place, queried instead of raw documents
- **Tiago Forte's PARA method** — already partially implemented in NATLClaw's
  category system (projects / areas / resources / archive)
- **Zep** — temporal awareness and memory decay for relevance weighting

---

## 2. Memory Tiers

### 2.1 Short-Term Memory — Atomic Notes (existing)

Atomic notes are the raw building blocks. They capture single observations,
errors, findings, or insights — one per heartbeat step.

| Property       | Detail |
|----------------|--------|
| **Granularity** | One idea per note |
| **Lifespan**    | Created each heartbeat; archived after consolidation |
| **Volume**      | High — multiple notes per heartbeat cycle |
| **Curation**    | Low — the agent writes freely; accuracy is "good enough" |
| **Purpose**     | Capture everything; filter later |

Notes use the existing `Note` dataclass:

```python
@dataclass
class Note:
    id: str
    content: str
    summary: str
    source: str          # "heartbeat", "user", "tool"
    tags: list[str]
    category: str        # "projects" | "areas" | "resources" | "archive"
    connections: list[str]
    created_at: str
    updated_at: str
```

### 2.2 Long-Term Memory — Wiki Pages (new)

Wiki pages are **synthesized, maintained documents** — one per topic or theme.
They accumulate knowledge from multiple atomic notes, resolve contradictions,
and serve as the agent's stable reference material.

| Property       | Detail |
|----------------|--------|
| **Granularity** | One page per topic / theme / domain area |
| **Lifespan**    | Persistent — updated in-place, never discarded |
| **Volume**      | Low — grows slowly as new topics emerge |
| **Curation**    | High — consolidated, cross-referenced, error-checked |
| **Purpose**     | Serve as trusted, compressed knowledge for prompt injection |

Proposed `WikiPage` dataclass:

```python
@dataclass
class WikiPage:
    id: str              # slug, e.g. "deployment-patterns"
    title: str           # human-readable title
    content: str         # full markdown body
    sources: list[str]   # note IDs that contributed to this page
    tags: list[str]
    created_at: str
    updated_at: str
```

### 2.3 Working Memory — Prompt Context (implicit)

The agent's prompt context each heartbeat is its "working memory." It is
assembled from:

1. **Persona instructions** — role, guidelines, task description
2. **Wiki page summaries** — first paragraph or title of each page (long-term)
3. **Recent atomic notes** — last 3–5 unconsolidated notes (short-term)
4. **Agent state** — execution count, last heartbeat, lessons learned
5. **Current task** — the specific step prompt

This mapping mirrors MemGPT's tiers:

```
┌──────────────────────────────────────────┐
│  PROMPT CONTEXT  (working memory)        │
│  ├── persona instructions                │
│  ├── wiki page summaries (long-term)     │
│  ├── recent atomic notes (short-term)    │
│  ├── agent state (lessons, history)      │
│  └── current task prompt                 │
└──────────────────────────────────────────┘
              ↑ assembled from ↑
┌──────────────────────────────────────────┐
│  WIKI PAGES  (long-term memory)          │
│  ├── one page per topic                  │
│  ├── updated by consolidation step       │
│  └── cited back to source note IDs       │
└──────────────────────────────────────────┘
              ↑ promoted from ↑
┌──────────────────────────────────────────┐
│  ATOMIC NOTES  (short-term memory)       │
│  ├── created every heartbeat             │
│  ├── high volume, low curation           │
│  └── archived after consolidation        │
└──────────────────────────────────────────┘
```

---

## 3. Consolidation

### 3.1 What Is Consolidation?

Consolidation is the process of **promoting atomic notes into wiki pages**.
It is analogous to how human memory consolidates short-term experiences into
long-term knowledge during sleep.

### 3.2 When Does It Run?

Two strategies (configurable per persona):

| Strategy | Trigger | Best for |
|----------|---------|----------|
| **Periodic** | Every N heartbeats (e.g., every 5th cycle) | Long-running agents with steady capture |
| **Threshold** | When unconsolidated note count exceeds K (e.g., 10 notes) | Bursty agents that capture a lot per cycle |

A new field on `Persona` controls this:

```python
consolidation_interval: int = 5    # every N heartbeats (0 = threshold mode)
consolidation_threshold: int = 10  # max unconsolidated notes before forced
```

### 3.3 How Does It Work?

The consolidation step is injected into the workflow as an additional phase:

```
Heartbeat N:
  [status] → [capture] → [connect] → [review]
                ↓ notes accumulate

Heartbeat N+5 (consolidation cycle):
  [status] → [capture] → [connect] → [CONSOLIDATE] → [review]
```

The consolidation prompt:

1. **Gather** — collect all notes with `category != "archive"` that haven't
   been consolidated yet (tracked via a `consolidated: bool` flag or by
   checking membership in any wiki page's `sources` list)
2. **Group** — ask the LLM to cluster notes by topic
3. **Update or Create** — for each topic cluster:
   - If a wiki page exists for that topic → update it with new information
   - If no page exists → create a new one
   - Resolve contradictions between old page content and new notes
   - Cite which note IDs contributed to each section
4. **Archive** — set consumed notes' category to `"archive"`

### 3.4 Consolidation Prompt Template

```
You are maintaining a knowledge wiki. Here are recent atomic notes that need
to be consolidated into wiki pages.

NOTES:
{notes_json}

EXISTING WIKI PAGES:
{page_titles_and_summaries}

For each group of related notes, either UPDATE an existing wiki page or CREATE
a new one.

Return JSON:
{
  "updates": [
    {
      "page_id": "existing-page-id",
      "new_content": "full updated markdown body",
      "sources_added": ["n0012", "n0015"]
    }
  ],
  "creates": [
    {
      "title": "New Topic Title",
      "content": "full markdown body",
      "sources": ["n0013", "n0014"],
      "tags": ["tag1", "tag2"]
    }
  ],
  "archived_notes": ["n0012", "n0013", "n0014", "n0015"]
}
```

---

## 4. Lint / Health Checks

Inspired by Karpathy's monthly lint step, the system should periodically audit
long-term memory for quality. This runs less frequently than consolidation
(e.g., every 20 heartbeats or once per day).

### 4.1 What the Lint Step Checks

| Check | Description |
|-------|-------------|
| **Stale pages** | Pages not updated in N cycles — flag for review or archival |
| **Orphan notes** | Unconsolidated notes older than M cycles — force-consolidate or discard |
| **Contradictions** | Claims in one page that conflict with another page |
| **Missing citations** | Page sections that don't reference any source note IDs |
| **Duplicate content** | Pages with high overlap that should be merged |

### 4.2 Lint Prompt Template

```
You are auditing a knowledge wiki for quality. Review the following pages
and report issues.

WIKI PAGES:
{pages_json}

Check for:
1. Pages with no updates in the last 10 heartbeats (stale)
2. Contradictions between pages
3. Sections missing source citations [Source: nXXXX]
4. Pages with highly overlapping content (merge candidates)
5. Factual claims that seem unsupported or hallucinated

Return JSON:
{
  "issues": [
    {
      "type": "stale | contradiction | missing_citation | duplicate | suspect_claim",
      "page_id": "...",
      "description": "...",
      "suggested_action": "update | merge | archive | flag"
    }
  ]
}
```

---

## 5. Changes to `BrainState`

### 5.1 Updated Dataclass

```python
@dataclass
class BrainState:
    # Short-term (existing)
    notes: dict[str, dict]         # id → Note as dict
    connections: list[dict]        # [{from, to, reason}]

    # Long-term (new)
    pages: dict[str, dict]         # id → WikiPage as dict

    # Metadata (existing + new)
    review_log: list[dict]         # [{timestamp, summary}]
    lint_log: list[dict]           # [{timestamp, issues}]  ← NEW
    capture_count: int
    last_review: str | None
    last_consolidation: str | None  # ← NEW
    last_lint: str | None           # ← NEW
```

### 5.2 New Helper Functions

```python
def add_page(brain, title, content, sources, tags) -> str:
    """Create a new wiki page. Returns the page ID (slug)."""

def update_page(brain, page_id, content, new_sources) -> None:
    """Update an existing wiki page with new content and sources."""

def get_unconsolidated_notes(brain) -> list[dict]:
    """Return notes not yet consumed by any wiki page."""

def build_wiki_summary(brain, max_pages=10) -> str:
    """Build a text summary of wiki pages for prompt injection."""

def should_consolidate(brain, interval, threshold) -> bool:
    """Check if consolidation should run this heartbeat."""

def should_lint(brain, interval) -> bool:
    """Check if a lint pass should run this heartbeat."""
```

### 5.3 Updated `build_brain_summary()`

The function shifts from listing recent atomic notes to prioritizing wiki page
summaries, with a small window of recent unconsolidated notes:

```python
def build_brain_summary(brain, max_pages=10, max_recent_notes=5) -> str:
    lines = ["== SECOND BRAIN =="]
    lines.append(f"Wiki pages: {len(brain.pages)}")
    lines.append(f"Atomic notes: {len(brain.notes)} ({unconsolidated} pending)")

    # Long-term: wiki page titles and first lines
    for page in sorted(brain.pages.values(), key=...):
        lines.append(f"  📄 {page['title']}: {page['content'][:80]}")

    # Short-term: only recent unconsolidated notes
    for note in get_recent_unconsolidated(brain, max_recent_notes):
        lines.append(f"  📝 ({note['id']}) {note['summary'][:60]}")

    return "\n".join(lines)
```

---

## 6. Workflow Integration

### 6.1 Updated `second_brain` Workflow (4+1 steps)

```
Standard heartbeat:
  1. status_check
  2. capture        → creates atomic note
  3. connect        → links related notes
  4. review         → summarizes cycle

Consolidation heartbeat (every Nth cycle):
  1. status_check
  2. capture
  3. connect
  4. consolidate    → promotes notes to wiki pages  ← NEW
  5. review

Lint heartbeat (every Mth cycle):
  1. status_check
  2. capture
  3. connect
  4. lint           → audits wiki quality            ← NEW
  5. review
```

### 6.2 Persona Configuration

Personas can control consolidation and lint behavior via `mcp.json`:

```jsonc
{
  "personas": {
    "researcher": {
      "workflow": "second_brain",
      "consolidation": {
        "interval": 5,     // every 5 heartbeats
        "threshold": 10    // or when 10+ notes pending
      },
      "lint": {
        "interval": 20     // every 20 heartbeats
      }
    }
  }
}
```

---

## 7. Migration Path

This design is **additive** — no existing functionality breaks:

1. **Phase 1**: Add `pages`, `lint_log`, `last_consolidation`, `last_lint`
   fields to `BrainState`. Existing `brain.json` files load fine (missing
   fields get defaults).

2. **Phase 2**: Add `WikiPage` dataclass and helper functions
   (`add_page`, `update_page`, `get_unconsolidated_notes`, etc.).

3. **Phase 3**: Add consolidation step to workflow. Existing notes remain as-is
   until the first consolidation run promotes them.

4. **Phase 4**: Add lint step. Update `build_brain_summary()` to prioritize
   wiki pages over raw notes.

5. **Phase 5**: Add persona-level configuration for consolidation/lint intervals.

---

## 8. Benefits Over Current Design

| Problem (today) | Solution (tiered) |
|------------------|-------------------|
| Context window bloat — all notes injected | Wiki summaries are compact; old notes archived |
| No deduplication — similar notes pile up | Consolidation merges duplicates into pages |
| Knowledge loss — old notes pushed out of summary | Promotion preserves everything important in wiki |
| No error correction — bad notes persist | Consolidation is a natural correction point; lint catches compounding errors |
| Flat retrieval — recency only | Wiki pages organized by topic; both recency and relevance available |
| Unbounded growth — JSON file grows forever | Archiving consumed notes caps active note count |

---

## 9. Comparison to Alternative Approaches

| Feature | NATLClaw (tiered) | MemGPT / Letta | Karpathy Wiki | Zep |
|---------|-------------------|-----------------|---------------|-----|
| Short-term memory | Atomic notes | Recall buffer | N/A (no short-term) | Conversation buffer |
| Long-term memory | Wiki pages | Archival (vector DB) | Markdown wiki | Enriched memory store |
| Consolidation | Periodic LLM step | Agent self-manages | Manual ingest | Automatic summarization |
| Working memory | Prompt injection | Core memory (editable system prompt) | Schema + index | Session context |
| Error correction | Lint step | Agent self-editing | Manual lint prompt | Temporal decay |
| Storage | JSON file | SQLite / Postgres + vectors | Markdown files | Postgres + pgvector |
| Vector search | No | Yes | No | Yes |
| Human intervention | None (autonomous) | Minimal | Required (ingest, lint, query) | None |

---

## 10. Future Enhancements

- **Semantic retrieval**: Embed wiki pages and notes with a local model;
  retrieve by similarity instead of recency. ChromaDB or `sqlite-vss` as
  storage backend.
- **Memory decay**: Add a relevance score to wiki pages that decreases over
  time unless accessed or updated. Use this to prioritize which pages appear
  in the prompt summary.
- **Self-modifying core memory**: Allow the agent to update its own persona
  instructions based on accumulated wiki knowledge (à la MemGPT's core
  memory editing).
- **Multi-agent consolidation**: Different personas contribute notes; a
  dedicated "librarian" persona runs consolidation across all of them.
- **Version control**: Git-commit wiki pages after each consolidation so
  changes can be reviewed, diffed, and rolled back.
