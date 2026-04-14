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
    python cli.py report                   # Run workspace audit report
    python cli.py report --save            # Save report to data/reports/
    python cli.py brain stats            # Show brain statistics
    python cli.py brain show n0001       # Inspect one note in detail
    python cli.py brain search "React"   # Full-text search over notes
    python cli.py brain topics           # Show the most connected topics
    python cli.py brain trace React      # Traverse notes reachable from a topic
    python cli.py brain feedback n0001 --relevant   # Reinforce a memory
    python cli.py brain contradict n0002 n0005      # Demote a contradicted memory
    python cli.py brain add "insight"    # Manually add a note
    python cli.py brain export           # Dump brain to markdown
    python cli.py brain lint             # Run health check
    python cli.py inbox list                # Show unread messages
    python cli.py inbox list -a            # Show all messages
    python cli.py inbox show m1a2b3        # View message detail (marks read)
    python cli.py inbox dismiss m1a2b3     # Dismiss a message
    python cli.py inbox dismiss -a         # Dismiss all read messages
    python cli.py inbox clear              # Clear all messages
    python cli.py serve                         # Start API server + dashboard
    python cli.py serve --port 9000             # Custom port
    python cli.py task add "Fix the login bug" -p high   # Create a task
    python cli.py task list                # List all tasks
    python cli.py task list -s blocked     # List blocked tasks
    python cli.py task status t1a2b3       # Show task details
    python cli.py task answer t1a2b3 "Use OAuth2"  # Unblock a task
    python cli.py task cancel t1a2b3               # Cancel a task
    python cli.py task retry t1a2b3                # Retry a failed/blocked task
    python cli.py persona list           # Show available personas
    python cli.py persona set <name>      # Switch active persona
    python cli.py watch start             # Start file/git event watcher
    python cli.py watch stop              # Stop event watcher
    python cli.py watch status            # Show watcher status
    python cli.py watch install-hook      # Install git post-commit hook
    python cli.py config show            # Print resolved config
    python cli.py config validate        # Check for missing/invalid settings
    python cli.py api                    # Start HTTP API server
    python cli.py api --port 9000        # Custom port
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

def cmd_serve(args: argparse.Namespace, config: AppConfig) -> None:
    """Start the API server with embedded dashboard."""
    from api_server import create_app
    import uvicorn

    app = create_app(config)
    host = getattr(args, "host", "0.0.0.0")
    port = getattr(args, "port", 8000)
    print(f"Starting NATLClaw API server on http://{host}:{port}")
    print(f"  Dashboard: http://localhost:{port}/")
    print(f"  OpenAI API: http://localhost:{port}/v1/chat/completions")
    print(f"  Tasks API: http://localhost:{port}/api/tasks")
    uvicorn.run(app, host=host, port=port)


def cmd_run(args: argparse.Namespace, config: AppConfig) -> None:
    """Start the heartbeat scheduler, or run a single heartbeat."""
    from scheduler import run_scheduler

    errors = validate_config(config)
    if errors:
        for err in errors:
            print(f"Config error: {err}", file=sys.stderr)
        sys.exit(1)

    if args.once:
        # Run exactly one heartbeat via the real scheduler loop (full fidelity)
        try:
            asyncio.run(run_scheduler(config, max_iterations=1))
        except KeyboardInterrupt:
            pass
    else:
        try:
            asyncio.run(run_scheduler(config))
        except KeyboardInterrupt:
            logging.getLogger(__name__).info("Shutting down (Ctrl+C).")


def cmd_brain_stats(args: argparse.Namespace, config: AppConfig) -> None:
    """Show brain statistics."""
    from second_brain import build_brain_stats_from_store

    stats = build_brain_stats_from_store(config.state_file)

    print(f"Notes:             {stats['notes']}")
    print(f"Wiki pages:        {stats['pages']}")
    print(f"Topics:            {stats['topics']}")
    print(f"Connections:       {stats['connections']}")
    print(f"Reviews:           {stats['reviews']}")
    print(f"Pending consolidate: {stats['unconsolidated']}")
    print(f"Orphans:           {stats['orphans']}")
    print(f"Connection density: {stats['connection_density']:.2f}")
    print(f"Last review:       {stats['last_review']}")
    print(f"Last consolidation: {stats['last_consolidation']}")
    if stats["categories"]:
        print(f"Categories:        {', '.join(f'{k}={v}' for k, v in sorted(stats['categories'].items()))}")
    if stats["note_types"]:
        print(f"Note types:        {', '.join(f'{k}={v}' for k, v in sorted(stats['note_types'].items()))}")
    if stats["statuses"]:
        print(f"Statuses:          {', '.join(f'{k}={v}' for k, v in sorted(stats['statuses'].items()))}")
    if any(stats[key] for key in ("recalls", "positive_feedback", "negative_feedback", "contradictions")):
        print(
            "Memory signals:    "
            f"recalls={stats['recalls']}, "
            f"relevant={stats['positive_feedback']}, "
            f"irrelevant={stats['negative_feedback']}, "
            f"contradictions={stats['contradictions']}"
        )
    if stats["top_topics"]:
        print("Top topics:")
        for topic in stats["top_topics"]:
            print(f"  - {topic['name']} ({topic['notes']} notes, {topic['related']} related)")


def cmd_brain_show(args: argparse.Namespace, config: AppConfig) -> None:
    """Inspect a single note with metadata and related context."""
    from second_brain import describe_note_from_store

    details = describe_note_from_store(config.state_file, args.note_id, record_access=True)
    if details is None:
        print(f"Note '{args.note_id}' not found.")
        return

    print(f"[{details['id']}] {details['summary']}")
    print(f"Type: {details['note_type']} | Status: {details['status']} | Category: {details['category']}")
    if details["confidence"] is not None:
        print(f"Confidence: {details['confidence']}")
    print(f"Created: {details['created_at'] or '?'}")
    print(f"Updated: {details['updated_at'] or '?'}")
    if details["last_accessed_at"]:
        print(f"Last accessed: {details['last_accessed_at']}")
    if details["last_confirmed_at"]:
        print(f"Last confirmed: {details['last_confirmed_at']}")
    print(f"Recall count: {details['recall_count']}")
    if details["positive_feedback"] or details["negative_feedback"]:
        print(
            "Feedback: "
            f"+{details['positive_feedback']} / -{details['negative_feedback']}"
        )
    if details["contradiction_count"]:
        print(f"Contradictions: {details['contradiction_count']}")
    if details["contradicted_by"]:
        print(f"Contradicted by: {', '.join(details['contradicted_by'])}")

    source = details["source"]
    if isinstance(source, dict):
        print("Source:")
        for key, value in source.items():
            print(f"  - {key}: {value}")
    else:
        print(f"Source: {source}")

    if details["tags"]:
        print(f"Tags: {', '.join(details['tags'])}")
    if details["topics"]:
        print(f"Topics: {', '.join(details['topics'])}")
    if details["evidence"]:
        print("Evidence:")
        for item in details["evidence"]:
            print(f"  - {item}")
    if details["source_pages"]:
        print("Source pages:")
        for page in details["source_pages"]:
            print(f"  - {page}")
    if details["feedback_log"]:
        print("Recent feedback:")
        for entry in details["feedback_log"][-3:]:
            label = "relevant" if entry.get("relevant") else "irrelevant"
            suffix = f": {entry['reason']}" if entry.get("reason") else ""
            print(f"  - {entry.get('timestamp', '?')} {label}{suffix}")
    if details["contradiction_log"]:
        print("Recent contradictions:")
        for entry in details["contradiction_log"][-3:]:
            label = entry.get("by_note_id") or "unknown"
            suffix = f": {entry['reason']}" if entry.get("reason") else ""
            print(f"  - {entry.get('timestamp', '?')} by {label}{suffix}")

    print("Content:")
    print(details["content"])

    if details["connected_notes"]:
        print("Connected notes:")
        for note in details["connected_notes"]:
            print(
                f"  - [{note['id']}] ({note['note_type']}/{note['category']}) {note['summary']}"
            )


def cmd_brain_topics(args: argparse.Namespace, config: AppConfig) -> None:
    """Show the top topics in the brain."""
    from second_brain import get_topic_map_from_store

    topics = sorted(
        get_topic_map_from_store(config.state_file),
        key=lambda topic: (topic["notes"], topic["related"], topic["name"].lower()),
        reverse=True,
    )
    limit = max(1, args.limit)
    if not topics:
        print("No topics found.")
        return

    print(f"Top topics ({min(limit, len(topics))}/{len(topics)}):")
    for topic in topics[:limit]:
        print(f"  - {topic['name']} [{topic['id']}] {topic['notes']} note(s), {topic['related']} related topic(s)")


def cmd_brain_trace(args: argparse.Namespace, config: AppConfig) -> None:
    """Trace a topic through the brain graph."""
    from second_brain import trace_topic_from_store

    trace = trace_topic_from_store(
        config.state_file,
        args.topic,
        depth=args.depth,
        limit=args.limit,
        record_access=True,
    )
    if trace is None:
        print(f"Topic '{args.topic}' not found.")
        return

    print(f"Topic trace: {trace['topic']} (depth={trace['depth']})")
    print(f"Topics visited: {len(trace['topics'])}")
    for topic in trace["topics"]:
        print(f"  - {topic['name']} [{topic['id']}] depth={topic['depth']} notes={topic['notes']} related={topic['related']}")

    print(f"Reachable notes: {trace['total_notes']}")
    for note in trace["notes"]:
        tags = ", ".join(note.get("tags", []))
        tag_str = f" [{tags}]" if tags else ""
        print(f"  - [{note['id']}] ({note['note_type']}/{note['category']}) {note['summary']}{tag_str}")


def cmd_brain_search(args: argparse.Namespace, config: AppConfig) -> None:
    """Full-text search over brain notes."""
    from second_brain import search_notes_from_store

    hits = [
        (note.get("id", "?"), note)
        for note in search_notes_from_store(
            config.state_file,
            args.query,
            max_results=args.limit,
            record_access=True,
        )
    ]

    if not hits:
        print(f"No notes matching '{args.query}'")
        return

    print(f"Found {len(hits)} matching note(s):\n")
    for nid, note in hits:
        summary = note.get("summary") or note.get("content", "")[:80]
        tags = ", ".join(note.get("tags", []))
        cat = note.get("category", "resources")
        note_type = note.get("note_type", "general")
        status = note.get("status", "active")
        print(f"  [{nid}] ({note_type}/{cat}, {status}) {summary}")
        if tags:
            print(f"    tags: {tags}")
        if note.get("confidence") is not None:
            print(f"    confidence: {note['confidence']}")


def cmd_brain_feedback(args: argparse.Namespace, config: AppConfig) -> None:
    """Record explicit relevance feedback for a note."""
    from second_brain import apply_relevance_feedback, load_brain, save_brain

    async def _feedback():
        brain = await load_brain(config.state_file)
        relevant = bool(args.relevant)
        if not apply_relevance_feedback(
            brain,
            args.note_id,
            relevant=relevant,
            reason=args.reason,
        ):
            print(f"Note '{args.note_id}' not found.")
            return
        await save_brain(brain, config.state_file)
        signal = "relevant" if relevant else "irrelevant"
        print(f"Recorded {signal} feedback for {args.note_id}.")

    asyncio.run(_feedback())


def cmd_brain_contradict(args: argparse.Namespace, config: AppConfig) -> None:
    """Mark one note as contradicted by another note."""
    from second_brain import load_brain, record_contradiction, save_brain

    async def _contradict():
        brain = await load_brain(config.state_file)
        if args.note_id not in brain.notes:
            print(f"Note '{args.note_id}' not found.")
            return
        if args.by_note_id not in brain.notes:
            print(f"Note '{args.by_note_id}' not found.")
            return
        if not record_contradiction(
            brain,
            args.note_id,
            args.by_note_id,
            reason=args.reason,
            supersede=True if args.supersede else None,
        ):
            print("Could not record contradiction.")
            return
        await save_brain(brain, config.state_file)
        status = brain.notes[args.note_id].get("status", "active")
        print(f"Marked {args.note_id} as contradicted by {args.by_note_id} ({status}).")

    asyncio.run(_contradict())


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
            note_type=args.note_type,
            status=args.status,
            confidence=args.confidence,
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
            lines.append(
                f"\n*Type: {note.get('note_type', 'general')} | "
                f"Status: {note.get('status', 'active')} | "
                f"Category: {note.get('category', 'resources')}*"
            )
            if note.get("confidence") is not None:
                lines.append(f"*Confidence: {note['confidence']}*")
            if tags:
                lines.append(f"\n*Tags: {tags}*")
            evidence = note.get("evidence", [])
            if evidence:
                lines.append("\n*Evidence:*")
                for item in evidence:
                    lines.append(f"- {item}")
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


def cmd_report(args: argparse.Namespace, config: AppConfig) -> None:
    """Run a comprehensive workspace audit using the workspace_observer persona."""
    from agent_setup import create_agent
    from second_brain import build_brain_summary, load_brain
    from state import load_state
    from prompts import load_prompt
    from agent_framework import AgentSession

    persona = load_persona("workspace_observer")
    state = asyncio.run(load_state(config.state_file))
    brain = asyncio.run(load_brain(config.state_file))
    brain_summary = build_brain_summary(brain, max_notes=5)

    # Load the audit prompt template
    prompt = load_prompt(
        "report", "workspace_audit",
        agent_name=config.agent_name,
        brain_summary=brain_summary,
    )
    if not prompt:
        print("Error: prompts/report/workspace_audit.txt not found.", file=sys.stderr)
        sys.exit(1)

    base_instructions = persona.instructions
    enriched = f"{base_instructions}\n\nYou are performing a one-shot workspace audit. Use ALL your tools thoroughly."

    agent = create_agent(
        config, enriched,
        tools=persona.tools,
        mcp_servers=persona.mcp_servers,
    )

    max_turns = getattr(args, "max_turns", None) or 15

    async def _run_audit():
        session = AgentSession()
        report_parts: list[str] = []
        current_prompt = prompt

        for turn in range(1, max_turns + 1):
            print(f"[audit turn {turn}/{max_turns}] Analyzing...", file=sys.stderr, flush=True)
            try:
                response = await agent.run(current_prompt, session=session)
                text = response.text if hasattr(response, "text") else str(response)
            except Exception as e:
                print(f"  Error: {e}", file=sys.stderr)
                break

            report_parts.append(text)

            # If the report contains our expected headings, it's likely complete
            if "## Recommended Priorities" in text or "## Summary" in text:
                break

            current_prompt = (
                "Continue the audit. You have more tools to use. "
                "When done, output the full markdown report."
            )

        return "\n\n".join(report_parts)

    if _needs_copilot_session(agent):
        async def _copilot_audit():
            async with agent:
                return await _run_audit()
        report = asyncio.run(_copilot_audit())
    else:
        report = asyncio.run(_run_audit())

    # Output the report
    print(report)

    # Save if requested
    if getattr(args, "save", False):
        from pathlib import Path as _Path
        from datetime import datetime as _dt, timezone as _tz
        reports_dir = _Path("data") / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{_dt.now(_tz.utc).strftime('%Y-%m-%d_%H%M')}_audit.md"
        out_path = reports_dir / filename
        out_path.write_text(report, encoding="utf-8")
        print(f"\nSaved to {out_path}", file=sys.stderr)


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


def cmd_task_add(args: argparse.Namespace, config: AppConfig) -> None:
    """Create a new task for the agent."""
    from tasks import create_task, load_tasks, save_tasks
    from event_watcher import enqueue_event

    async def _add():
        tasks = await load_tasks(config.state_file)
        task = create_task(
            title=args.title,
            description=args.description or args.title,
            priority=args.priority,
            max_heartbeats=args.max_heartbeats,
        )
        tasks.append(task)
        await save_tasks(tasks, config.state_file)
        enqueue_event("task_created", {"task_id": task.id, "title": task.title})
        print(f"Created task {task.id}: {task.title} (priority={task.priority})")

    asyncio.run(_add())


def cmd_task_list(args: argparse.Namespace, config: AppConfig) -> None:
    """List tasks."""
    from tasks import format_task_list, load_tasks

    async def _list():
        tasks = await load_tasks(config.state_file)
        status_filter = getattr(args, "status", "all") or "all"
        print(format_task_list(tasks, status_filter=status_filter))

    asyncio.run(_list())


def cmd_task_status(args: argparse.Namespace, config: AppConfig) -> None:
    """Show detailed status of a task."""
    from tasks import find_task, format_task_detail, load_tasks

    async def _status():
        tasks = await load_tasks(config.state_file)
        task = find_task(tasks, args.task_id)
        if task is None:
            print(f"Task '{args.task_id}' not found.")
            sys.exit(1)
        print(format_task_detail(task))

    asyncio.run(_status())


def cmd_task_answer(args: argparse.Namespace, config: AppConfig) -> None:
    """Answer a blocked task's question to unblock it."""
    from tasks import TaskTransitionError, answer_task, find_task, load_tasks, save_tasks
    from event_watcher import enqueue_event

    async def _answer():
        tasks = await load_tasks(config.state_file)
        task = find_task(tasks, args.task_id)
        if task is None:
            print(f"Task '{args.task_id}' not found.")
            sys.exit(1)
        if (
            task.status in ("assigned", "in_progress")
            and task.answers
            and task.answers[-1].get("answer", "") == args.answer
        ):
            print(f"No-op: task {task.id} already recorded this answer.")
            return
        if task.status != "blocked":
            print(f"Task {task.id} is not blocked (status={task.status}).")
            sys.exit(1)
        try:
            answer_task(task, args.answer)
        except TaskTransitionError as exc:
            print(str(exc))
            sys.exit(1)
        await save_tasks(tasks, config.state_file)
        enqueue_event("task_answered", {"task_id": task.id})
        print(f"Answered task {task.id} — it will resume on the next heartbeat.")

    asyncio.run(_answer())


def cmd_inbox_list(args: argparse.Namespace, config: AppConfig) -> None:
    """List messages in the inbox."""
    from messaging import format_inbox, get_unread, load_outbox

    async def _list():
        messages = await load_outbox(config.state_file)
        show_all = getattr(args, "all", False)
        if show_all:
            print(format_inbox(messages, show_read=True))
        else:
            print(format_inbox(messages))
        unread = get_unread(messages)
        if unread:
            needs_response = sum(1 for m in unread if m.requires_response)
            print(f"\n{len(unread)} unread message(s)", end="")
            if needs_response:
                print(f", {needs_response} need response", end="")
            print()

    asyncio.run(_list())


def cmd_inbox_show(args: argparse.Namespace, config: AppConfig) -> None:
    """Show a message in detail and mark it as read."""
    from messaging import find_message, format_message_detail, load_outbox, mark_read, save_outbox

    async def _show():
        messages = await load_outbox(config.state_file)
        msg = find_message(messages, args.message_id)
        if msg is None:
            print(f"Message '{args.message_id}' not found.")
            sys.exit(1)
        mark_read(msg)
        await save_outbox(messages, config.state_file)
        print(format_message_detail(msg))

    asyncio.run(_show())


def cmd_inbox_dismiss(args: argparse.Namespace, config: AppConfig) -> None:
    """Dismiss a message or all read messages."""
    from messaging import dismiss_all_read, find_message, load_outbox, mark_dismissed, save_outbox

    async def _dismiss():
        messages = await load_outbox(config.state_file)
        if getattr(args, "all", False):
            count = dismiss_all_read(messages)
            await save_outbox(messages, config.state_file)
            print(f"Dismissed {count} read message(s).")
        else:
            msg = find_message(messages, args.message_id)
            if msg is None:
                print(f"Message '{args.message_id}' not found.")
                sys.exit(1)
            mark_dismissed(msg)
            await save_outbox(messages, config.state_file)
            print(f"Dismissed message {msg.id}.")

    asyncio.run(_dismiss())


def cmd_inbox_clear(args: argparse.Namespace, config: AppConfig) -> None:
    """Clear all messages from the outbox."""
    from messaging import load_outbox, save_outbox

    async def _clear():
        messages = await load_outbox(config.state_file)
        count = len(messages)
        await save_outbox([], config.state_file)
        print(f"Cleared {count} message(s) from inbox.")

    asyncio.run(_clear())


def cmd_task_cancel(args: argparse.Namespace, config: AppConfig) -> None:
    """Cancel a task."""
    from tasks import TaskTransitionError, cancel_task, find_task, load_tasks, save_tasks
    from event_watcher import enqueue_event

    async def _cancel():
        tasks = await load_tasks(config.state_file)
        task = find_task(tasks, args.task_id)
        if task is None:
            print(f"Task '{args.task_id}' not found.")
            sys.exit(1)
        if task.status == "failed" and any(
            note.startswith("CANCELLED") for note in task.progress_notes
        ):
            print(f"No-op: task {task.id} is already cancelled.")
            return
        if task.status in ("completed", "failed"):
            print(f"Task {task.id} is already {task.status} — cannot cancel.")
            sys.exit(1)
        reason = getattr(args, "reason", "") or ""
        try:
            cancel_task(task, reason)
        except TaskTransitionError as exc:
            print(str(exc))
            sys.exit(1)
        await save_tasks(tasks, config.state_file)
        enqueue_event("task_cancelled", {"task_id": task.id})
        print(f"Cancelled task {task.id}: {task.title}")

    asyncio.run(_cancel())


def cmd_task_retry(args: argparse.Namespace, config: AppConfig) -> None:
    """Retry a failed or blocked task."""
    from tasks import TaskTransitionError, find_task, load_tasks, retry_task, save_tasks
    from event_watcher import enqueue_event

    async def _retry():
        tasks = await load_tasks(config.state_file)
        task = find_task(tasks, args.task_id)
        if task is None:
            print(f"Task '{args.task_id}' not found.")
            sys.exit(1)
        if task.status == "pending" and any(
            note == "RETRIED by developer" for note in task.progress_notes
        ):
            print(f"No-op: task {task.id} is already retried and pending pickup.")
            return
        if task.status not in ("failed", "blocked"):
            print(f"Task {task.id} is {task.status} — only failed or blocked tasks can be retried.")
            sys.exit(1)
        try:
            retry_task(task)
        except TaskTransitionError as exc:
            print(str(exc))
            sys.exit(1)
        await save_tasks(tasks, config.state_file)
        enqueue_event("task_retried", {"task_id": task.id})
        print(f"Retried task {task.id}: {task.title} — it will be picked up next heartbeat.")

    asyncio.run(_retry())


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


def cmd_api(args: argparse.Namespace, config: AppConfig) -> None:
    """Start the FastAPI HTTP server."""
    import uvicorn
    from api_server import create_app

    app = create_app(config)
    host = getattr(args, "host", "127.0.0.1")
    port = getattr(args, "port", 8321)
    reload = getattr(args, "reload", False)
    print(f"Starting NATLClaw API on http://{host}:{port}")
    uvicorn.run(
        "api_server:app" if reload else app,
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


def cmd_status(args: argparse.Namespace, config: AppConfig) -> None:
    """Print a single operator snapshot of runtime state."""
    from operator_status import build_operator_status

    snap = asyncio.run(build_operator_status(config))

    sched = snap["scheduler"]
    hb = snap["heartbeat"]
    tasks = snap["tasks"]
    inbox = snap["inbox"]
    errors = snap["errors"]
    reliability = snap.get("reliability", {})
    active = tasks["active"]
    sla = tasks.get("sla", {})

    print("Operator Status")
    print("---------------")
    print(
        f"Scheduler: {'RUNNING' if sched['running'] else 'STOPPED'} "
        f"(in-process={sched['in_process_task_running']})"
    )
    print(
        f"Heartbeat: {hb['status']} | count={hb['count']} | "
        f"last={hb['last'] or '-'} | seconds_ago={hb['seconds_ago']}"
    )
    if active:
        print(
            "Active task: "
            f"[{active['id']}] {active['title']} ({active['status']}, {active['priority']}, "
            f"{active['heartbeats_spent']}/{active['max_heartbeats']})"
        )
    else:
        print("Active task: none")
    print(f"Blocked tasks: {tasks['blocked_count']} | Total tasks: {tasks['total']}")
    if sla:
        oldest_pending = sla.get("oldest_pending_age_sec")
        oldest_pending_str = (
            f"{oldest_pending}s" if isinstance(oldest_pending, (int, float)) else "-"
        )
        print(
            "SLA risk: "
            f"at_risk={sla.get('at_risk_count', 0)} | "
            f"breached={sla.get('breached_count', 0)} | "
            f"oldest_pending={oldest_pending_str}"
        )
    print(
        f"Inbox unread: {inbox['unread_count']} "
        f"(needs response: {inbox['requires_response_count']})"
    )
    print(
        f"Recent errors: {errors['recent_error_count']}"
        + (f" | last={errors['last_error']['step']} @ {errors['last_error']['timestamp']}"
           if errors["last_error"] else "")
    )
    top_types = errors.get("top_error_types", [])
    if top_types:
        top_line = ", ".join(f"{item['type']}={item['count']}" for item in top_types)
        print(f"Top error types: {top_line}")
    if reliability:
        err_rate = reliability.get("error_rate")
        err_rate_str = f"{err_rate:.3f}" if isinstance(err_rate, (int, float)) else "-"
        print(
            "Soak reliability: "
            f"{reliability.get('status', 'unknown')} | "
            f"window={reliability.get('window_heartbeats', 0)} hb | "
            f"errors={reliability.get('recent_error_count', 0)} | "
            f"error_rate={err_rate_str} | "
            f"stale_lock={reliability.get('stale_lock', False)}"
        )


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
    from second_brain import load_brain, save_brain, add_note, search_notes, search_notes_from_store
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
            results = search_notes_from_store(
                config.state_file,
                query,
                max_results=8,
                record_access=True,
            )
            brain = _run_async(load_brain(config.state_file))  # refresh access counters
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

    # status
    sub.add_parser("status", help="Show unified operator status snapshot")

    # serve — API server + dashboard
    serve_p = sub.add_parser("serve", help="Start the API server with dashboard")
    serve_p.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    serve_p.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")

    # chat (NEW)
    chat_p = sub.add_parser("chat", help="Start an interactive chat session with the agent")

    # brief — daily digest
    brief_p = sub.add_parser("brief", help="Print a daily digest / morning briefing")
    brief_p.add_argument("--save", action="store_true",
                         help="Also save digest to data/digests/YYYY-MM-DD.md")

    # report — workspace audit
    report_p = sub.add_parser("report", help="Run a comprehensive workspace audit")
    report_p.add_argument("--save", action="store_true",
                          help="Save report to data/reports/")
    report_p.add_argument("--max-turns", type=int, default=15,
                          help="Maximum agent turns for the audit (default: 15)")

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

    # inbox — messaging
    inbox_p = sub.add_parser("inbox", help="View agent messages and notifications")
    inbox_sub = inbox_p.add_subparsers(dest="inbox_command")

    inbox_list_p = inbox_sub.add_parser("list", help="List messages (default: unread only)")
    inbox_list_p.add_argument("-a", "--all", action="store_true",
                              help="Show all messages including dismissed")

    inbox_show_p = inbox_sub.add_parser("show", help="Show a message in detail (marks as read)")
    inbox_show_p.add_argument("message_id", help="Message ID to view")

    inbox_dismiss_p = inbox_sub.add_parser("dismiss", help="Dismiss a message or all read messages")
    inbox_dismiss_p.add_argument("message_id", nargs="?", default=None,
                                 help="Message ID to dismiss (omit with --all)")
    inbox_dismiss_p.add_argument("-a", "--all", action="store_true",
                                 help="Dismiss all read messages")

    inbox_sub.add_parser("clear", help="Clear all messages from inbox")

    # brain
    brain_p = sub.add_parser("brain", help="Brain management commands")
    brain_sub = brain_p.add_subparsers(dest="brain_command")

    brain_sub.add_parser("stats", help="Show brain statistics")

    search_p = brain_sub.add_parser("search", help="Full-text search over notes")
    search_p.add_argument("query", help="Search query")
    search_p.add_argument("--limit", type=int, default=10, help="Maximum results to show")

    show_p = brain_sub.add_parser("show", help="Show one note with metadata and connections")
    show_p.add_argument("note_id", help="Note ID to inspect")

    topics_p = brain_sub.add_parser("topics", help="Show the most connected topics")
    topics_p.add_argument("--limit", type=int, default=10, help="Maximum topics to show")

    trace_p = brain_sub.add_parser("trace", help="Trace notes reachable from a topic")
    trace_p.add_argument("topic", help="Topic name to trace")
    trace_p.add_argument("--depth", type=int, default=1, help="How many topic hops to traverse")
    trace_p.add_argument("--limit", type=int, default=10, help="Maximum notes to show")

    feedback_p = brain_sub.add_parser("feedback", help="Record explicit relevance feedback for a note")
    feedback_p.add_argument("note_id", help="Note ID to reinforce or demote")
    feedback_group = feedback_p.add_mutually_exclusive_group(required=True)
    feedback_group.add_argument("--relevant", action="store_true", help="Mark note as reinforced")
    feedback_group.add_argument("--irrelevant", action="store_true", help="Mark note as weak or stale")
    feedback_p.add_argument("--reason", default="", help="Optional reason for the feedback")

    contradict_p = brain_sub.add_parser("contradict", help="Mark a note as contradicted by another note")
    contradict_p.add_argument("note_id", help="Note ID to demote")
    contradict_p.add_argument("by_note_id", help="Note ID that contradicts or supersedes it")
    contradict_p.add_argument("--reason", default="", help="Optional contradiction reason")
    contradict_p.add_argument("--supersede", action="store_true", help="Force the contradicted note into superseded status")

    add_p = brain_sub.add_parser("add", help="Manually add a note")
    add_p.add_argument("content", help="Note content")
    add_p.add_argument("--tags", default="", help="Comma-separated tags")
    add_p.add_argument("--category", default="resources", help="PARA category")
    add_p.add_argument("--type", dest="note_type", default="general", help="Note type")
    add_p.add_argument("--status", default="active", help="Lifecycle status")
    add_p.add_argument("--confidence", type=int, default=None, help="Confidence score (0-100)")

    export_p = brain_sub.add_parser("export", help="Export brain to markdown")
    export_p.add_argument("-o", "--output", help="Output file path (default: stdout)")

    brain_sub.add_parser("lint", help="Run brain health check")

    # task
    task_p = sub.add_parser("task", help="Task queue management")
    task_sub = task_p.add_subparsers(dest="task_command")

    task_add_p = task_sub.add_parser("add", help="Create a new task for the agent")
    task_add_p.add_argument("title", help="Task title / short description")
    task_add_p.add_argument("-d", "--description", default=None,
                            help="Detailed description (default: same as title)")
    task_add_p.add_argument("-p", "--priority", default="medium",
                            choices=["low", "medium", "high", "urgent"],
                            help="Task priority (default: medium)")
    task_add_p.add_argument("--max-heartbeats", type=int, default=10,
                            help="Maximum heartbeats before auto-timeout (default: 10)")

    task_list_p = task_sub.add_parser("list", help="List tasks")
    task_list_p.add_argument("-s", "--status", default="all",
                             help="Filter by status (pending, in_progress, blocked, completed, failed, all)")

    task_status_p = task_sub.add_parser("status", help="Show detailed task status")
    task_status_p.add_argument("task_id", help="Task ID to inspect")

    task_answer_p = task_sub.add_parser("answer", help="Answer a blocked task's question")
    task_answer_p.add_argument("task_id", help="Task ID to answer")
    task_answer_p.add_argument("answer", help="Your answer to the agent's question")

    task_cancel_p = task_sub.add_parser("cancel", help="Cancel a pending or in-progress task")
    task_cancel_p.add_argument("task_id", help="Task ID to cancel")
    task_cancel_p.add_argument("--reason", default="", help="Optional cancellation reason")

    task_retry_p = task_sub.add_parser("retry", help="Retry a failed or blocked task")
    task_retry_p.add_argument("task_id", help="Task ID to retry")

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

    # api
    api_p = sub.add_parser("api", help="Start the HTTP API server")
    api_p.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    api_p.add_argument("--port", type=int, default=8321, help="Port (default: 8321)")
    api_p.add_argument("--reload", action="store_true", help="Auto-reload on code changes")

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
        "status": cmd_status,
        "serve": cmd_serve,
        "chat": cmd_chat,
        "brief": cmd_brief,
        "report": cmd_report,
        "code": cmd_code,
        "inbox": {
            "list": cmd_inbox_list,
            "show": cmd_inbox_show,
            "dismiss": cmd_inbox_dismiss,
            "clear": cmd_inbox_clear,
        },
        "task": {
            "add": cmd_task_add,
            "list": cmd_task_list,
            "status": cmd_task_status,
            "answer": cmd_task_answer,
            "cancel": cmd_task_cancel,
            "retry": cmd_task_retry,
        },
        "brain": {
            "stats": cmd_brain_stats,
            "show": cmd_brain_show,
            "search": cmd_brain_search,
            "topics": cmd_brain_topics,
            "trace": cmd_brain_trace,
            "feedback": cmd_brain_feedback,
            "contradict": cmd_brain_contradict,
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
        "api": cmd_api,
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
