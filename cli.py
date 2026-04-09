"""NATLClaw CLI — command-line interface with subcommands.

Usage
-----
::

    python cli.py run                    # Start the heartbeat scheduler
    python cli.py run --once             # Run a single heartbeat and exit
    python cli.py code "fix the tests"   # One-shot agentic task
    python cli.py code                   # Interactive agentic REPL
    python cli.py code "task" -y         # Auto-continue (non-interactive)
    python cli.py chat                   # Interactive chat session
    python cli.py brief                  # Daily digest / morning briefing
    python cli.py brief --save           # Save digest to data/digests/
    python cli.py brain stats            # Show brain statistics
    python cli.py brain search "React"   # Full-text search over notes
    python cli.py brain add "insight"    # Manually add a note
    python cli.py brain export           # Dump brain to markdown
    python cli.py brain lint             # Run health check
    python cli.py persona list           # Show available personas
    python cli.py persona set <name>      # Switch active persona
    python cli.py watch start             # Start file/git event watcher
    python cli.py watch stop              # Stop event watcher
    python cli.py watch status            # Show watcher status
    python cli.py watch install-hook      # Install git post-commit hook
    python cli.py config show            # Print resolved config
    python cli.py config validate        # Check for missing/invalid settings
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import AppConfig, load_config, validate_config
from execution_log import (
    append_entry as _log_entry,
    clear_log as _clear_log,
    set_db_path as _set_log_db_path,
)
from persona_loader import load_persona  # ADD THIS IMPORT


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _needs_copilot_session(agent) -> bool:
    """Check if the agent is a GitHubCopilotAgent requiring ``async with``.

    Returns False when the SDK module is mocked (tests) or unavailable.
    """
    try:
        from agent_framework_github_copilot import GitHubCopilotAgent
        return isinstance(agent, GitHubCopilotAgent)
    except (ImportError, TypeError):
        return False

def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )


def _load_brain_sync(config: AppConfig):
    """Load brain synchronously (for CLI commands)."""
    from second_brain import load_brain
    return asyncio.run(load_brain(config.state_file))


# ──────────────────────────────────────────────────────────────────────
# Subcommand handlers
# ──────────────────────────────────────────────────────────────────────

def cmd_run(args: argparse.Namespace, config: AppConfig) -> None:
    """Start the heartbeat scheduler, or run a single heartbeat."""
    from scheduler import run_scheduler

    errors = validate_config(config)
    if errors:
        for err in errors:
            print(f"Config error: {err}", file=sys.stderr)
        sys.exit(1)

    if args.once:
        # Run exactly one heartbeat then exit
        from persona_loader import load_persona
        from second_brain import load_brain, save_brain
        from state import load_state, save_state
        from workflow import run_heartbeat
        from agent_setup import create_agent
        from learning import build_context_block
        from second_brain import build_brain_summary, decay_stale_notes
        from goals import auto_expire_goals, build_goals_block

        async def _single_heartbeat():
            persona = load_persona(config.persona)
            state = await load_state(config.state_file)
            brain = await load_brain(config.state_file)
            decay_stale_notes(brain)
            auto_expire_goals(state)
            state.execution_count += 1
            state.last_heartbeat = datetime.now(timezone.utc).isoformat()

            base_instructions = config.agent_instructions or persona.instructions
            context_block = build_context_block(state)
            brain_block = build_brain_summary(brain, max_notes=5)
            goals_block = build_goals_block(state)
            enriched = (
                f"{base_instructions}\n\n{context_block}\n\n{brain_block}"
                + (f"\n\n{goals_block}" if goals_block else "")
            )

            agent = create_agent(config, enriched, tools=persona.tools, mcp_servers=persona.mcp_servers)

            if _needs_copilot_session(agent):
                async with agent:
                    await run_heartbeat(agent, state, brain, config, persona)
            else:
                await run_heartbeat(agent, state, brain, config, persona)

            await save_state(state, config.state_file, config.max_history)
            await save_brain(brain, config.state_file)
            print(f"Heartbeat #{state.execution_count} completed.")

        asyncio.run(_single_heartbeat())
    else:
        try:
            asyncio.run(run_scheduler(config))
        except KeyboardInterrupt:
            logging.getLogger(__name__).info("Shutting down (Ctrl+C).")


def cmd_brain_stats(args: argparse.Namespace, config: AppConfig) -> None:
    """Show brain statistics."""
    brain = _load_brain_sync(config)

    categories: dict[str, int] = {}
    for note in brain.notes.values():
        cat = note.get("category", "resources")
        categories[cat] = categories.get(cat, 0) + 1

    print(f"Notes:       {len(brain.notes)}")
    print(f"Connections: {len(brain.connections)}")
    print(f"Reviews:     {len(brain.review_log)}")
    print(f"Last review: {brain.last_review or 'never'}")
    if categories:
        print(f"Categories:  {', '.join(f'{k}={v}' for k, v in sorted(categories.items()))}")


def cmd_brain_search(args: argparse.Namespace, config: AppConfig) -> None:
    """Full-text search over brain notes."""
    brain = _load_brain_sync(config)
    query = args.query.lower()
    hits = []
    for nid, note in brain.notes.items():
        content = note.get("content", "").lower()
        summary = note.get("summary", "").lower()
        tags = " ".join(note.get("tags", [])).lower()
        if query in content or query in summary or query in tags:
            hits.append((nid, note))

    if not hits:
        print(f"No notes matching '{args.query}'")
        return

    print(f"Found {len(hits)} matching note(s):\n")
    for nid, note in hits:
        summary = note.get("summary") or note.get("content", "")[:80]
        tags = ", ".join(note.get("tags", []))
        cat = note.get("category", "resources")
        print(f"  [{nid}] ({cat}) {summary}")
        if tags:
            print(f"    tags: {tags}")


def cmd_brain_add(args: argparse.Namespace, config: AppConfig) -> None:
    """Manually add a note to the brain."""
    from second_brain import add_note, load_brain, save_brain

    async def _add():
        brain = await load_brain(config.state_file)
        nid = add_note(
            brain,
            content=args.content,
            summary=args.content[:80],
            source={"type": "manual", "timestamp": datetime.now(timezone.utc).isoformat()},
            tags=args.tags.split(",") if args.tags else [],
            category=args.category,
        )
        await save_brain(brain, config.state_file)
        print(f"Added note {nid}")

    asyncio.run(_add())


def cmd_brain_export(args: argparse.Namespace, config: AppConfig) -> None:
    """Export brain contents to markdown."""
    brain = _load_brain_sync(config)
    lines = [
        "# NATLClaw Brain Export",
        f"*Exported: {datetime.now(timezone.utc).isoformat()}*",
        f"\nNotes: {len(brain.notes)} | Connections: {len(brain.connections)} "
        f"| Reviews: {len(brain.review_log)}",
        "",
    ]

    # Group by category
    by_cat: dict[str, list[tuple[str, dict]]] = {}
    for nid, note in brain.notes.items():
        cat = note.get("category", "resources")
        by_cat.setdefault(cat, []).append((nid, note))

    for cat, notes in sorted(by_cat.items()):
        lines.append(f"## {cat.title()} ({len(notes)} notes)\n")
        for nid, note in notes:
            summary = note.get("summary") or note.get("content", "")[:80]
            tags = ", ".join(note.get("tags", []))
            lines.append(f"### {nid}: {summary}\n")
            lines.append(note.get("content", ""))
            if tags:
                lines.append(f"\n*Tags: {tags}*")
            lines.append(f"*Created: {note.get('created_at', '?')}*\n")

    if brain.connections:
        lines.append("## Connections\n")
        for c in brain.connections:
            lines.append(f"- **{c['from']}** ↔ **{c['to']}**: {c.get('reason', '')}")

    output = "\n".join(lines)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Exported to {args.output}")
    else:
        print(output)


def cmd_brain_lint(args: argparse.Namespace, config: AppConfig) -> None:
    """Run brain health check."""
    from second_brain import lint_brain

    brain = _load_brain_sync(config)
    issues = lint_brain(brain)

    if not issues:
        print("Brain is healthy — no issues found.")
        return

    warnings = [i for i in issues if i["severity"] == "warning"]
    infos = [i for i in issues if i["severity"] == "info"]
    print(f"Found {len(warnings)} warning(s) and {len(infos)} info issue(s):\n")
    for issue in issues:
        prefix = "WARNING" if issue["severity"] == "warning" else "INFO"
        nid = issue.get("note_id") or "global"
        print(f"  [{prefix}] {issue['type']}: {issue['message']} ({nid})")


def cmd_persona_list(args: argparse.Namespace, config: AppConfig) -> None:
    """Show available personas."""
    from persona_loader import list_personas, load_persona

    names = list_personas()
    print(f"Available personas ({len(names)}):\n")
    for name in sorted(names):
        try:
            p = load_persona(name)
            wf = p.workflow
            tools_count = len(p.tools)
            desc = p.description[:60] if p.description else "(no description)"
            active = " ← active" if name == config.persona else ""
            print(f"  {name:<20s} [{wf}] {desc} ({tools_count} tools){active}")
        except Exception as e:
            print(f"  {name:<20s} (failed to load: {e})")


def cmd_persona_set(args: argparse.Namespace, config: AppConfig) -> None:
    """Switch the active persona by updating .env."""
    from persona_loader import list_personas

    name = args.name
    available = list_personas()
    if name not in available:
        print(f"Unknown persona '{name}'.")
        print(f"Available: {', '.join(sorted(available))}")
        sys.exit(1)

    env_path = Path(args.env) if hasattr(args, "env") and args.env else Path(".env")
    if env_path.exists():
        content = env_path.read_text(encoding="utf-8")
    else:
        content = ""

    import re as _re
    if _re.search(r"^PERSONA=", content, _re.MULTILINE):
        content = _re.sub(r"^PERSONA=.*$", f"PERSONA={name}", content, flags=_re.MULTILINE)
    else:
        # Append under Agent section if present, otherwise at end
        if "\n# Agent" in content:
            content = content.replace("\n# Agent\n", f"\n# Agent\nPERSONA={name}\n", 1)
        else:
            content = content.rstrip("\n") + f"\n\nPERSONA={name}\n"

    env_path.write_text(content, encoding="utf-8")
    print(f"Active persona set to '{name}'.")


def cmd_watch_start(args: argparse.Namespace, config: AppConfig) -> None:
    """Start the background file/git event watcher."""
    from event_watcher import start_background_watcher
    watch_path = getattr(args, "path", ".")
    start_background_watcher(watch_path)


def cmd_watch_stop(args: argparse.Namespace, config: AppConfig) -> None:
    """Stop the background file/git event watcher."""
    from event_watcher import stop_background_watcher
    stop_background_watcher()


def cmd_watch_status(args: argparse.Namespace, config: AppConfig) -> None:
    """Show whether the event watcher is running."""
    from event_watcher import is_watcher_running, _read_pid, EVENT_QUEUE_PATH
    if is_watcher_running():
        print(f"Watcher is RUNNING (PID {_read_pid()}).")
    else:
        print("Watcher is NOT running.")
    if EVENT_QUEUE_PATH.exists():
        lines = EVENT_QUEUE_PATH.read_text(encoding="utf-8").strip().splitlines()
        print(f"Event queue: {len(lines)} pending event(s).")
    else:
        print("Event queue: empty.")


def cmd_watch_install_hook(args: argparse.Namespace, config: AppConfig) -> None:
    """Install the git post-commit hook."""
    from event_watcher import install_git_hook
    repo_path = getattr(args, "path", ".")
    result = install_git_hook(repo_path)
    print(result)


def cmd_brief(args: argparse.Namespace, config: AppConfig) -> None:
    """Print a daily digest / morning briefing."""
    from daily_digest import build_digest, save_digest
    from second_brain import load_brain
    from state import load_state

    brain = asyncio.run(load_brain(config.state_file))
    state = asyncio.run(load_state(config.state_file))
    persona = load_persona(config.persona)

    digest = build_digest(
        brain,
        state.last_heartbeat,
        persona_name=persona.name,
    )
    print(digest)

    if getattr(args, "save", False):
        path = save_digest(digest)
        print(f"\nSaved to {path}")


def cmd_config_show(args: argparse.Namespace, config: AppConfig) -> None:
    """Print resolved configuration."""
    from dataclasses import asdict
    for key, val in asdict(config).items():
        # Mask sensitive values
        if "key" in key.lower() or "secret" in key.lower():
            display = "***" if val else "(empty)"
        else:
            display = val
        print(f"  {key}: {display}")


def cmd_config_validate(args: argparse.Namespace, config: AppConfig) -> None:
    """Check for configuration errors."""
    errors = validate_config(config)
    if errors:
        print(f"Configuration has {len(errors)} error(s):\n")
        for err in errors:
            print(f"  ERROR: {err}")
        sys.exit(1)
    else:
        print("Configuration is valid.")


# ──────────────────────────────────────────────────────────────────────
# Agentic task mode (used by `natl code`)
# ──────────────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────────
# Agentic task mode (used by `natl code`)
# ──────────────────────────────────────────────────────────────────────

# Legacy constants — kept for backward-compatibility & tests.
# At runtime, cmd_code reads these from the active persona instead.
_DONE_MARKER = "[TASK_COMPLETE]"
_BLOCKED_MARKER = "[TASK_BLOCKED]"
_DEFAULT_MAX_TURNS = 20


def _describe_tools(tools: list) -> str:
    """Build a human-readable list of tool names + docstrings."""
    lines: list[str] = []
    for fn in tools:
        name = getattr(fn, "__name__", str(fn))
        doc = (getattr(fn, "__doc__", "") or "").split("\n")[0]
        lines.append(f"- `{name}` — {doc}")
    return "\n".join(lines) or "(no tools attached)"


def cmd_code(args: argparse.Namespace, config: AppConfig) -> None:
    """Run the agentic task mode — one-shot task or interactive REPL."""
    from second_brain import (
        add_note, build_brain_summary, load_brain, save_brain,
    )
    from state import load_state, save_state, AgentState
    from agent_setup import create_agent
    from learning import build_context_block
    from prompts import load_prompt
    from agent_framework import AgentSession

    # ------------------------------------------------------------------
    # Initialise state, brain, persona, agent
    # ------------------------------------------------------------------
    persona = load_persona(args.persona or config.persona)
    state: AgentState = asyncio.run(load_state(config.state_file))
    brain = asyncio.run(load_brain(config.state_file))
    cwd = args.cwd or "."

    # Read agentic config from persona (with legacy defaults)
    prompt_mode = persona.prompt_dir or "coding_agent"
    done_marker = persona.done_marker
    blocked_marker = persona.blocked_marker

    brain_summary = build_brain_summary(brain, max_notes=5)
    tools_desc = _describe_tools(persona.tools)

    system_prompt = load_prompt(
        prompt_mode, "system",
        agent_name=config.agent_name,
        cwd=cwd,
        persona_name=persona.name,
        persona_description=persona.description,
        brain_summary=brain_summary,
        tools_description=tools_desc,
        done_marker=done_marker,
        blocked_marker=blocked_marker,
    )
    if not system_prompt:
        # Fallback if template missing
        system_prompt = (
            f"You are {config.agent_name}, an agent assisting with tasks. "
            f"Use your tools to complete the work. "
            f"End with {done_marker} when complete."
        )

    agent = create_agent(
        config, system_prompt,
        tools=persona.tools,
        mcp_servers=persona.mcp_servers,
    )

    max_turns: int = args.max_turns or persona.max_turns
    auto_approve: bool = args.yes

    # ------------------------------------------------------------------
    # Helper: run a coroutine — reuses existing loop or creates one
    # ------------------------------------------------------------------
    def _run_async(coro):
        """Run a coroutine, reusing the running event loop if present."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            # Already inside asyncio.run() (Copilot session) — create a task
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()
        return asyncio.run(coro)

    # ------------------------------------------------------------------
    # Core agentic loop — returns final summary text
    # ------------------------------------------------------------------
    def _run_agent_loop(task: str) -> str:
        """Send task to agent and let it iterate with tools until done."""
        task_prompt = load_prompt(prompt_mode, "task", task=task) or task
        turns: list[str] = []

        print(f"\n{'─'*60}")
        print(f"  Task: {task}")
        print(f"  Persona: {persona.name}  |  Max turns: {max_turns}")
        print(f"{'─'*60}\n")

        async def _loop() -> str:
            nonlocal turns
            current_prompt = task_prompt
            session = AgentSession()  # Persists context across turns

            for turn in range(1, max_turns + 1):
                print(f"[turn {turn}/{max_turns}] Thinking...", flush=True)
                try:
                    response = await agent.run(current_prompt, session=session)
                    text = response.text if hasattr(response, "text") else str(response)
                except Exception as e:
                    print(f"  Agent error: {e}")
                    turns.append(f"Turn {turn}: ERROR — {e}")
                    break

                turns.append(text)

                # Print agent output (trimmed)
                display = text if len(text) < 2000 else text[:2000] + "\n... (truncated)"
                print(f"\n{display}\n")

                # Check for completion markers
                if done_marker in text:
                    print("Task complete.")
                    break
                if blocked_marker in text:
                    print("Agent reports it is blocked.")
                    break

                # In interactive mode, ask if user wants to continue
                if not auto_approve and turn < max_turns:
                    try:
                        choice = input(
                            "[c]ontinue / [m]essage / [s]top  (c): "
                        ).strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        print("\nStopping.")
                        break

                    if choice in ("s", "stop"):
                        break
                    elif choice in ("m", "message"):
                        try:
                            msg = input("You: ").strip()
                        except (EOFError, KeyboardInterrupt):
                            break
                        if msg:
                            followup = load_prompt(
                                prompt_mode, "followup", message=msg,
                            ) or msg
                            current_prompt = followup
                            continue
                    # default: continue — let agent keep going
                    current_prompt = f"Continue. If you are done, respond with {done_marker}."
                else:
                    current_prompt = f"Continue. If you are done, respond with {done_marker}."
            else:
                print(f"\nReached max turns ({max_turns}). Stopping.")

            return "\n---\n".join(turns[-3:])  # last 3 turns as summary

        return _run_async(_loop())

    # ------------------------------------------------------------------
    # Capture insight to brain after task
    # ------------------------------------------------------------------
    def _capture_insight(task_result: str) -> None:
        capture_prompt = load_prompt(
            prompt_mode, "capture", task_result=task_result[:1500],
        )
        if not capture_prompt:
            return
        try:
            async def _cap():
                resp = await agent.run(capture_prompt)
                return resp.text if hasattr(resp, "text") else str(resp)

            raw = _run_async(_cap())

            import json as _json
            text = raw.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                text = text.rsplit("```", 1)[0]
            data = _json.loads(text)
            nid = add_note(
                brain,
                content=data.get("content", ""),
                summary=data.get("topic", ""),
                source={"type": "task_agent", "persona": persona.name, "timestamp": datetime.now(timezone.utc).isoformat()},
                tags=data.get("tags", []),
                category=data.get("category", "resources"),
            )
            _run_async(save_brain(brain, config.state_file))
            print(f"  Captured insight → {nid}")
        except Exception as e:
            logging.getLogger(__name__).debug("Capture failed: %s", e)

    # ------------------------------------------------------------------
    # Save state helper
    # ------------------------------------------------------------------
    def _save() -> None:
        state.execution_count += 1
        state.last_heartbeat = datetime.now(timezone.utc).isoformat()
        _run_async(save_state(state, config.state_file, config.max_history))

    # ------------------------------------------------------------------
    # Unified runner — wraps all work in a single Copilot session if needed
    # ------------------------------------------------------------------
    def _run_one_task(task: str) -> None:
        result = _run_agent_loop(task)
        _capture_insight(result)
        _save()

    def _run_repl() -> None:
        print(f"\n{'='*60}")
        print(f"  {config.agent_name} — {persona.description or persona.name}")
        print(f"  Type a task, or /exit to quit")
        print(f"{'='*60}\n")

        while True:
            try:
                task = input("task> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye!")
                break
            if not task:
                continue
            if task.lower() in ("/exit", "/quit", "exit", "quit"):
                print("Goodbye!")
                break
            if task.startswith("/help"):
                print("  Type a task and the agent will execute it.")
                print("  /exit  — quit")
                print("  /brain — show brain knowledge")
                print("  /help  — show this message\n")
                continue
            if task.startswith("/brain"):
                print(build_brain_summary(brain, max_notes=8))
                print()
                continue

            result = _run_agent_loop(task)
            _capture_insight(result)
            _save()
            print()

    # ------------------------------------------------------------------
    # Entry: open ONE session for the entire cmd_code lifetime
    # ------------------------------------------------------------------
    if _needs_copilot_session(agent):
        async def _copilot_session():
            async with agent:
                if args.task:
                    _run_one_task(args.task)
                else:
                    _run_repl()
        asyncio.run(_copilot_session())
    else:
        if args.task:
            _run_one_task(args.task)
        else:
            _run_repl()


# ──────────────────────────────────────────────────────────────────────
# Chat interface
# ──────────────────────────────────────────────────────────────────────

def cmd_chat(args: argparse.Namespace, config: AppConfig) -> None:
    """Start an interactive chat session with the agent."""
    from second_brain import load_brain, save_brain, add_note, search_notes
    from state import load_state, save_state
    from agent_setup import create_agent
    from learning import build_context_block
    from second_brain import build_brain_summary, build_lint_block
    from goals import build_goals_block, get_active_goals
    from state import AgentState
    from agent_framework import AgentSession, tool
    from typing import Annotated

    def _run_async(coro):
        """Run a coroutine, reusing the running event loop if present."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()
        return asyncio.run(coro)

    def chat_loop():
        state = _run_async(load_state(config.state_file))
        brain = _run_async(load_brain(config.state_file))
        persona = load_persona(config.persona)

        # -- Memory tools the agent can call during chat ------------------
        @tool
        def remember(
            content: Annotated[str, "The fact, preference, or information to remember"],
            tags: Annotated[str, "Comma-separated tags (e.g. 'user-pref,name')"] = "chat",
        ) -> str:
            """Save a piece of information to your long-term memory (Second Brain).
            Use this whenever the user asks you to remember something, shares a
            preference, or tells you something worth keeping for future reference."""
            nonlocal brain
            brain = _run_async(load_brain(config.state_file))  # reload latest
            now = datetime.now(timezone.utc).isoformat()
            tag_list = [t.strip() for t in tags.split(",") if t.strip()]

            # Dedup check — avoid storing the same fact twice
            existing = search_notes(brain, content)
            for note in existing[:3]:
                from second_brain import _token_overlap
                if _token_overlap(content, note.get("content", "")) > 0.7:
                    return f"Already remembered (note {note['id']}): {note['content'][:100]}"

            note_id = add_note(
                brain,
                content=content,
                summary=content[:80],
                source={"type": "chat_tool", "timestamp": now},
                tags=tag_list,
                category="resources",
            )
            _run_async(save_brain(brain, config.state_file))
            return f"Remembered (note {note_id}): {content[:100]}"

        @tool
        def recall(
            query: Annotated[str, "Search term or topic to look up in memory"],
        ) -> str:
            """Search your long-term memory (Second Brain) for information.
            Use this when the user asks what you know, or when you need to look
            up a previously stored fact, preference, or piece of knowledge."""
            nonlocal brain
            brain = _run_async(load_brain(config.state_file))  # reload latest
            results = search_notes(brain, query)
            if not results:
                return f"No memories found matching '{query}'."
            lines = [f"Found {len(results)} memory note(s):"]
            for note in results[:8]:
                tags = ", ".join(note.get("tags", []))
                tag_str = f" [{tags}]" if tags else ""
                lines.append(f"  - {note['content'][:200]}{tag_str}")
            return "\n".join(lines)

        # Combine persona tools with memory tools
        chat_tools = list(persona.tools or []) + [remember, recall]

        base_instructions = config.agent_instructions or persona.instructions
        agent = create_agent(config, base_instructions, tools=chat_tools, mcp_servers=persona.mcp_servers)
        session = AgentSession()  # Persists conversation history across messages
        
        print(f"\n{'='*60}")
        print(f"Chatting as: {config.agent_name}")
        print(f"Knowledge base: {len(brain.notes)} notes")
        print(f"Active goals: {len(get_active_goals(state))}")
        print(f"{'='*60}\n")
        print("Type '/exit' to quit, '/help' for commands, '/brain' for knowledge summary\n")
        
        def _repl_loop():
            nonlocal state, brain, base_instructions
            while True:
                try:
                    print("Waiting for input...", flush=True)
                    user_input = sys.stdin.readline().strip()
                    if not user_input:
                        continue
                    
                    # Handle special commands
                    if user_input.lower() in ['/exit', '/quit', 'exit', 'quit']:
                        print("\n👋 Goodbye! Happy automating!")
                        break
                    elif user_input.startswith('/clear'):
                        _clear_log()
                        nonlocal session
                        session = AgentSession()  # Reset conversation history
                        print("🧹 Conversation history cleared.")
                        continue
                    elif user_input.startswith('/brain'):
                        summary = build_brain_summary(brain, max_notes=8)
                        print(f"\n🧠 Second Brain:\n{summary}\n")
                        continue
                    elif user_input.startswith('/goals'):
                        goals_block = build_goals_block(state)
                        if goals_block:
                            print(f"\n🎯 Active Goals:\n{goals_block}\n")
                        else:
                            print("\n📭 No active goals.\n")
                        continue
                    elif user_input.startswith('/help'):
                        print("\n💬 Interactive Chat Commands:")
                        print("  /exit    - Exit the chat")
                        print("  /clear   - Clear conversation history")
                        print("  /brain   - Show knowledge base summary")
                        print("  /goals   - Show active goals")
                        print("  /help    - Show this help")
                        print("  /add <text> - Add note to brain (no quotes needed)")
                        print("\nYou can also just type normally to chat with the agent.\n")
                        continue
                    elif user_input.startswith('/add '):
                        # Add insight to brain - extract content after /add
                        content = user_input[5:].strip()
                        if content:
                            from second_brain import add_note
                            now = datetime.now(timezone.utc).isoformat()
                            note_id = add_note(
                                brain,
                                content=content,
                                summary=content[:80],
                                source={"type": "manual_chat", "timestamp": now},
                                tags=["chat"],
                                category="resources",
                            )
                            _run_async(save_brain(brain, config.state_file))
                            print(f"✅ Added note {note_id} to brain")
                        continue
                    
                    # Build agent context (reload brain from disk for heartbeat notes)
                    brain = _run_async(load_brain(config.state_file))
                    context_block = build_context_block(state)
                    brain_block = build_brain_summary(brain, max_notes=8)
                    goals_block = build_goals_block(state)
                    lint_block = build_lint_block(brain) if state.execution_count % 10 == 0 else ""

                    # Pre-load user-preference notes into context
                    user_prefs = search_notes(brain, "user name preference")
                    pref_lines = []
                    for note in user_prefs[:5]:
                        tags = note.get("tags", [])
                        src = note.get("source", "")
                        src_type = src.get("type", "") if isinstance(src, dict) else str(src)
                        is_user_fact = (
                            any(t in tags for t in ("user-pref", "name", "preference"))
                            or src_type == "chat_tool"
                        )
                        if is_user_fact:
                            pref_lines.append(f"  - {note['content'][:200]}")
                    pref_block = ""
                    if pref_lines:
                        pref_block = "\n\nKnown facts about the user:\n" + "\n".join(pref_lines)
                    
                    enriched = base_instructions + "\n\n" + context_block + "\n\n" + brain_block
                    enriched += pref_block
                    enriched += (
                        "\n\nIMPORTANT — Memory tools:\n"
                        "You have `remember` and `recall` tools for your Second Brain.\n"
                        "- If the user asks \"do you remember\", \"what did I say\", "
                        "\"what's my name\", or references ANY past conversation, "
                        "you MUST call `recall` FIRST before answering.\n"
                        "- When the user shares a fact, preference, or asks you to "
                        "remember something, ALWAYS call `remember`.\n"
                        "- Before saving with `remember`, use `recall` to check if the "
                        "fact already exists to avoid duplicates."
                    )
                    if goals_block:
                        enriched += "\n\n" + goals_block
                    if lint_block:
                        enriched += "\n\n" + lint_block
                    agent.instructions = enriched
                    
                    # Send message to agent
                    print("\nAgent: Thinking...")
                    try:
                        response = _run_async(agent.run(user_input, session=session))
                        text = response.text if hasattr(response, "text") else str(response)
                    except Exception as e:
                        print(f"\nError running agent: {str(e)}\n")
                        continue
                    
                    # Record the interaction (full text — no truncation)
                    now = datetime.now(timezone.utc).isoformat()
                    state.execution_count += 1
                    state.last_heartbeat = now
                    _log_entry("chat_message", user_input, text)
                    
                    print(f"\nAgent: {text}\n")
                    
                    # Save state and brain after each exchange
                    _run_async(save_state(state, config.state_file, config.max_history))
                    
                except KeyboardInterrupt:
                    print("\n\n👋 Goodbye!")
                    break
                except Exception as e:
                    print(f"\nError: {str(e)}\n")
                    # Continue the loop even if error occurs

        # Open a single Copilot session for the entire chat
        if _needs_copilot_session(agent):
            async def _copilot_chat():
                async with agent:
                    _repl_loop()
            _run_async(_copilot_chat())
        else:
            _repl_loop()
    
    # Run the chat loop
    chat_loop()


# ──────────────────────────────────────────────────────────────────────
# Argument parser
# ──────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="natl",
        description="NATLClaw — Autonomous Second-Brain Agent",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument("--env", default=".env", help="Path to .env file (default: .env)")
    sub = parser.add_subparsers(dest="command", help="Subcommand")

    # run
    run_p = sub.add_parser("run", help="Start the heartbeat scheduler")
    run_p.add_argument("--once", action="store_true", help="Run a single heartbeat and exit")

    # chat (NEW)
    chat_p = sub.add_parser("chat", help="Start an interactive chat session with the agent")

    # brief — daily digest
    brief_p = sub.add_parser("brief", help="Print a daily digest / morning briefing")
    brief_p.add_argument("--save", action="store_true",
                         help="Also save digest to data/digests/YYYY-MM-DD.md")

    # code — agentic task mode
    code_p = sub.add_parser("code", help="Execute tasks with tools using the active persona")
    code_p.add_argument("task", nargs="?", default=None,
                        help="Task to execute (omit for interactive REPL)")
    code_p.add_argument("--persona", default=None,
                        help="Persona to use (default: from config or python_developer)")
    code_p.add_argument("--cwd", default=None,
                        help="Working directory (default: current directory)")
    code_p.add_argument("--max-turns", type=int, default=None,
                        help=f"Max agent turns before stopping (default: {_DEFAULT_MAX_TURNS})")
    code_p.add_argument("-y", "--yes", action="store_true",
                        help="Auto-continue without asking (non-interactive)")

    # brain
    brain_p = sub.add_parser("brain", help="Brain management commands")
    brain_sub = brain_p.add_subparsers(dest="brain_command")

    brain_sub.add_parser("stats", help="Show brain statistics")

    search_p = brain_sub.add_parser("search", help="Full-text search over notes")
    search_p.add_argument("query", help="Search query")

    add_p = brain_sub.add_parser("add", help="Manually add a note")
    add_p.add_argument("content", help="Note content")
    add_p.add_argument("--tags", default="", help="Comma-separated tags")
    add_p.add_argument("--category", default="resources", help="PARA category")

    export_p = brain_sub.add_parser("export", help="Export brain to markdown")
    export_p.add_argument("-o", "--output", help="Output file path (default: stdout)")

    brain_sub.add_parser("lint", help="Run brain health check")

    # persona
    persona_p = sub.add_parser("persona", help="Persona management")
    persona_sub = persona_p.add_subparsers(dest="persona_command")
    persona_sub.add_parser("list", help="Show available personas")
    set_p = persona_sub.add_parser("set", help="Switch active persona")
    set_p.add_argument("name", help="Name of the persona to activate")

    # watch
    watch_p = sub.add_parser("watch", help="File/git event watcher")
    watch_sub = watch_p.add_subparsers(dest="watch_command")
    start_w = watch_sub.add_parser("start", help="Start background file watcher")
    start_w.add_argument("path", nargs="?", default=".", help="Directory to watch (default: current)")
    watch_sub.add_parser("stop", help="Stop background file watcher")
    watch_sub.add_parser("status", help="Show watcher status and queue size")
    hook_p = watch_sub.add_parser("install-hook", help="Install git post-commit hook")
    hook_p.add_argument("path", nargs="?", default=".", help="Git repo path (default: current)")

    # config
    config_p = sub.add_parser("config", help="Configuration management")
    config_sub = config_p.add_subparsers(dest="config_command")
    config_sub.add_parser("show", help="Print resolved config")
    config_sub.add_parser("validate", help="Check for config errors")
    
    return parser


# ──────────────────────────────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    config = load_config(args.env)

    # Point the execution log at the same data directory as the state file
    import os as _os
    _set_log_db_path(_os.path.join(_os.path.dirname(config.state_file), "execution_log.db"))

    dispatch = {
        "run": cmd_run,
        "chat": cmd_chat,
        "brief": cmd_brief,
        "code": cmd_code,
        "brain": {
            "stats": cmd_brain_stats,
            "search": cmd_brain_search,
            "add": cmd_brain_add,
            "export": cmd_brain_export,
            "lint": cmd_brain_lint,
        },
        "persona": {
            "list": cmd_persona_list,
            "set": cmd_persona_set,
        },
        "watch": {
            "start": cmd_watch_start,
            "stop": cmd_watch_stop,
            "status": cmd_watch_status,
            "install-hook": cmd_watch_install_hook,
        },
        "config": {
            "show": cmd_config_show,
            "validate": cmd_config_validate,
        },
    }

    cmd = args.command
    if cmd is None:
        parser.print_help()
        return

    handler = dispatch.get(cmd)
    if isinstance(handler, dict):
        subcmd = getattr(args, f"{cmd}_command", None)
        if subcmd is None:
            parser.parse_args([cmd, "--help"])
            return
        handler = handler.get(subcmd)

    if handler is None:
        parser.print_help()
        return

    handler(args, config)


if __name__ == "__main__":
    main()
