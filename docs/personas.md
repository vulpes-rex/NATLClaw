# NATLClaw — External Persona Specification

## 1. Overview

A **persona** is an externally-defined personality, instruction set, and tooling
configuration that the NATLClaw agent loads at runtime. Personas are developed
_outside_ the agent codebase and plugged in via the central `mcp.json` config
file at the project root.

The agent is the **engine**. Personas are the **configuration**.

```
mcp.json                     ← Declares MCP servers + personas
personas/
├── default/
│   └── instructions.md      ← System prompt (markdown)
├── devops_engineer/
│   ├── instructions.md
│   └── tools.py             ← Optional local Python tools
└── ...
```

---

## 2. Design Principles

| Principle | Detail |
|---|---|
| **External** | Personas live outside agent source code — in `personas/` or any path `mcp.json` points to. |
| **Declarative** | All configuration is in `mcp.json`. No code changes needed to add a persona. |
| **Composable** | A persona can combine markdown instructions, local Python tools, and remote MCP server tools. |
| **Swappable** | Switch personas at runtime via the `PERSONA` env var. |
| **Self-contained** | Each persona directory holds everything it needs — instructions, tools, assets. |

---

## 3. `mcp.json` Structure

The root file is `mcp.json` at the project root. It has two top-level keys:

```jsonc
{
  "mcpServers": { ... },   // Shared MCP server definitions
  "personas":   { ... }    // Persona definitions keyed by name
}
```

### 3.1 `mcpServers` — MCP Server Pool

A shared pool of MCP server connections. Personas reference these by name.

```jsonc
{
  "mcpServers": {
    "<server-name>": {
      "type":    "stdio" | "http",          // Required
      "command": "docker",                  // Required for stdio
      "args":    ["mcp", "server"],         // Optional, default []
      "env":     { "KEY": "value" },        // Optional, default {}
      "url":     "http://localhost:3001",    // Required for http
      "headers": { "Authorization": "..." },// Optional for http
      "timeout": 30                         // Optional, default 30
    }
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `"stdio"` \| `"http"` | Yes | Server transport type |
| `command` | `string` | stdio only | Executable to spawn |
| `args` | `string[]` | No | Command-line arguments |
| `env` | `object` | No | Environment variables for the process |
| `url` | `string` | http only | Remote MCP server URL |
| `headers` | `object` | No | HTTP headers (auth tokens, etc.) |
| `timeout` | `integer` | No | Connection timeout in seconds (default 30) |

### 3.2 `personas` — Persona Definitions

Each persona is a named entry under `personas`:

```jsonc
{
  "personas": {
    "<persona-name>": {
      "description":   "Short human label",
      "instructions":  "personas/<name>/instructions.md",
      "heartbeatTask": "What the agent does each heartbeat cycle.",
      "mcpServers":    ["server-a", "server-b"],
      "tools": {
        "module":    "personas.<name>.tools",
        "functions": ["fn_a", "fn_b"]
      }
    }
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `description` | `string` | Yes | Short description of the persona |
| `instructions` | `string` | Yes | Relative path to a markdown file with the system prompt |
| `heartbeatTask` | `string` | Yes | Prompt used during the "capture" step of each heartbeat |
| `mcpServers` | `string[]` | No | Names of MCP servers from the top-level `mcpServers` pool |
| `inheritBaseTools` | `boolean` | No | Default `true`. Merge `core_agent_tools.get_base_tools()` with this persona’s extension tools; extension wins on duplicate function names. Set `false` to use only the persona’s `tools` module/file. |
| `tools` | `object` | No | Local Python tool configuration |
| `tools.module` | `string` | Yes (if `tools`) | Dotted Python module path to import |
| `tools.functions` | `string[]` | No | Specific function names to load. If omitted, all public functions are loaded. |

---

## 4. Persona Resolution at Runtime

```
1. Agent reads PERSONA env var (default: "default")
2. Load mcp.json from project root
3. Look up persona by name in personas{}
4. Read instructions markdown file → system prompt
5. Import tools.module (or tools.file), resolve tools.functions → extension callable list
6. Merge core base tools + extension (unless inheritBaseTools is false); extension overrides base on same __name__
7. Resolve mcpServers[] → typed MCP server configs from top-level pool
8. Pass instructions + merged tools + mcp_servers to the agent runtime
```

Core base tools live in `core_agent_tools.py` (`get_base_tools()`). Persona-specific tools remain in `personas/<name>/tools.py` (or an external module). The resolved list is stored on `Persona.tools`; raw manifest tools are also available as `Persona.extension_tools` for debugging.

---

## 5. Instructions File Format

The instructions file is a standard Markdown document. It becomes the agent's
system prompt verbatim. Recommended structure:

```markdown
# Persona Name

Brief role description.

## Responsibilities

1. **Area** — what the persona does
2. ...

## Tools available

- `tool_name` — what it does
- ...

## MCP Tools available

- **server_name** — what tools it provides

## Guidelines

- Behavioral rules
- Output format expectations
- When asked to return JSON, return ONLY valid JSON with no extra text
```

---

## 6. Adding a New Persona

1. Create a directory: `personas/<name>/`
2. Write `personas/<name>/instructions.md`
3. (Optional) Write `personas/<name>/tools.py` with plain Python functions
4. (Optional) Add an MCP server entry to `mcpServers` in `mcp.json`
5. Add the persona entry to `personas` in `mcp.json`
6. Set `PERSONA=<name>` in `.env` and run the agent

You do not need to edit Python to register a persona. Base tools are merged automatically unless you set `inheritBaseTools` to `false`.

---

## 7. Example: Minimal Persona

```jsonc
// mcp.json
{
  "mcpServers": {},
  "personas": {
    "note_taker": {
      "description":   "Simple note-taking agent",
      "instructions":  "personas/note_taker/instructions.md",
      "heartbeatTask": "Capture one new insight from your recent observations."
    }
  }
}
```

```markdown
<!-- personas/note_taker/instructions.md -->
# Note Taker

You are a focused note-taking agent. Capture clear, atomic notes.
When asked to return JSON, return ONLY valid JSON with no extra text.
```

## 8. Example: Full Persona with Tools + MCP

```jsonc
{
  "mcpServers": {
    "docker": {
      "type": "stdio",
      "command": "docker",
      "args": ["mcp", "server"]
    }
  },
  "personas": {
    "devops_engineer": {
      "description":   "DevOps / infrastructure engineer",
      "instructions":  "personas/devops_engineer/instructions.md",
      "heartbeatTask": "Check container health and capture operational insights.",
      "mcpServers":    ["docker"],
      "tools": {
        "module":    "personas.devops_engineer.tools",
        "functions": ["list_files", "read_source_file", "run_shell_command"]
      }
    }
  }
}
```

---

## 9. Loading a Persona from a 3rd-Party Repository

Personas can live in any external repository — they don't need to be copied into
`personas/` or modify any agent source code. There are three supported patterns:

### Pattern A — `persona.json` manifest (recommended)

The external repo ships a `persona.json` manifest (validated against
`persona.schema.json`). The agent discovers it via `personasPaths` in `mcp.json`.

**External repo layout:**
```
my-persona-repo/
├── persona.json          ← manifest
├── instructions.md       ← system prompt
└── tools.py              ← optional tools
```

**`persona.json`:**
```jsonc
{
  "$schema": "https://raw.githubusercontent.com/.../persona.schema.json",
  "name": "data_scientist",
  "description": "Data science and ML analyst",
  "instructions": "instructions.md",
  "heartbeatTask": "Identify one ML insight from recent project activity.",
  "tools": {
    "file": "./tools.py"
  }
}
```

**`mcp.json` — point to the external directory:**
```jsonc
{
  "personasPaths": [
    "personas",                      // local (default)
    "C:/path/to/my-persona-repo",    // absolute — cloned external repo
    "../shared-personas"             // relative to mcp.json
  ]
}
```

The loader scans each `personasPaths` entry for `persona.json` files, one level
deep. Supported layouts:

```
my-persona-repo/persona.json           ← single-persona repo (root scanned)
shared-personas/analyst/persona.json   ← multi-persona repo (subdirs scanned)
shared-personas/developer/persona.json
```

Inline `personas` in `mcp.json` always win on name collision.

---

### Pattern B — Installed Python package

If the external persona ships as a published Python package:

```sh
.venv\Scripts\pip install natl-persona-datasci
```

Reference it by module path directly in `mcp.json` — no `personasPaths` needed:

```jsonc
{
  "personas": {
    "data_scientist": {
      "description":   "Data science analyst",
      "instructions":  "personas/data_scientist/instructions.md",
      "heartbeatTask": "...",
      "tools": { "module": "natl_persona_datasci.tools" }
    }
  }
}
```

---

### Pattern C — Git submodule

Clone the external repo into the project tree and let `personasPaths` discover it:

```sh
git submodule add https://github.com/org/my-persona external-personas/my-persona
```

```jsonc
{
  "personasPaths": ["external-personas"]
}
```

The `persona.json` inside the submodule is discovered automatically.

---

### Tool loading for external personas

| Source | Field in `persona.json` `tools` | When to use |
|--------|--------------------------------|-------------|
| `.py` file in the repo | `"file": "./tools.py"` | Repo is not an installed package |
| Installed package | `"module": "pkg.tools"` | Package on PyPI / private registry |

`tools.file` paths resolve relative to the `persona.json` file — the persona dir
is fully self-contained with no path assumptions about the host project.

---

### Resolution order summary

```
1. Inline personas in mcp.json            (highest priority)
2. External personas from personasPaths   (discovery order)
3. Built-in default                       (fallback)
```
