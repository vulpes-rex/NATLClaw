# NATLClaw as a Learning Coding Agent

## Vision

NATLClaw runs alongside your development workflow as a background learning agent that continuously observes your codebase, captures patterns and conventions, and feeds that knowledge back into Copilot CLI / Copilot Chat to make AI-assisted coding progressively more aligned with how you actually work.

The core insight: **existing AI coding tools index what code exists; NATLClaw learns how you think about code.**

---

## 1  Why This Fits

NATLClaw's heartbeat loop is purpose-built for this use case:

| Existing capability | How it applies |
|---|---|
| Heartbeat loop | Runs in the background while you code |
| Persona system | A `codebase_learner` persona focuses on your project |
| Second brain | Captures and connects knowledge across sessions |
| Lesson extraction | Already detects error/success/warning patterns |
| MCP support | Can connect to filesystem, git, language servers |
| PARA categories | Maps naturally to code: projects=features, areas=modules, resources=patterns, archive=deprecated |

The missing piece is an **observation layer** that feeds your development activity into the heartbeat loop, and an **output layer** that makes the accumulated knowledge available to Copilot.

---

## 2  Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Your development workflow                                  │
│                                                             │
│  VS Code / Terminal                                         │
│    ├── Copilot CLI / Copilot Chat                           │
│    ├── Git operations                                       │
│    └── File edits                                           │
│         │          │           │                            │
│         ▼          ▼           ▼                            │
│  ┌──────────────────────────────────────┐                   │
│  │  Event Queue                        │                   │
│  │  (file watcher + git hooks +        │                   │
│  │   Copilot observer)                 │                   │
│  └──────────────┬───────────────────────┘                   │
│                 │                                           │
│                 ▼                                           │
│  ┌──────────────────────────────────────┐                   │
│  │  NATLClaw Heartbeat Loop            │                   │
│  │  (codebase_learner persona)         │                   │
│  │                                     │                   │
│  │  1. Ingest events since last cycle  │                   │
│  │  2. Analyse changes (AST, diff)     │                   │
│  │  3. Update codebase model           │                   │
│  │  4. Capture patterns / conventions  │                   │
│  │  5. Connect to existing knowledge   │                   │
│  │  6. Write CODEBASE_CONTEXT.md       │                   │
│  └──────────────┬───────────────────────┘                   │
│                 │                                           │
│                 ▼                                           │
│  ┌──────────────────────────────────────┐                   │
│  │  Second Brain                       │                   │
│  │  ├── Architecture notes             │                   │
│  │  ├── Pattern notes                  │  ◀── MCP Server   │
│  │  ├── Convention notes               │      endpoint     │
│  │  ├── Dependency graph               │      for Copilot  │
│  │  └── Developer preference model     │                   │
│  └──────────────────────────────────────┘                   │
└─────────────────────────────────────────────────────────────┘
```

---

## 3  Observation Layer

### 3.1  Event model

Every development activity produces a `CodebaseEvent` that enters a queue between heartbeats:

```python
@dataclass
class CodebaseEvent:
    """A single development activity observation."""
    type: str          # "file_changed" | "git_commit" | "test_result"
                       # | "copilot_accept" | "copilot_reject" | "terminal_command"
    path: str          # file path or repo-relative path
    diff: str          # unified diff, commit message, or command output
    timestamp: str     # ISO 8601
    metadata: dict     # language, function names, commit hash, etc.
```

### 3.2  Event sources

| Source | Mechanism | Events produced |
|---|---|---|
| **File watcher** | `watchdog` library monitoring workspace | `file_changed` with path + diff |
| **Git hooks** | `post-commit` hook writes to event queue | `git_commit` with message + changed files + diff stats |
| **Test runner** | `pytest` plugin or post-test hook | `test_result` with pass/fail + coverage delta |
| **Copilot observer** | VS Code extension telemetry or log parsing | `copilot_accept`, `copilot_reject` with suggestion content |
| **Terminal watcher** | Shell history monitoring | `terminal_command` with command + exit code |

### 3.3  Event queue

Events accumulate in a simple append-only JSON file between heartbeats:

```python
# codebase_observer.py

EVENT_QUEUE_PATH = "data/event_queue.json"

async def push_event(event: CodebaseEvent) -> None:
    """Append an event to the queue (called by watchers)."""
    async with aiofiles.open(EVENT_QUEUE_PATH, "a") as f:
        await f.write(json.dumps(asdict(event)) + "\n")

async def drain_events() -> list[CodebaseEvent]:
    """Read and clear all pending events (called by heartbeat)."""
    if not os.path.exists(EVENT_QUEUE_PATH):
        return []
    async with aiofiles.open(EVENT_QUEUE_PATH, "r") as f:
        lines = await f.readlines()
    os.remove(EVENT_QUEUE_PATH)
    return [CodebaseEvent(**json.loads(line)) for line in lines if line.strip()]
```

When the heartbeat fires, it calls `drain_events()` and processes the batch.

---

## 4  Code-Aware Note Types

Generic text notes are insufficient for code knowledge. The brain needs structured note types that map to how developers think about codebases.

### 4.1  Pattern notes

Recurring code patterns the agent observes across multiple files:

```json
{
    "type": "pattern",
    "content": "All API calls use a custom useApi hook that handles loading/error states",
    "evidence": ["src/hooks/useApi.ts", "src/pages/Dashboard.tsx#L14", "src/pages/Settings.tsx#L22"],
    "confidence": 0.92,
    "first_seen": "2026-04-01T10:00:00Z",
    "last_confirmed": "2026-04-07T14:30:00Z",
    "tags": ["react", "hooks", "api", "pattern"]
}
```

### 4.2  Architecture notes

High-level structural understanding of the project:

```json
{
    "type": "architecture",
    "content": "State management uses Zustand with one store per feature domain",
    "modules": ["src/stores/authStore.ts", "src/stores/cartStore.ts", "src/stores/uiStore.ts"],
    "relationships": [
        {"from": "src/stores/cartStore.ts", "to": "src/stores/authStore.ts", "reason": "cart reads user ID from auth"}
    ],
    "tags": ["architecture", "state-management", "zustand"]
}
```

### 4.3  Convention notes

Coding style and preferences inferred from your edits:

```json
{
    "type": "convention",
    "content": "Error handling uses a Result<T,E> sum type — never raw try/catch in business logic",
    "examples": ["src/utils/result.ts#L1-L25", "src/services/payment.ts#L44"],
    "strength": "strong",
    "tags": ["convention", "error-handling", "typescript"]
}
```

### 4.4  Preference notes

What the developer accepts vs. rejects from AI suggestions:

```json
{
    "type": "preference",
    "content": "Developer prefers named exports over default exports",
    "evidence_for": 14,
    "evidence_against": 2,
    "confidence": 0.875,
    "tags": ["preference", "imports", "style"]
}
```

### 4.5  Dependency notes

Structural relationships between modules:

```json
{
    "type": "dependency",
    "from": "src/api/client.ts",
    "to": "src/config/env.ts",
    "relationship": "imports BASE_URL from env config",
    "tags": ["dependency", "api", "config"]
}
```

### 4.6  Implementation in `second_brain.py`

The existing `Note` dataclass gains a `note_type` field:

```python
@dataclass
class Note:
    id: str
    content: str
    note_type: str = "general"  # NEW: general | pattern | architecture | convention
                                #      | preference | dependency
    summary: str = ""
    source: str = "agent"
    tags: list[str] = field(default_factory=list)
    category: str = "resources"
    connections: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)  # NEW: file paths, line refs
    confidence: float = 1.0                             # NEW: 0.0–1.0
    created_at: str = ""
    updated_at: str = ""
```

Query functions are extended to filter by type:

```python
def get_notes_by_type(brain: BrainState, note_type: str) -> list[dict]:
    """Return notes filtered by type (pattern, convention, etc.)."""
    return [n for n in brain.notes.values() if n.get("note_type") == note_type]

def get_patterns(brain: BrainState) -> list[dict]:
    return get_notes_by_type(brain, "pattern")

def get_conventions(brain: BrainState) -> list[dict]:
    return get_notes_by_type(brain, "convention")

def get_architecture(brain: BrainState) -> list[dict]:
    return get_notes_by_type(brain, "architecture")
```

---

## 5  Codebase Learner Persona

### 5.1  Persona definition in `mcp.json`

```jsonc
"codebase_learner": {
    "description": "Background agent that learns your codebase patterns and conventions",
    "instructions": "personas/codebase_learner/instructions.md",
    "workflow": "steps",
    "stepwise": false,
    "tools": {
        "module": "personas.codebase_learner.tools",
        "functions": [
            "list_files", "read_source_file", "read_git_log",
            "read_git_diff", "search_codebase", "drain_events",
            "write_context_file"
        ]
    },
    "steps": [
        {
            "name": "ingest",
            "prompt": "You are a codebase learning agent. Process events that occurred since the last heartbeat.\n\nPending events:\n{events}\n\nFor each event, extract: what changed, why it matters, and what pattern or convention it reveals. Summarise the batch in 3-5 bullet points.",
            "storeToBrain": false
        },
        {
            "name": "analyse",
            "prompt": "Based on the events you just processed:\n{prev}\n\nAnd the current brain knowledge:\n{brain}\n\nIdentify ONE of the following:\n1. A new pattern you haven't seen before\n2. A strengthened confidence in an existing pattern\n3. An architectural insight about how modules relate\n4. A developer preference (what they accept/reject from AI)\n\nReturn as JSON:\n{\"note_type\": \"pattern|convention|architecture|preference\", \"content\": \"...\", \"evidence\": [\"file paths\"], \"confidence\": 0.0-1.0, \"tags\": [...]}",
            "storeToBrain": true
        },
        {
            "name": "connect",
            "prompt": "Review these recent brain notes:\n{brain}\n\nFind ONE meaningful connection between any two notes — perhaps a pattern that supports an architectural decision, or a convention that contradicts an older observation.\n\nReturn as JSON: {\"from\": \"<id>\", \"to\": \"<id>\", \"reason\": \"...\"}",
            "storeToBrain": false
        },
        {
            "name": "context_export",
            "prompt": "Generate a CODEBASE_CONTEXT.md file from the current brain state.\n\n{brain}\n\nThe file should contain:\n1. ## Architecture — 3-5 bullet points on project structure\n2. ## Patterns — recurring code patterns with file references\n3. ## Conventions — coding style rules with examples\n4. ## Dependencies — key module relationships\n5. ## Developer Preferences — what the developer prefers\n\nKeep it under 200 lines. Write it using the write_context_file tool.",
            "storeToBrain": false
        }
    ]
}
```

### 5.2  Persona instructions

```markdown
# Codebase Learner Agent

You are a background learning agent that observes a developer's codebase and
development activity. Your job is to build a growing understanding of:

1. **Architecture** — how the project is structured, what depends on what
2. **Patterns** — recurring code patterns used across the codebase
3. **Conventions** — naming, error handling, import style, test structure
4. **Preferences** — what the developer likes and dislikes in generated code

## Rules

- Never modify source code. You are read-only.
- Focus on patterns that appear in 2+ files (not one-off implementations).
- Update confidence scores: confirm = +0.1, contradict = −0.2.
- Decay notes you haven't confirmed in 20+ heartbeats.
- Keep CODEBASE_CONTEXT.md concise — Copilot will read it every request.
- Prefer specific file references over vague descriptions.
```

### 5.3  Persona tools

```python
# personas/codebase_learner/tools.py

import json
import os
import subprocess
from pathlib import Path

WORKSPACE = os.environ.get("NATL_WORKSPACE", "workspace")


def list_files(directory: str = ".") -> str:
    """List files in the workspace directory."""
    target = Path(WORKSPACE) / directory
    if not target.exists():
        return f"Directory not found: {directory}"
    files = []
    for p in sorted(target.rglob("*")):
        if p.is_file() and not any(part.startswith(".") for part in p.parts):
            files.append(str(p.relative_to(WORKSPACE)))
    return "\n".join(files[:200])


def read_source_file(path: str) -> str:
    """Read a source file from the workspace."""
    target = Path(WORKSPACE) / path
    if not target.exists():
        return f"File not found: {path}"
    return target.read_text(encoding="utf-8")[:8000]


def read_git_log(count: int = 10) -> str:
    """Read recent git commits."""
    try:
        result = subprocess.run(
            ["git", "log", f"-{count}", "--oneline", "--stat"],
            capture_output=True, text=True, cwd=WORKSPACE, timeout=10,
        )
        return result.stdout[:4000]
    except Exception as e:
        return f"Git log failed: {e}"


def read_git_diff(ref: str = "HEAD~1") -> str:
    """Read git diff against a reference."""
    try:
        result = subprocess.run(
            ["git", "diff", ref, "--stat", "-p"],
            capture_output=True, text=True, cwd=WORKSPACE, timeout=10,
        )
        return result.stdout[:6000]
    except Exception as e:
        return f"Git diff failed: {e}"


def search_codebase(pattern: str, file_glob: str = "*.py") -> str:
    """Search for a pattern in the codebase using grep."""
    try:
        result = subprocess.run(
            ["grep", "-rn", "--include", file_glob, pattern, "."],
            capture_output=True, text=True, cwd=WORKSPACE, timeout=10,
        )
        return result.stdout[:4000]
    except Exception as e:
        return f"Search failed: {e}"


def drain_events() -> str:
    """Read and clear pending codebase events."""
    queue_path = "data/event_queue.json"
    if not os.path.exists(queue_path):
        return "No pending events."
    with open(queue_path, "r", encoding="utf-8") as f:
        content = f.read()
    os.remove(queue_path)
    return content[:6000] or "No pending events."


def write_context_file(content: str) -> str:
    """Write the CODEBASE_CONTEXT.md file for Copilot to consume."""
    target = Path(WORKSPACE) / "CODEBASE_CONTEXT.md"
    target.write_text(content, encoding="utf-8")
    return f"Written {len(content)} chars to {target}"
```

---

## 6  Copilot Integration

### 6.1  Option A: Context file injection (simplest)

Every heartbeat, NATLClaw writes `CODEBASE_CONTEXT.md` to the project root. Copilot CLI and Copilot Chat in VS Code automatically pick up files named `AGENTS.md`, `COPILOT.md`, or similar context files.

The file contains the agent's current understanding:

```markdown
# Codebase Context (auto-generated by NATLClaw)
# Last updated: 2026-04-07T14:30:00Z | Brain: 47 notes, 23 connections

## Architecture
- React 18 + TypeScript + Vite SPA
- Feature-based directory structure: src/features/{auth,cart,products}/
- Zustand stores per feature domain (src/stores/)
- API layer in src/api/ using custom fetch wrapper with Result<T,E>

## Patterns
- Custom hooks for all data fetching (useApi, useFetch, useQuery)
- Form validation uses Zod schemas co-located with form components
- All async operations return Result<T, AppError> — never raw promises

## Conventions
- Named exports only (no default exports)
- Error types extend AppError base class (src/errors/AppError.ts)
- Test files co-located: Component.test.tsx next to Component.tsx
- CSS modules for component styles, global theme in src/styles/

## Key Dependencies
- src/api/client.ts → src/config/env.ts (base URL)
- src/stores/cartStore.ts → src/stores/authStore.ts (user ID)
- src/features/checkout/ → src/api/stripe.ts + src/stores/cartStore.ts

## Developer Preferences
- Prefers explicit type annotations over inference (14 accept / 2 reject)
- Prefers early returns over nested if/else (9 accept / 1 reject)
- Dislikes barrel index files (0 in codebase, rejected twice from AI)
```

This file is small enough to fit in any context window and specific enough to meaningfully steer code generation.

### 6.2  Option B: MCP server endpoint (richer)

NATLClaw exposes its brain as an MCP server that Copilot can query dynamically:

```python
# mcp_brain_server.py

from mcp import Server, Tool

server = Server("natl-brain")

@server.tool("brain.query")
async def query_brain(query: str, note_type: str = None, limit: int = 5) -> str:
    """Query the NATLClaw brain for relevant knowledge."""
    brain = await load_brain(config.state_file)
    # Semantic search if index available, else keyword match
    results = search_brain(brain, query, note_type=note_type, limit=limit)
    return format_results(results)

@server.tool("brain.patterns")
async def get_patterns() -> str:
    """Get all known code patterns."""
    brain = await load_brain(config.state_file)
    patterns = get_notes_by_type(brain, "pattern")
    return json.dumps(patterns, indent=2)

@server.tool("brain.conventions")
async def get_conventions() -> str:
    """Get all known coding conventions."""
    brain = await load_brain(config.state_file)
    conventions = get_notes_by_type(brain, "convention")
    return json.dumps(conventions, indent=2)

@server.tool("brain.architecture")
async def get_architecture() -> str:
    """Get architectural understanding of the codebase."""
    brain = await load_brain(config.state_file)
    arch = get_notes_by_type(brain, "architecture")
    return json.dumps(arch, indent=2)
```

This would be registered in Copilot CLI's MCP config so the assistant can query the brain when generating code:

```jsonc
// Copilot CLI MCP config
{
    "mcpServers": {
        "natl-brain": {
            "type": "stdio",
            "command": "python",
            "args": ["-m", "natl.mcp_brain_server"]
        }
    }
}
```

### 6.3  Option C: Both (recommended)

Use context file injection for baseline knowledge (architecture, conventions) that applies to every request, and the MCP server for on-demand queries when Copilot needs deeper context about a specific module or pattern.

---

## 7  Learning from Copilot Usage

This is the unique value proposition. The agent doesn't just read code — it learns how you respond to AI-generated code.

### 7.1  Signal types

| Developer action | What NATLClaw learns |
|---|---|
| Accept Copilot suggestion as-is | Pattern is preferred; increase confidence |
| Accept then immediately edit | Base pattern is OK but preferences differ on details |
| Reject and write manually | The suggestion violated a convention; capture the delta |
| Undo an accepted suggestion | Strong negative signal — the pattern is wrong for this codebase |
| Same pattern accepted 3+ times | Promote to high-confidence convention |

### 7.2  Preference model

Over time, the brain accumulates a preference model:

```python
@dataclass
class PreferenceSignal:
    """A single observation of developer preference."""
    pattern: str           # "named_exports" | "early_return" | "explicit_types" | ...
    accepted: bool         # True if developer kept it, False if rejected
    context: str           # file path + surrounding code
    timestamp: str

def update_preference(brain: BrainState, signal: PreferenceSignal) -> None:
    """Update or create a preference note based on observed behavior."""
    existing = find_preference_note(brain, signal.pattern)
    if existing:
        if signal.accepted:
            existing["evidence_for"] = existing.get("evidence_for", 0) + 1
        else:
            existing["evidence_against"] = existing.get("evidence_against", 0) + 1
        total = existing["evidence_for"] + existing["evidence_against"]
        existing["confidence"] = existing["evidence_for"] / total
        existing["updated_at"] = datetime.now(timezone.utc).isoformat()
    else:
        add_note(brain,
            content=f"Developer {'prefers' if signal.accepted else 'avoids'} {signal.pattern}",
            note_type="preference",
            tags=["preference", signal.pattern],
            confidence=1.0 if signal.accepted else 0.0,
        )
```

### 7.3  Feedback loop

```
    Developer writes code
            │
            ▼
    Copilot suggests completion
    (informed by CODEBASE_CONTEXT.md)
            │
            ▼
    Developer accepts / rejects / edits
            │
            ▼
    NATLClaw observes the outcome
            │
            ▼
    Brain updates preference model
            │
            ▼
    Next heartbeat updates CODEBASE_CONTEXT.md
            │
            ▼
    Copilot's next suggestion is better aligned
            │
            ▼
    (cycle continues)
```

Each iteration makes Copilot's suggestions slightly more aligned with the developer's actual style. After a few days of active development, the context file reflects genuine project knowledge — not generic best practices.

---

## 8  Differentiation from Existing Tools

| Tool | What it knows | When it learns | Persistence | Knowledge type |
|---|---|---|---|---|
| **Copilot** | Current file + open tabs | Per-request | None | Code tokens |
| **Copilot Workspace** | Repo via embeddings | On-demand | Cached embeddings | Code similarity |
| **Cursor** | Codebase index | On file save | Embeddings | Code similarity |
| **Continue.dev** | Codebase + docs index | On-demand | Embeddings | Code + docs |
| **NATLClaw** | Patterns, conventions, architecture, preferences | Continuously | Structured brain + connections | Semantic knowledge |

Key differences:

1. **Structured knowledge vs. embeddings** — NATLClaw doesn't just know that code exists; it knows *why* code is structured a certain way and whether the developer prefers that structure.

2. **Continuous learning vs. on-demand indexing** — Embedding-based tools re-index when queried. NATLClaw learns between queries, building understanding proactively.

3. **Preference modelling** — No existing tool tracks what developers accept and reject from AI suggestions to improve future suggestions.

4. **Cross-session persistence** — NATLClaw's brain survives IDE restarts, repo switches, and machine changes (it's just a JSON file). Cursor's context resets per session.

5. **Auditable knowledge** — Every note has provenance (which file, which commit, which heartbeat). You can inspect and correct what the agent has learned.

---

## 9  Minimal Viable Version

A working prototype can be built in a weekend using existing NATLClaw infrastructure.

### Phase 1: Read-only codebase learner (4–6 hours)

1. **Create `codebase_learner` persona** in `mcp.json` with file-reading tools
2. **Add `read_git_log` tool** — reads recent commits as the event source
3. **Heartbeat workflow:**
   - Step 1: Read git log since last heartbeat
   - Step 2: Analyse changes and capture a pattern/convention note
   - Step 3: Write `CODEBASE_CONTEXT.md` from brain contents
4. **Test:** Run NATLClaw with `PERSONA=codebase_learner` while doing normal development. After 5–10 heartbeats, inspect the generated context file.

### Phase 2: File watcher + richer notes (1–2 days)

5. **Add `watchdog` file watcher** that queues `file_changed` events
6. **Add `note_type` field** to the `Note` dataclass
7. **Type-specific capture prompts** — different prompts for patterns vs. conventions vs. architecture
8. **Confidence scoring** — notes gain/lose confidence based on repeated observation

### Phase 3: Copilot feedback loop (2–3 days)

9. **Copilot usage observer** — monitor accepted/rejected suggestions
10. **Preference model** — track developer preferences from Copilot interactions
11. **MCP brain server** — expose brain as queryable MCP endpoint
12. **Adaptive context** — tailor `CODEBASE_CONTEXT.md` to the files most recently edited

### Phase 4: Polish (ongoing)

13. **Semantic search** — embed notes for relevance-based retrieval
14. **Tiered memory** — consolidate atomic observations into durable wiki pages
15. **Multi-project support** — separate brains per project root
16. **CLI commands** — `natl brain search`, `natl brain stats`, `natl context show`

---

## 10  Example Session

### Heartbeat #1 (cold start)

```
[INFO] === Heartbeat #1 starting ===
[INFO] [ingest] Read git log: 12 commits in last 24h
[INFO] [analyse] Detected pattern: "All React components use named exports"
[INFO]   Evidence: src/components/Header.tsx, src/components/Footer.tsx, src/components/Nav.tsx
[INFO]   Confidence: 0.75 (3 files observed)
[INFO] [connect] No connections yet (first heartbeat)
[INFO] [context_export] Wrote CODEBASE_CONTEXT.md (42 lines)
[INFO] === Heartbeat #1 completed in 8.3s ===
```

### Heartbeat #5 (learning)

```
[INFO] === Heartbeat #5 starting ===
[INFO] [ingest] 3 events: file_changed(src/hooks/useAuth.ts),
                           git_commit("Add auth hook with Result type"),
                           file_changed(src/hooks/useCart.ts)
[INFO] [analyse] Strengthened pattern: "Custom hooks for all data access"
[INFO]   Evidence: src/hooks/useApi.ts, src/hooks/useAuth.ts, src/hooks/useCart.ts
[INFO]   Confidence: 0.75 → 0.92
[INFO] [analyse] New convention: "Hooks return Result<T, AppError> not raw data"
[INFO]   Evidence: src/hooks/useAuth.ts#L15, src/hooks/useCart.ts#L22
[INFO]   Confidence: 0.80
[INFO] [connect] Linked n0012 (hook pattern) ↔ n0003 (Result<T,E> convention)
[INFO]   Reason: "Hooks use Result type consistent with error handling convention"
[INFO] [context_export] Updated CODEBASE_CONTEXT.md (67 lines)
[INFO] === Heartbeat #5 completed in 6.1s ===
```

### Heartbeat #20 (mature)

```
[INFO] === Heartbeat #20 starting ===
[INFO] [ingest] 1 event: copilot_reject(suggestion used default export)
[INFO] [analyse] Preference reinforced: "Named exports over default exports"
[INFO]   evidence_for: 14, evidence_against: 2, confidence: 0.875
[INFO] [analyse] Decayed 2 stale notes (no confirmation in 15+ heartbeats)
[INFO] [context_export] Updated CODEBASE_CONTEXT.md (89 lines, 23 patterns, 8 conventions)
[INFO] === Heartbeat #20 completed in 4.2s ===
```

At this point, Copilot's suggestions are noticeably better — it uses named exports, returns Result types from hooks, follows the project's error handling patterns, and structures new components to match the existing architecture.

---

## 11  Risks and Mitigations

| Risk | Mitigation |
|---|---|
| **Stale/wrong knowledge** | Confidence decay + periodic lint step removes low-confidence notes |
| **Context file too large** | Cap at 200 lines; prioritise by confidence and recency |
| **Token cost per heartbeat** | Adaptive interval (back off when few events); skip heartbeat if no events |
| **Privacy (code leaving machine)** | All processing is local; brain is a local JSON file; context file stays in repo |
| **Conflicting patterns** | Agent flags contradictions in the connect step; developer can resolve via CLI |
| **Hallucinated patterns** | Evidence field requires file paths; lint step verifies evidence files still exist |

---

## 12  Relation to Other Docs

- **`docs/tiered-memory.md`** — The consolidation loop applies directly: atomic observations (short-term) consolidate into pattern/convention pages (long-term).
- **`docs/knowledge-quality.md`** — Lint, source citations, and BRAIN.md schema all apply to code notes. The lint step should verify that evidence file paths still exist.
- **`docs/improvements.md`** — Semantic search (1.1), note dedup (1.3), adaptive interval (1.4), and the CLI (4.1) are all prerequisites or accelerators for this use case.
- **`docs/openclaw-comparison.md`** — The codebase learner could also run as an OpenClaw skill, feeding code knowledge into multi-channel conversations about your project.
