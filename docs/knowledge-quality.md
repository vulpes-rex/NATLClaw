# NATLClaw — Knowledge Quality & Schema Design

Five complementary features that harden the second brain's knowledge quality,
traceability, and self-governance. Each is designed to work independently but
compounds in value when combined.

1. **Lint / Health-Check Step** — periodic automated audit of brain contents
2. **Source Citation Tracking** — every note traces back to the heartbeat and
   prompt that created it
3. **Query Output Filing** — useful agent analyses are stored back as notes
4. **BRAIN.md Schema File** — per-persona rules governing how knowledge is
   organized
5. **HEARTBEAT.md Schema File** — per-persona rules governing how each cycle
   is executed, priorities, and adaptive behavior

---

## Current Enforced Quality Gates

The runtime lint currently enforces these observer-oriented checks:

- `missing_citation`: workspace observer notes must include concrete evidence
  (commit hash and/or file paths).
- `tag_quality`: active notes must include at least one non-generic tag.
- `stale_pattern`: older repeated "same files touched" observer notes are
  flagged for cleanup/consolidation.

These checks are in `second_brain.lint_brain()` and are surfaced through
`brain lint` and heartbeat health context.

---

## 1. Lint / Health-Check Step

### 1.1 Problem

Karpathy identifies error compounding as the **#1 risk** of AI-maintained
knowledge bases: the AI writes a subtle mistake → the next query builds on
it → the answer gets filed back → now two entries reinforce the same error.

NATLClaw currently has no mechanism to detect or correct this. `learning.py`
catches errors in individual agent *responses* (signal-word detection), but
never audits the *accumulated knowledge* for internal consistency.

### 1.2 Design

A **lint step** runs periodically (less often than capture — e.g., every
20 heartbeats or when explicitly triggered). It audits both atomic notes and
wiki pages.

```
                        Regular heartbeats
                   ┌─────┬─────┬─────┬─────┐
Heartbeat:         │  1  │  2  │  3  │ ... │  20
                   └─────┴─────┴─────┴─────┘───▶  LINT
                                                    │
                                    ┌───────────────┘
                                    ▼
                          ┌─────────────────┐
                          │  Audit checks:  │
                          │  • contradictions│
                          │  • stale content │
                          │  • missing cites │
                          │  • duplicates    │
                          │  • orphan notes  │
                          │  • suspect claims│
                          └────────┬────────┘
                                   ▼
                          ┌─────────────────┐
                          │  Auto-remediate  │
                          │  or flag for     │
                          │  human review    │
                          └─────────────────┘
```

### 1.3 What the Lint Step Checks

| # | Check | Scope | Severity | Auto-fix? |
|---|-------|-------|----------|-----------|
| L1 | **Contradictions** | Cross-note / cross-page — two entries make opposing claims | High | No — flag for next heartbeat to resolve |
| L2 | **Stale content** | Notes or pages not updated/accessed in N heartbeats | Medium | Archive stale notes; flag stale pages |
| L3 | **Missing citations** | Wiki page sections with no `[Source: nXXXX]` reference | Medium | Flag — consolidation step should fix on next pass |
| L4 | **Duplicate content** | Notes or pages with >80% content overlap | Medium | Yes — merge into single entry, archive duplicate |
| L5 | **Orphan notes** | Notes with `category != "archive"` older than M heartbeats that were never consolidated or connected | Low | Force-consolidate or archive |
| L6 | **Suspect claims** | Assertions that the LLM itself rates as low-confidence or unsupported | Low | Flag — add `[UNVERIFIED]` marker |
| L7 | **Connection rot** | Connections where one or both notes have been archived | Low | Yes — prune dead connections |

### 1.4 Triggering Strategy

Configurable per persona in `mcp.json`:

```jsonc
{
  "lint": {
    "interval": 20,              // run every 20 heartbeats
    "minNotesSinceLast": 5,      // skip lint if fewer than 5 new notes since last
    "autoRemediate": ["L4", "L5", "L7"],  // auto-fix these; flag the rest
    "enabled": true
  }
}
```

Logic in the workflow dispatcher:

```python
def should_lint(brain: BrainState, state: AgentState, config: LintConfig) -> bool:
    if not config.enabled:
        return False
    if state.execution_count % config.interval != 0:
        return False
    # Don't waste a lint pass if nothing changed
    notes_since = count_notes_since(brain, brain.last_lint)
    return notes_since >= config.min_notes_since_last
```

### 1.5 Lint Prompt (two-pass)

**Pass 1 — Detection** (cheap, can use smaller model):

```
You are auditing a knowledge base for quality issues.

WIKI PAGES:
{pages_with_content}

RECENT NOTES (not yet consolidated):
{unconsolidated_notes}

Check for:
1. Contradictions: two entries that make opposing claims
2. Stale entries: notes/pages not updated since heartbeat #{stale_threshold}
3. Missing citations: page sections without [Source: nXXXX] references
4. Duplicates: entries with very similar content
5. Suspect claims: assertions that seem unsupported or hallucinated

Return JSON:
{
  "issues": [
    {
      "id": "lint_001",
      "type": "contradiction | stale | missing_citation | duplicate | suspect_claim | orphan",
      "targets": ["page_id or note_id", "..."],
      "description": "what the issue is",
      "confidence": 0.0-1.0,
      "suggested_action": "merge | archive | flag | update | prune"
    }
  ],
  "clean": true | false
}
```

**Pass 2 — Remediation** (only if issues found, and only for auto-fixable types):

```
The following quality issues were detected in the knowledge base.
Apply the fixes described. For each fix, return the updated entry.

ISSUES TO FIX:
{auto_remediatable_issues}

ENTRIES TO MODIFY:
{target_entries}

Return JSON:
{
  "fixes": [
    {
      "issue_id": "lint_001",
      "action_taken": "merged notes n0005 and n0007 into n0005",
      "updated_entry": { ... }
    }
  ],
  "flagged_for_review": ["lint_003", "lint_005"]
}
```

### 1.6 Data Model Additions

```python
@dataclass
class LintIssue:
    id: str                    # "lint_001"
    type: str                  # "contradiction" | "stale" | "duplicate" | ...
    targets: list[str]         # note or page IDs involved
    description: str
    confidence: float          # 0.0–1.0
    suggested_action: str      # "merge" | "archive" | "flag" | "update"
    status: str = "open"       # "open" | "resolved" | "dismissed"
    resolved_at: str | None = None

# Added to BrainState:
lint_log: list[dict]           # [{timestamp, issues: [...], clean: bool}]
last_lint: str | None
```

### 1.7 Workflow Integration

```
second_brain workflow with lint:

  [status] → [capture] → [connect] → [lint?] → [consolidate?] → [review]
                                         │              │
                                  only if should_lint   only if should_consolidate
```

Lint runs *before* consolidation so that detected issues can be fixed before
new wiki pages are created/updated. This prevents consolidating bad data.

### 1.8 Review Log Entry

Each lint run produces a log entry:

```json
{
  "timestamp": "2026-04-07T14:30:00Z",
  "heartbeat": 40,
  "issues_found": 3,
  "auto_fixed": 2,
  "flagged": 1,
  "clean": false,
  "details": [
    {"id": "lint_012", "type": "duplicate", "action": "merged", "targets": ["n0023", "n0025"]},
    {"id": "lint_013", "type": "stale", "action": "archived", "targets": ["n0004"]},
    {"id": "lint_014", "type": "contradiction", "action": "flagged", "targets": ["p_deployment", "p_testing"]}
  ]
}
```

---

## 2. Source Citation Tracking

### 2.1 Problem

When a note contains incorrect information, there is currently no way to trace
*where* it came from — which heartbeat created it, which prompt elicited it,
or which agent response contained the original claim. This makes error
compounding invisible and impossible to debug.

### 2.2 Design

Every note and wiki page records a **provenance chain** — the full lineage of
how it was created and modified.

### 2.3 Note Provenance

Add a `provenance` field to every note:

```python
@dataclass
class Note:
    id: str
    content: str
    summary: str = ""
    source: str = "agent"
    tags: list[str] = field(default_factory=list)
    category: str = "resources"
    connections: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    # NEW — provenance tracking
    provenance: dict = field(default_factory=dict)
    #   {
    #     "heartbeat":   42,              ← which heartbeat cycle
    #     "step":        "capture",       ← which workflow step
    #     "persona":     "researcher",    ← which persona was active
    #     "prompt_hash": "a1b2c3d4",      ← short hash of the prompt that produced this
    #     "model":       "claude-sonnet-4", ← which model generated it
    #   }
```

### 2.4 Wiki Page Provenance

For wiki pages, provenance is more complex because pages are *updated* over
multiple consolidation cycles:

```python
@dataclass
class WikiPage:
    id: str
    title: str
    content: str
    sources: list[str]       # note IDs that were consolidated into this page
    tags: list[str]
    created_at: str
    updated_at: str

    # NEW — change log
    change_log: list[dict] = field(default_factory=list)
    #   [{
    #     "timestamp":   "2026-04-07T14:30:00Z",
    #     "heartbeat":   45,
    #     "action":      "created" | "updated" | "lint_fix",
    #     "notes_added": ["n0012", "n0015"],
    #     "summary":     "Added deployment pipeline insights from notes n0012, n0015"
    #   }]
```

### 2.5 How Provenance Is Populated

The `_store_capture()` helper in `workflow.py` already has access to all the
context needed. Changes:

```python
# In workflow.py, when storing a capture:
note_id = add_note(
    brain,
    content=data.get("content", raw[:300]),
    summary=data.get("topic", ""),
    tags=data.get("tags", []),
    category=data.get("category", "resources"),
    source="heartbeat",
    provenance={                          # ← NEW
        "heartbeat": state.execution_count,
        "step": step_name,
        "persona": persona.name,
        "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest()[:8],
        "model": config.model,
    },
)
```

The `add_note()` function gains an optional `provenance` parameter:

```python
def add_note(brain, content, *, summary="", source="agent",
             tags=None, category="resources",
             provenance=None) -> str:          # ← NEW param
    ...
    brain.notes[note_id] = asdict(Note(
        ...
        provenance=provenance or {},
    ))
```

### 2.6 Provenance in Prompts

When the lint step detects an issue, it can cite provenance to help debug:

```
Issue: Contradiction between n0023 and n0041
  n0023 (heartbeat #12, researcher, capture step):
    "Deployment requires manual approval"
  n0041 (heartbeat #28, devops_engineer, task step):
    "Deployment is fully automated"
```

When `build_brain_summary()` surfaces notes, it now includes the source:

```
  - (n0023) Deployment requires manual approval [heartbeat #12, researcher]
```

### 2.7 Prompt Hash Traceability

The `prompt_hash` is a short SHA-256 of the full prompt text. This doesn't
store the prompt (which could be very long) but allows matching a note back
to the exact prompt in `state.execution_history`:

```python
# To trace a note back to its source conversation:
target_hash = note["provenance"]["prompt_hash"]
for entry in state.execution_history:
    if hashlib.sha256(entry["prompt"].encode()).hexdigest()[:8] == target_hash:
        print(f"Found source: {entry}")
```

This is lightweight and doesn't increase storage significantly.

---

## 3. Query Output Filing

### 3.1 Problem

When the agent generates a valuable analysis — a comparison, a synthesis, a
decision framework — that output currently evaporates after the heartbeat.
The `freeform` workflow partially addresses this by distilling an insight from
the task result, but:

- Only the distilled 1-2 sentence insight is saved, not the full analysis
- The `second_brain` workflow doesn't file outputs at all — only the capture
  step's structured note is stored
- The `steps` workflow only files outputs for steps with `storeToBrain: true`

### 3.2 Design

Add a **quality gate** that evaluates every step output and decides whether
it's worth filing back into the brain. This models Karpathy's advice:
"good answers should be filed back into the wiki."

### 3.3 Quality Gate Logic

Not every step output deserves to be a note. A status check that says
"everything is normal" isn't worth storing. A detailed analysis of
deployment patterns absolutely is.

Two approaches (can be combined):

**Approach A — Heuristic (no extra LLM call)**

```python
def is_worth_filing(step_name: str, response: str) -> bool:
    """Heuristic check: is this output substantial enough to file?"""
    # Skip short/trivial responses
    if len(response) < 200:
        return False

    # Skip status checks and reviews (already logged elsewhere)
    if step_name in ("status_check", "review"):
        return False

    # Look for signals of substantive content
    substance_signals = (
        "because", "therefore", "compared to", "in contrast",
        "key finding", "recommendation", "pattern", "trade-off",
        "advantage", "disadvantage", "suggests that",
    )
    signal_count = sum(1 for s in substance_signals if s in response.lower())
    return signal_count >= 2
```

**Approach B — LLM judge (costs one extra call per step)**

```python
gate_prompt = f"""
Rate this output on a scale of 1-5 for knowledge value:
1 = trivial status/confirmation
2 = minor observation
3 = useful but incremental
4 = significant insight or analysis
5 = breakthrough understanding or novel synthesis

Output:
{response[:500]}

Return ONLY a JSON object: {{"score": N, "reason": "one sentence"}}
"""
# File if score >= 3
```

**Recommended: Approach A by default, Approach B opt-in per persona.**

### 3.4 What Gets Filed

When an output passes the quality gate, the system creates a note with:

```python
note_id = add_note(
    brain,
    content=response[:1000],           # fuller text than the 2-sentence distill
    summary=f"Analysis from {step_name}",
    tags=["auto-filed", step_name],
    category="resources",
    source="query_output",             # distinguishes from heartbeat captures
    provenance={
        "heartbeat": state.execution_count,
        "step": step_name,
        "persona": persona.name,
        "filing_reason": "quality_gate_heuristic",
        "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest()[:8],
        "model": config.model,
    },
)
```

### 3.5 Filing vs. Capture: How They Differ

| Aspect | Capture step (existing) | Query output filing (new) |
|--------|------------------------|--------------------------|
| **Trigger** | Dedicated workflow step | Any step that passes quality gate |
| **Content** | LLM writes a structured note on demand | The actual step output (analysis, finding) |
| **Format** | JSON with topic/content/tags | Longer-form text, auto-tagged |
| **Volume** | 1 per heartbeat (guaranteed) | 0–N per heartbeat (only if quality threshold met) |
| **Source field** | `"heartbeat"` | `"query_output"` |

### 3.6 Compounding Loop

This creates the compounding loop Karpathy describes:

```
Heartbeat N:
  [task step] → produces analysis of deployment patterns
                    │
                    ▼  (passes quality gate)
               filed as note n0042 [auto-filed, task]
                    │
Heartbeat N+1:     │
  [capture step] ←─┘  sees n0042 in brain summary
                       builds on it, adds nuance
                    │
                    ▼
               note n0043 references n0042
                    │
Heartbeat N+5:     │
  [consolidation] ─┘  merges n0042 + n0043 into wiki page
                       "Deployment Patterns"
```

### 3.7 Deduplication Guard

Risk: filing outputs could create near-duplicates of the capture step's note.
Mitigation:

```python
def is_duplicate_of_recent(brain: BrainState, content: str, lookback: int = 5) -> bool:
    """Check if content is too similar to any of the last N notes."""
    recent = get_recent_notes(brain, lookback)
    for note in recent:
        existing = note.get("content", "")
        # Simple overlap check (upgrade to cosine similarity later)
        overlap = len(set(content.split()) & set(existing.split()))
        total = max(len(set(content.split())), 1)
        if overlap / total > 0.7:
            return True
    return False
```

### 3.8 Configuration

```jsonc
{
  "personas": {
    "researcher": {
      "queryFiling": {
        "enabled": true,
        "method": "heuristic",        // "heuristic" | "llm_judge" | "both"
        "minLength": 200,              // minimum response length to consider
        "excludeSteps": ["status_check", "review"],
        "maxPerHeartbeat": 2           // cap to prevent flooding
      }
    }
  }
}
```

---

## 4. BRAIN.md Schema File

### 4.1 Problem

Currently, knowledge organization rules are implicit — split across persona
instruction files (`instructions.md`), workflow code (`workflow.py`), and the
JSON structure of `BrainState`. There is no single document that tells the
agent *how* to organize knowledge for a given domain.

Karpathy's system has `CLAUDE.md` — a schema file that defines:
- What the knowledge base covers
- How to categorize and tag entries
- What citation format to use
- When and how to cross-reference
- Quality standards for entries

NATLClaw needs an equivalent.

### 4.2 Design

Each persona can have an optional **`BRAIN.md`** file that describes the
knowledge management rules for that persona's domain. The agent reads this
file every heartbeat as part of its enriched instructions.

### 4.3 File Location

```
personas/
├── default/
│   ├── instructions.md       ← persona role and behavior
│   └── BRAIN.md              ← knowledge organization rules (NEW)
├── researcher/
│   ├── instructions.md
│   └── BRAIN.md
├── devops_engineer/
│   ├── instructions.md
│   └── BRAIN.md              ← domain-specific org rules
│   └── tools.py
```

`BRAIN.md` is **optional**. If absent, the system uses a built-in default
schema. If present, it is prepended to the brain summary in the agent's
prompt context.

### 4.4 Schema Structure

A `BRAIN.md` file has these sections:

```markdown
# Knowledge Schema: {Persona Name}

## Domain
What this knowledge base covers. Defines boundaries so the agent
doesn't capture irrelevant information.

## Categories
How to use the PARA categories for this domain.
- **projects**: active work with deadlines
- **areas**: ongoing responsibilities
- **resources**: reference material
- **archive**: completed/outdated items

## Tags
Approved tag vocabulary. Keeps tags consistent instead of the agent
inventing new ones every heartbeat.

## Citation Rules
How to cite sources. What format to use for [Source: nXXXX] references.

## Quality Standards
What makes a good note. Minimum content length, required fields,
confidence tagging.

## Connection Rules
What kinds of connections to look for. How to judge whether two
notes are meaningfully related.

## Wiki Page Guidelines (for tiered memory)
How to structure long-term wiki pages. Section format, update rules,
what goes in vs. stays as an atomic note.
```

### 4.5 Example: Default BRAIN.md

```markdown
# Knowledge Schema: Default

## Domain
General AI agents, autonomous systems, and knowledge management.
Capture insights about architecture patterns, tool ecosystems,
best practices, and emerging research.

## Categories
- **projects**: specific build tasks with deliverables
  (e.g., "implement tiered memory")
- **areas**: ongoing concerns
  (e.g., "agent reliability", "prompt engineering")
- **resources**: reference knowledge
  (e.g., "comparison of memory frameworks")
- **archive**: superseded or completed items

## Tags
Use lowercase, hyphenated tags from this vocabulary:
  ai-agents, memory, architecture, patterns, tools,
  prompt-engineering, knowledge-management, llm,
  reliability, testing, personas, workflows

New tags are allowed but should be discussed in the review step
before first use. Avoid synonyms of existing tags.

## Citation Rules
Every note must include provenance metadata (auto-populated).
Wiki page sections must reference contributing note IDs as:
  [Source: n0012, n0015]

When a claim spans multiple sources, cite all of them.
When a claim is the agent's own synthesis (not from a source),
mark it as [Synthesis] so lint can verify it later.

## Quality Standards
- Notes: minimum 1 sentence, maximum 3 sentences
- Wiki pages: minimum 3 paragraphs with section headers
- Every note needs at least 1 tag
- Claims must be specific — avoid "many people think..."
- Flag confidence: [HIGH], [MEDIUM], [LOW] for uncertain claims

## Connection Rules
Look for these relationship types:
- **supports**: one note provides evidence for another
- **contradicts**: one note challenges another's claim
- **extends**: one note adds nuance or detail to another
- **similar**: overlapping topic, potential deduplication
Only create connections you can explain in one sentence.

## Wiki Page Guidelines
- One page per distinct topic (not per heartbeat)
- Structure: Overview → Key Points → Details → Open Questions
- Update existing pages before creating new ones
- Cite all contributing notes in each section
- Mark any section that hasn't been updated in 10+ heartbeats
  with [STALE] so lint can flag it
```

### 4.6 Example: DevOps Engineer BRAIN.md

```markdown
# Knowledge Schema: DevOps Engineer

## Domain
Infrastructure, CI/CD pipelines, container orchestration, monitoring,
and deployment automation. Out of scope: frontend development,
business strategy.

## Categories
- **projects**: infra builds — "migrate to k8s", "setup monitoring"
- **areas**: SLA tracking, incident response, cost optimization
- **resources**: tooling comparisons, runbooks, config patterns
- **archive**: decommissioned infra, resolved incidents

## Tags
  docker, kubernetes, ci-cd, monitoring, terraform,
  cloud-aws, cloud-azure, cloud-gcp, networking,
  security, cost, incident, runbook, pipeline

## Citation Rules
Same as default. Additionally, for tool-generated outputs
(e.g., from Docker MCP server), cite the tool:
  [Source: n0015, tool: docker-mcp]

## Quality Standards
- Runbook entries must include: trigger, steps, rollback
- Architecture notes must specify: components, connections, scale limits
- Flag any infra claim older than 30 heartbeats as potentially stale
  (infrastructure changes fast)

## Connection Rules
Primary relationships for devops:
- **depends_on**: service A requires service B
- **alternative_to**: two approaches to the same problem
- **incident_related**: connected to an incident or outage
- **runbook_for**: this note describes how to handle that system

## Wiki Page Guidelines
- Organize by system/service, not by date
- Every wiki page should have a "Last verified" timestamp
- Runbook pages: numbered steps, copy-pasteable commands
- Architecture pages: component list, dependency graph description
```

### 4.7 Loading the Schema

The `persona_loader.py` resolves `BRAIN.md` the same way it resolves
`instructions.md` — by looking in the persona's directory:

```python
@dataclass
class Persona:
    name: str
    description: str
    instructions: str
    heartbeat_task: str = ""
    tools: list[Callable[..., Any]] = field(default_factory=list)
    mcp_servers: dict[str, dict] | None = None
    workflow: str = "second_brain"
    steps: list[dict] | None = None
    stepwise: bool = False

    # NEW
    brain_schema: str = ""   # contents of BRAIN.md, or default schema
```

In `persona_loader.py`:

```python
def _load_brain_schema(persona_dir: str) -> str:
    """Load BRAIN.md from the persona directory, or return default."""
    brain_path = os.path.join(persona_dir, "BRAIN.md")
    if os.path.isfile(brain_path):
        with open(brain_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    return _DEFAULT_BRAIN_SCHEMA  # built-in fallback
```

### 4.8 Injection into Prompt Context

The schema is injected between persona instructions and the brain summary:

```python
# In scheduler.py, when building enriched instructions:
enriched_instructions = (
    f"{base_instructions}\n\n"
    f"== KNOWLEDGE SCHEMA ==\n{persona.brain_schema}\n\n"   # ← NEW
    f"{context_block}\n\n"
    f"{brain_block}"
)
```

This means every LLM call in every workflow step sees the schema. The agent
knows how to categorize, tag, cite, and structure its knowledge output
consistently.

### 4.9 Schema Validation at Lint Time

The lint step can reference the schema to check compliance:

```
You are auditing a knowledge base. The knowledge schema below defines
the rules for this domain.

SCHEMA:
{brain_schema}

ENTRIES TO AUDIT:
{notes_and_pages}

Check for:
1. Notes using tags not in the approved vocabulary
2. Notes missing required metadata (provenance, tags)
3. Wiki pages not following the section structure
4. Claims missing confidence markers where the schema requires them
5. Out-of-scope content that doesn't belong in this domain

Return JSON with issues found.
```

This closes the loop: **BRAIN.md defines the rules → the agent follows them
during capture → lint verifies compliance → violations are flagged or fixed.**

---

## 5. How Features 1–4 Interact

```
                    BRAIN.md Schema
                    ┌─────────────┐
                    │  Defines:   │
                    │  • tags     │
                    │  • format   │    governs
                    │  • quality  │──────────────┐
                    │  • citation │              │
                    └─────────────┘              ▼
                                        ┌───────────────┐
      ┌────────────────────────────────▶│  CAPTURE STEP │
      │  quality gate decides           │  + provenance │──── creates notes with
      │  what else gets filed           │  + citation   │     full traceability
      │                                 └───────┬───────┘
      │                                         │
┌─────┴──────────┐                              ▼
│ QUERY OUTPUT   │                    ┌─────────────────┐
│ FILING         │                    │  CONSOLIDATION  │
│                │                    │  (notes → wiki) │
│ Files valuable │                    │  cites sources  │
│ step outputs ──┘                    └────────┬────────┘
│ back to brain                                │
└────────────────┘                             ▼
                                      ┌─────────────────┐
                                      │  LINT STEP      │
                                      │                 │
                                      │  Checks schema  │
                                      │  compliance     │
                                      │  + contradictions│
                                      │  + staleness    │
                                      │  + citations    │
                                      └─────────────────┘
```

**The lifecycle of a piece of knowledge:**

1. **HEARTBEAT.md** determines: what should this cycle focus on?
2. Agent produces output during a workflow step
3. **Query filing** evaluates: is this output worth storing? If yes → create note
4. **Source citation** records: which heartbeat, step, persona, model, prompt
5. **BRAIN.md** governs: how to tag, categorize, and format the note
6. **Consolidation** (from tiered-memory design): promotes notes into wiki pages,
   carrying provenance forward
7. **Lint** audits: does this entry comply with the schema? Is it contradicted?
   Stale? Missing citations?
8. Lint issues are either auto-fixed or flagged for the next heartbeat

---

## 6. Configuration Summary

All five features are controlled per persona in `mcp.json`:

```jsonc
{
  "personas": {
    "researcher": {
      "description": "Research analyst",
      "instructions": "personas/researcher/instructions.md",
      "workflow": "second_brain",
      "heartbeatTask": "...",

      "brainSchema": "personas/researcher/BRAIN.md",
      "heartbeatStrategy": {
        "file": "personas/researcher/HEARTBEAT.md",
        "selfUpdate": false
      },

      "consolidation": {
        "interval": 5,
        "threshold": 10
      },

      "lint": {
        "enabled": true,
        "interval": 20,
        "minNotesSinceLast": 5,
        "autoRemediate": ["duplicate", "orphan", "connection_rot"]
      },

      "queryFiling": {
        "enabled": true,
        "method": "heuristic",
        "minLength": 200,
        "excludeSteps": ["status_check", "review"],
        "maxPerHeartbeat": 2
      }
    }
  }
}
```

---

## 7. HEARTBEAT.md Schema File

### 7.1 Problem

The heartbeat cycle is currently governed by a scattered mix of:

| Piece | Where it lives | What it controls |
|-------|---------------|-----------------|
| `persona.workflow` | `mcp.json` | Which mode: `second_brain`, `freeform`, `steps` |
| `persona.heartbeat_task` | `mcp.json` | A single-line task description |
| `persona.steps` | `mcp.json` | Step-by-step definitions (for `steps` mode) |
| Hard-coded logic | `workflow.py` | The 4-step sequence, prompt templates |
| `heartbeat_interval_sec` | `.env` / config | How often to run |

None of these express **cycle strategy** — the higher-level thinking about
how each heartbeat should be conducted. Questions like:

- Should this cycle explore **breadth** (new topics) or **depth** (existing ones)?
- How should priorities shift as the brain matures (10 notes vs. 500 notes)?
- When should the agent skip capture and focus on connections or consolidation?
- What triggers escalation to the human?
- What phase is the project in (discovery / building / maintenance / winding down)?
- How should consecutive heartbeats relate to each other?
- What are the token/time budgets per step?

The `instructions.md` says **who** the agent is. The `BRAIN.md` says **how**
to organize knowledge. The `heartbeatTask` says **what** to do. But nobody
says **how to think about doing it** — the strategic layer.

### 7.2 Design

A **`HEARTBEAT.md`** file in each persona directory defines the execution
strategy for the heartbeat cycle. Like `BRAIN.md`, it's optional — the system
uses a built-in default if absent.

### 7.3 The Three Schema Files

```
personas/researcher/
├── instructions.md       ← WHO:  identity, role, guidelines
├── BRAIN.md              ← WHAT: knowledge organization rules
└── HEARTBEAT.md          ← HOW:  cycle execution strategy
```

Together they form a complete governance stack:

```
┌─────────────────────────────────────────────────────┐
│                  PROMPT CONTEXT                      │
│                                                     │
│  ┌──────────────────┐                               │
│  │  instructions.md │  WHO you are                  │
│  │  (persona role)  │  "You are a research analyst" │
│  └────────┬─────────┘                               │
│           │                                         │
│  ┌────────▼─────────┐                               │
│  │  HEARTBEAT.md    │  HOW to execute               │
│  │  (cycle strategy)│  "focus on depth if >50 notes"│
│  └────────┬─────────┘                               │
│           │                                         │
│  ┌────────▼─────────┐                               │
│  │  BRAIN.md        │  WHAT to organize             │
│  │  (knowledge mgmt)│  "use these tags, cite sources│
│  └────────┬─────────┘                               │
│           │                                         │
│  ┌────────▼─────────┐                               │
│  │  brain summary   │  CONTEXT from past cycles     │
│  │  + agent state   │                               │
│  └──────────────────┘                               │
└─────────────────────────────────────────────────────┘
```

### 7.4 Schema Structure

```markdown
# Heartbeat Strategy: {Persona Name}

## Phase
What phase is this agent currently operating in? This can be updated
over time as the project evolves.

## Cycle Focus Rules
How the agent should decide what to do each heartbeat, based on
brain state and history.

## Priority Stack
When multiple things could be done, what comes first?

## Adaptive Behavior
How behavior changes based on brain maturity, error rate, or
external signals.

## Escalation Rules
When should the agent flag something for human attention instead
of acting autonomously?

## Cycle Continuity
How each heartbeat should relate to the previous one.

## Resource Constraints
Token budgets, time limits, cost awareness.

## Goal Evolution
How the heartbeat task should change over time as the brain matures.
```

### 7.5 Example: Default HEARTBEAT.md

```markdown
# Heartbeat Strategy: Default

## Phase
**Discovery** — the brain is young. Focus on breadth: cover as many
sub-topics as possible before diving deep into any single one.

Update this to **Deepening** when the brain has 50+ notes and 3+
wiki pages. Update to **Maintenance** when the brain has 200+ notes
and the primary domain feels well-covered.

## Cycle Focus Rules
Choose ONE focus per heartbeat based on brain state:

| Brain state | Focus this cycle |
|-------------|-----------------|
| < 10 notes | Capture a foundational concept in the domain |
| 10–50 notes, few connections | Prioritize connecting existing notes |
| 50+ notes, < 3 wiki pages | Trigger consolidation — promote to long-term |
| 50+ notes, 3+ wiki pages | Alternate: deepen an existing page one cycle, explore a new angle the next |
| Lint flagged issues | Resolve the highest-severity lint issue before capturing new knowledge |

## Priority Stack
When in doubt, prioritize in this order:
1. Fix flagged lint issues (errors compound if left)
2. Consolidate pending notes (prevent context bloat)
3. Deepen a weak wiki page (strengthen existing knowledge)
4. Capture something new (expand coverage)
5. Explore connections (find non-obvious relationships)

## Adaptive Behavior
- **High error rate** (3+ error lessons in last 10 heartbeats):
  Slow down. Focus on reviewing and correcting existing knowledge
  before adding more.
- **Repetitive captures** (last 3 notes have overlapping tags):
  The agent is stuck in a loop. Force it to explore a different
  category or sub-topic.
- **Empty review** (review step said "nothing new"):
  Skip the next capture step and spend the cycle on connections
  or consolidation instead.

## Escalation Rules
Flag for human attention (log at WARNING level, add to review queue):
- A contradiction between two high-confidence wiki pages
- The agent has captured the same topic 3+ times without resolution
- A tool call fails 3 consecutive heartbeats
- The brain hasn't grown (0 new notes) in 5+ heartbeats
- A lint issue has been flagged but unresolved for 10+ heartbeats

Do NOT escalate:
- Normal operational status
- Minor stale-note warnings
- Single tool call failures (retry first)

## Cycle Continuity
Each heartbeat should begin by reading the previous cycle's review
summary (already injected via agent state). Use it to:
- Avoid repeating what was just captured
- Follow up on "next areas to explore" suggestions
- Continue multi-cycle investigations

If the previous review suggested a specific topic, prioritize it
unless a higher-priority item (lint fix, consolidation) takes precedence.

## Resource Constraints
- **Status check**: keep to 2-3 sentences (don't waste tokens on verbose status)
- **Capture**: 2-3 sentence content maximum per note
- **Connect**: only attempt if there are 2+ recent notes; skip otherwise
- **Review**: 2-3 sentences maximum; focus on actionable next steps
- If total heartbeat exceeds 4 LLM calls, skip the connect step

## Goal Evolution
- **Weeks 1-2** (heartbeats 1–100 at 2min interval):
  Cast a wide net. Cover the major sub-topics of the domain.
  Accept lower-quality notes — volume matters more than polish.
- **Weeks 3-4** (heartbeats 100–200):
  Shift to depth. Pick the 3 weakest wiki pages and systematically
  strengthen them. Start filing query outputs back.
- **Month 2+** (heartbeats 200+):
  Maintenance mode. Focus on connections, contradiction detection,
  and periodic review. New captures should be genuinely novel, not
  incremental.
```

### 7.6 Example: DevOps Engineer HEARTBEAT.md

```markdown
# Heartbeat Strategy: DevOps Engineer

## Phase
**Operational monitoring** — continuous. No end state. The
environment is always changing.

## Cycle Focus Rules
| Brain state | Focus this cycle |
|-------------|-----------------|
| Any | Always run a health check (container status, service health) |
| Anomaly detected | Investigate → capture finding → connect to past incidents |
| No anomaly | Check one infrastructure area not inspected in last 5 cycles |
| 10+ unconnected infra notes | Consolidate into a system-health wiki page |

## Priority Stack
1. Active incident / anomaly → investigate immediately
2. Stale runbook → update with current state
3. Routine health check → capture operational baseline
4. Knowledge gap → research an infra topic the brain lacks

## Adaptive Behavior
- **Incident detected**: Switch to rapid-fire mode — shorter interval
  between heartbeats if possible, focused investigation until resolved.
- **All healthy for 10+ cycles**: Expand scope — check a system or
  service not usually monitored. Look for blind spots.
- **Repeated same finding**: The environment hasn't changed. Skip
  capture and focus on enriching existing ops wiki pages.

## Escalation Rules
Flag for human attention:
- Any container in unhealthy/restarting state for 2+ heartbeats
- Disk usage > 85% detected
- A service that was healthy last cycle is now unreachable
- A critical finding that requires manual intervention

## Cycle Continuity
- If last cycle detected an anomaly, this cycle MUST follow up on it
  before doing anything else
- Track which systems were checked in `state.context.last_checked`
  and rotate through all systems over N heartbeats

## Resource Constraints
- Health checks via MCP tools are cheap — always run them
- Only call the LLM for analysis when something is abnormal
- Skip the capture step if the health check is clean and routine

## Goal Evolution
Goal doesn't evolve — operational monitoring is perpetual.
But the *depth* evolves:
- **Week 1**: Build baseline — what does healthy look like?
- **Week 2+**: Detect drift from baseline
- **Month 2+**: Correlate incidents, identify systemic patterns
```

### 7.7 Loading the Schema

Same pattern as `BRAIN.md` — resolved from the persona directory:

```python
@dataclass
class Persona:
    name: str
    description: str
    instructions: str
    heartbeat_task: str = ""
    tools: list[Callable[..., Any]] = field(default_factory=list)
    mcp_servers: dict[str, dict] | None = None
    workflow: str = "second_brain"
    steps: list[dict] | None = None
    stepwise: bool = False
    brain_schema: str = ""        # from BRAIN.md
    heartbeat_schema: str = ""    # from HEARTBEAT.md  ← NEW
```

```python
def _load_heartbeat_schema(persona_dir: str) -> str:
    """Load HEARTBEAT.md from the persona directory, or return default."""
    hb_path = os.path.join(persona_dir, "HEARTBEAT.md")
    if os.path.isfile(hb_path):
        with open(hb_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    return _DEFAULT_HEARTBEAT_SCHEMA
```

### 7.8 Injection into Prompt Context

The heartbeat schema sits between persona instructions and the brain schema,
forming a natural governance stack:

```python
# In scheduler.py:
enriched_instructions = (
    f"{base_instructions}\n\n"
    f"== HEARTBEAT STRATEGY ==\n{persona.heartbeat_schema}\n\n"  # ← NEW
    f"== KNOWLEDGE SCHEMA ==\n{persona.brain_schema}\n\n"
    f"{context_block}\n\n"
    f"{brain_block}"
)
```

### 7.9 How HEARTBEAT.md Interacts with Existing Config

HEARTBEAT.md **does not replace** `mcp.json` configuration. They operate at
different levels:

| Concern | Defined in | Example |
|---------|-----------|---------|
| Workflow mode | `mcp.json` → `workflow` | `"second_brain"` |
| Task description | `mcp.json` → `heartbeatTask` | `"Research one new insight..."` |
| Step definitions | `mcp.json` → `steps` | `[{name, prompt, storeToBrain}]` |
| Timing | `.env` → `HEARTBEAT_INTERVAL_SEC` | `120` |
| Lint/consolidation intervals | `mcp.json` → `lint` / `consolidation` | `{"interval": 20}` |
| **Cycle strategy** | `HEARTBEAT.md` | "If < 10 notes, focus breadth" |
| **Adaptive rules** | `HEARTBEAT.md` | "If 3+ errors, slow down and review" |
| **Priority stack** | `HEARTBEAT.md` | "Fix lint issues before capturing" |
| **Escalation criteria** | `HEARTBEAT.md` | "Flag unreachable services" |

`mcp.json` is **mechanical** — what mode, what interval, what steps.
`HEARTBEAT.md` is **strategic** — how to think, when to shift, what matters.

### 7.10 Making the Strategy Actionable

The agent sees the heartbeat schema in its prompt, but how does it *act* on
rules like "if < 10 notes, focus on breadth"? Two approaches:

**Approach A — Advisory (simpler)**

The schema is read-only context. The agent interprets it during the status
check step and adjusts its capture/connect behavior accordingly. This works
because the status step already produces a "status assessment" that feeds
into the capture step's prompt.

```
Status check prompt:
  "You are NATLClaw. Heartbeat #15.
   Brain has 8 notes, 2 connections, 0 wiki pages.

   == HEARTBEAT STRATEGY ==
   [full HEARTBEAT.md content]

   Given the strategy rules, what should this cycle focus on?
   Give a 2-3 sentence assessment including your chosen focus."
```

The agent reads the rules, sees "< 10 notes → capture a foundational concept,"
and its status output says "Brain is still young with only 8 notes. This cycle
should focus on capturing a foundational concept about X." That output feeds
the capture prompt, shaping its behavior.

**Approach B — Programmatic (more reliable)**

Parse some rules into code. For example, the priority stack and adaptive
behavior can be partially encoded:

```python
def select_cycle_focus(brain: BrainState, state: AgentState, config: HeartbeatConfig) -> str:
    """Determine this cycle's focus from heartbeat strategy rules."""
    # Check for unresolved lint issues
    open_issues = [i for i in brain.lint_log[-1].get("details", [])
                   if i.get("action") == "flagged"] if brain.lint_log else []
    if open_issues:
        return "resolve_lint"

    # Check note count thresholds
    unconsolidated = len(get_unconsolidated_notes(brain))
    if unconsolidated >= config.consolidation_threshold:
        return "consolidate"

    total = len(brain.notes)
    if total < 10:
        return "breadth_capture"
    elif total < 50 and len(brain.connections) < total * 0.3:
        return "connect"
    else:
        return "depth_or_explore"
```

**Recommended: Approach A by default (advisory), with Approach B for
well-defined thresholds like consolidation triggers.** The advisory approach
keeps the strategy human-readable in markdown rather than buried in Python.

### 7.11 Self-Updating Strategy

One powerful possibility: the agent can **propose updates** to its own
HEARTBEAT.md during the review step. For example:

```
Review prompt:
  "... Based on this cycle's results and the heartbeat strategy,
   should the phase, priority stack, or focus rules be updated?
   If yes, suggest the specific change as a markdown diff."
```

The agent might respond:

```
Phase should update from "Discovery" to "Deepening" —
the brain now has 55 notes and 4 wiki pages covering
the major sub-topics.
```

This update could be:
- **Auto-applied** (agent writes the file) — risky, MemGPT-style
- **Logged as a suggestion** in `review_log` for human approval — safer
- **Applied after N consecutive suggestions** — compromise

For the initial implementation, log as a suggestion. Self-modification
can be enabled per persona later:

```jsonc
{
  "personas": {
    "researcher": {
      "heartbeatStrategy": {
        "file": "personas/researcher/HEARTBEAT.md",
        "selfUpdate": false    // true = agent can modify its own strategy
      }
    }
  }
}
```

---

## 8. How All Five Features Interact

```
                    instructions.md
                    ┌──────────────┐
                    │  WHO: role,  │
                    │  identity    │
                    └──────┬───────┘
                           │
                    HEARTBEAT.md
                    ┌──────▼───────┐
                    │  HOW: cycle  │
                    │  strategy,   │    governs execution
                    │  priorities, │──────────────┐
                    │  adaptation  │              │
                    └──────┬───────┘              │
                           │                     │
                    BRAIN.md                     │
                    ┌──────▼───────┐             │
                    │  WHAT: tags, │             │
                    │  format,     │  governs    │
                    │  quality,    │─────────┐   │
                    │  citations   │         │   │
                    └──────────────┘         │   │
                                            ▼   ▼
                                    ┌───────────────┐
      ┌────────────────────────────▶│  CAPTURE STEP │
      │  quality gate decides       │  + provenance │──── creates notes with
      │  what else gets filed       │  + citation   │     full traceability
      │                             └───────┬───────┘
      │                                     │
┌─────┴──────────┐                          ▼
│ QUERY OUTPUT   │                ┌─────────────────┐
│ FILING         │                │  CONSOLIDATION  │
│                │                │  (notes → wiki) │
│ Files valuable │                │  cites sources  │
│ step outputs ──┘                └────────┬────────┘
│ back to brain                            │
└────────────────┘                         ▼
                                  ┌─────────────────┐
                                  │  LINT STEP      │
                                  │  Checks schema  │
                                  │  + strategy     │
                                  │  compliance     │
                                  └─────────────────┘
```

**The complete governance stack:**

| Layer | File | Purpose | Changes how often |
|-------|------|---------|------------------|
| Identity | `instructions.md` | Who the agent is | Rarely — set once per persona |
| Strategy | `HEARTBEAT.md` | How to execute cycles | Occasionally — as project phase changes |
| Knowledge rules | `BRAIN.md` | How to organize knowledge | Rarely — domain rules are stable |
| Mechanical config | `mcp.json` | Intervals, modes, tools | As needed — operational tuning |
| Accumulated knowledge | `brain.json` | What the agent knows | Every heartbeat — always growing |

---

## 9. Implementation Priority

| Feature | Complexity | Value | Dependencies | Suggested order |
|---------|-----------|-------|-------------|----------------|
| Source citation tracking | Low | High | None — can add to existing `add_note()` | **1st** |
| BRAIN.md schema file | Low | High | None — just a new file + prompt injection | **2nd** |
| HEARTBEAT.md schema file | Low | High | None — just a new file + prompt injection | **2nd** (parallel with BRAIN.md) |
| Query output filing | Medium | Medium | Source citations (for provenance) | **3rd** |
| Lint / health-check | High | High | Schema files (for compliance), citations (for tracing), tiered memory (for wiki pages) | **4th** |

Source citations, BRAIN.md, and HEARTBEAT.md are all low-effort, high-value
changes that can ship independently and in parallel. Query filing builds on
citations. Lint is the capstone that ties everything together.

---

## 10. Relationship to Other Design Docs

- **[tiered-memory.md](tiered-memory.md)** — Defines the two-tier memory model
  (atomic notes + wiki pages) and consolidation. The lint step documented here
  extends that design with quality auditing. Source citations provide the
  traceability that makes consolidation and lint trustworthy. HEARTBEAT.md's
  adaptive rules can trigger consolidation based on brain state.

- **[second-brain-research.md](second-brain-research.md)** — Surveys alternative
  solutions. The features in this document address specific gaps identified
  there: no error correction (→ lint), no traceability (→ citations),
  no knowledge compounding (→ query filing), no organizational governance
  (→ BRAIN.md + HEARTBEAT.md).

- **[spec.md](spec.md)** — Current technical spec. Will need updates to
  reflect new `Note` fields (provenance), new `Persona` fields (`brain_schema`,
  `heartbeat_schema`), and new workflow steps (lint, query filing).

- **[personas.md](personas.md)** — Persona specification. Will need a new
  section documenting the `BRAIN.md` and `HEARTBEAT.md` conventions and the
  new `mcp.json` keys for lint, consolidation, and query filing configuration.
