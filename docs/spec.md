# NATLClaw — POC Technical Spec

## 1. Architecture Overview

```
main.py                  ← Entry point: parse config, start heartbeat loop
├── config.py            ← Load settings from .env / env vars
├── scheduler.py         ← Async heartbeat loop with graceful shutdown
├── agent_setup.py       ← Build the Agent instance from config (provider-agnostic)
├── state.py             ← Load/save JSON state, manage execution history
├── workflow.py          ← Define and run the 3-step heartbeat workflow
└── learning.py          ← Extract lessons, build context enrichment from memory
```

State file: `data/agent_state.json` (auto-created)

---

## 2. Module Specifications

### 2.1 `config.py` — Configuration

```python
@dataclass
class AppConfig:
    # LLM Provider
    provider: str              # "copilot" | "foundry" | "openai" | "ollama"
    model: str                 # model name (e.g. "claude-sonnet-4", "gpt-4o-mini")
    project_endpoint: str      # Azure Foundry endpoint (if provider=foundry)
    openai_api_key: str        # OpenAI key (if provider=openai)
    ollama_host: str           # Ollama base URL (if provider=ollama)

    # Scheduler
    heartbeat_interval_sec: int  # default 120 (2 min for POC)

    # State
    state_file: str            # default "data/agent_state.json"
    max_history: int           # default 100

    # Agent
    agent_name: str            # default "NATLClaw"
    agent_instructions: str    # base system prompt
```

Loaded via `python-dotenv` from `.env`, with sensible defaults.

### 2.2 `state.py` — State Management

```python
@dataclass
class AgentState:
    last_heartbeat: str | None
    execution_count: int
    memory: dict
    context: dict
    execution_history: list[dict]   # [{timestamp, step, prompt, response}]
    lessons_learned: list[dict]     # [{type, description, timestamp}]

def load_state(path: str) -> AgentState: ...
def save_state(state: AgentState, path: str) -> None: ...
```

- File created automatically on first run.
- `save_state` writes atomically (write to temp file, then rename) to prevent corruption.
- History trimmed to `max_history` on every save.

### 2.3 `agent_setup.py` — Agent Factory

```python
def create_agent(config: AppConfig) -> Agent:
    """Build an Agent with the configured LLM provider."""
```

Provider dispatch:
| `config.provider` | Agent/Client class | Auth |
|-|-|-|
| `"copilot"` (default) | `GitHubCopilotAgent` | `gh auth login` session |
| `"foundry"` | `Agent` + `FoundryChatClient` | `AzureCliCredential` |
| `"openai"` | `Agent` + `OpenAIChatClient` | API key |
| `"ollama"` | `Agent` + `OllamaChatClient` | None |

Returns a configured `Agent(client=..., name=..., instructions=...)`.

### 2.4 `learning.py` — Learning & Context Enrichment

```python
def extract_lessons(step: str, prompt: str, response: str) -> list[dict]:
    """Parse a response for error/success/pattern signals, return lesson dicts."""

def build_context_block(state: AgentState, max_recent: int = 5) -> str:
    """Build a text block summarizing recent activity and lessons for injection
    into the agent's system prompt."""
```

Context block format (injected before each heartbeat):
```
== AGENT MEMORY ==
Last heartbeat: 2026-04-07T10:00:00
Total executions: 42
Recent lessons: [list of last 5 lessons]
Recent activity: [list of last 5 execution summaries]
```

### 2.5 `workflow.py` — Heartbeat Workflow

```python
async def run_heartbeat(agent: Agent, state: AgentState, config: AppConfig) -> None:
    """Execute the 3-step heartbeat workflow."""
```

**Step 1 — Status Check:**
- Prompt the agent with its own state summary (execution count, last run, recent errors).
- Agent responds with a status assessment.

**Step 2 — Task Execution:**
- Prompt the agent with the primary task.
- For POC: a configurable task prompt (e.g., "Research the latest developments in AI agents").
- The status check result is included as context.

**Step 3 — Report Generation:**
- Prompt the agent to summarize the heartbeat cycle.
- Input: status check + task execution results.
- Output is logged and stored in execution history.

Each step:
1. Logs step name + start time.
2. Calls `agent.run(prompt)`.
3. Records result in `state.execution_history`.
4. Extracts lessons via `learning.extract_lessons()`.
5. Logs step completion + duration.

### 2.6 `scheduler.py` — Heartbeat Loop

```python
async def run_scheduler(config: AppConfig) -> None:
    """Run the heartbeat loop until interrupted."""
```

Loop:
1. Load state.
2. Create agent (fresh each heartbeat to pick up config changes).
3. Enrich agent instructions with `learning.build_context_block(state)`.
4. Call `workflow.run_heartbeat(agent, state, config)`.
5. Save state.
6. Sleep `heartbeat_interval_sec`.
7. On error: log, save state, continue to next heartbeat.
8. On SIGINT/KeyboardInterrupt: save state, exit cleanly.

### 2.7 `main.py` — Entry Point

```python
if __name__ == "__main__":
    config = load_config()
    asyncio.run(run_scheduler(config))
```

---

## 3. State File Schema

`data/agent_state.json`:

```json
{
  "last_heartbeat": "2026-04-07T10:00:00",
  "execution_count": 42,
  "memory": {},
  "context": {},
  "execution_history": [
    {
      "timestamp": "2026-04-07T10:00:00",
      "step": "status_check",
      "prompt": "...",
      "response": "..."
    }
  ],
  "lessons_learned": [
    {
      "type": "success_achieved",
      "description": "Successfully completed status check",
      "timestamp": "2026-04-07T10:00:00"
    }
  ]
}
```

---

## 4. Environment Variables

```env
# Provider (pick one)
PROVIDER=copilot                           # copilot | foundry | openai | ollama

# GitHub Copilot (default — uses gh auth session)
GITHUB_COPILOT_MODEL=claude-sonnet-4

# Azure Foundry (alternative)
# AZURE_AI_PROJECT_ENDPOINT=https://...
# AZURE_AI_MODEL_DEPLOYMENT_NAME=gpt-4o-mini

# OpenAI (alternative)
# OPENAI_API_KEY=sk-...
# OPENAI_MODEL=gpt-4o-mini

# Ollama (alternative)
# OLLAMA_HOST=http://localhost:11434
# OLLAMA_MODEL=llama3

# Scheduler
HEARTBEAT_INTERVAL_SEC=120

# State
STATE_FILE=data/agent_state.json
MAX_HISTORY=100

# Agent
AGENT_NAME=NATLClaw
AGENT_INSTRUCTIONS=You are an autonomous agent that performs periodic tasks, learns from past interactions, and maintains persistent memory.
```

---

## 5. Execution Flow Diagram

```
┌─────────────────────────────────────────────────────────┐
│                      main.py                            │
│  load_config() → run_scheduler(config)                  │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                   scheduler.py                          │
│  loop:                                                  │
│    ┌─ load_state()                                      │
│    ├─ create_agent(config)                              │
│    ├─ enrich instructions with context_block            │
│    ├─ run_heartbeat(agent, state, config)               │
│    │    ├─ Step 1: Status Check                         │
│    │    ├─ Step 2: Task Execution                       │
│    │    └─ Step 3: Report Generation                    │
│    ├─ save_state()                                      │
│    └─ sleep(interval)                                   │
└─────────────────────────────────────────────────────────┘
```

---

## 6. POC Success Criteria

1. `python main.py` starts the heartbeat loop and runs at least 3 cycles without errors.
2. State file persists between process restarts — execution count continues from where it left off.
3. Lessons from previous heartbeats appear in the context of subsequent ones.
4. Switching `PROVIDER` from `copilot` to `openai` or `ollama` works with only `.env` changes.
5. Ctrl+C shuts down cleanly with state saved.
