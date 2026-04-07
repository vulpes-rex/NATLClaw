# NATLClaw — POC Requirements

## 1. Project Goal

Build a proof-of-concept autonomous agent system using the Microsoft Agent Framework (Python) that:

- Runs periodically on a configurable heartbeat interval
- Persists state and memory between executions
- Learns from past interactions to improve future responses
- Executes a multi-step workflow each heartbeat cycle
- Is runnable locally with no cloud deployment required

---

## 2. Functional Requirements

### FR-1: Heartbeat Scheduler
- The system runs an agent loop on a configurable interval (default: every 2 minutes for POC).
- Each heartbeat triggers a full workflow cycle.
- The scheduler runs locally as an async loop (no Azure Functions for POC).
- Supports graceful shutdown via Ctrl+C / SIGINT.

### FR-2: State Persistence
- Agent state is persisted to a local JSON file between heartbeats.
- State includes: memory, context, execution history, lessons learned.
- State is loaded at startup and saved after every heartbeat.
- Execution history is capped (keep last 100 entries).

### FR-3: Memory & Learning
- The agent maintains a memory dict that carries across heartbeats.
- After each execution, the system extracts "lessons" from the interaction (errors, successes, patterns).
- Lessons are stored and summarized, and fed back into future prompts as context.
- The agent's system instructions are enriched with relevant memory and recent activity before each run.

### FR-4: Multi-Step Workflow
Each heartbeat executes these steps in order:
1. **System Status Check** — Agent reviews its own state (execution count, last run, recent errors).
2. **Task Execution** — Agent performs a configurable primary task (e.g., analyze a topic, summarize data).
3. **Report Generation** — Agent produces a summary of what happened during this heartbeat cycle.

Steps are sequential; each step's output feeds into the next.

### FR-5: Configuration
- All settings loaded from environment variables (`.env` file) and/or a config file.
- Configurable: heartbeat interval, model, state file path, max history size.
- Primary provider: **GitHub Copilot CLI** via `GitHubCopilotAgent` (uses existing `gh auth` session).
- Fallback providers (Azure Foundry, OpenAI, Ollama) supported by changing `PROVIDER` in config.

### FR-6: Logging & Observability
- Structured logging to console (INFO level by default).
- Each heartbeat logs: start time, step transitions, token usage if available, end time, duration.
- Errors are logged with full tracebacks but do not crash the scheduler.

---

## 3. Non-Functional Requirements

### NFR-1: Local-First
- The POC runs entirely on a developer machine with `python main.py`.
- Default provider (GitHub Copilot) requires only `gh auth login` — no API keys or Azure setup.
- Alternative providers (Azure Foundry, OpenAI, Ollama) supported but not required.

### NFR-2: Simplicity
- Minimal dependencies beyond `agent-framework` and `python-dotenv`.
- No database, no message queue, no Docker required.
- Single entry point (`main.py`).

### NFR-3: Extensibility
- Adding new workflow steps should require only adding a function and registering it.
- Swapping the LLM provider should be a config change.

---

## 4. Out of Scope (for POC)

- Azure Functions / Durable Task hosting
- Web UI or API endpoints
- Multi-agent orchestration (single agent for now)
- Production state storage (Cosmos DB, Azure Blob, Redis)
- Authentication beyond `gh auth login` / API keys
- DevUI integration
- OpenTelemetry / distributed tracing
