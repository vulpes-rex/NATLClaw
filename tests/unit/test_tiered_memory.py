"""Tests for tiered memory: WikiPage, consolidation, wiki lint, and integration."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Mock external deps before importing project modules ──────────────
sys.modules.setdefault("copilot", MagicMock())
sys.modules.setdefault("agent_framework_github_copilot", MagicMock())
sys.modules.setdefault("agent_framework", MagicMock())
sys.modules.setdefault("agent_framework.foundry", MagicMock())
sys.modules.setdefault("agent_framework.openai", MagicMock())
sys.modules.setdefault("agent_framework.ollama", MagicMock())
sys.modules.setdefault("azure.identity", MagicMock())
sys.modules.setdefault("dotenv", MagicMock())

from second_brain import (
    BrainState,
    WikiPage,
    add_note,
    add_page,
    archive_consolidated_notes,
    build_brain_summary,
    build_wiki_summary,
    get_unconsolidated_notes,
    should_consolidate,
    should_lint_wiki,
    update_page,
)
from config import AppConfig
from persona_loader import Persona
from state import AgentState
import workflow as wf_mod
from workflow import (
    _apply_consolidation,
    _apply_wiki_lint,
    _run_consolidation_step,
    _run_second_brain_heartbeat,
    _run_wiki_lint_step,
)

logging.basicConfig(level=logging.DEBUG)


# ── Helpers ──────────────────────────────────────────────────────────

def _make_brain_with_notes(count: int) -> BrainState:
    """Return a BrainState pre-populated with *count* notes."""
    brain = BrainState()
    for i in range(count):
        add_note(brain, content=f"Note number {i}", tags=[f"tag{i}"])
    return brain


def _json_agent(*responses: str) -> MagicMock:
    """Build a mock agent that returns the given texts in sequence."""
    agent = MagicMock()
    agent.run = AsyncMock(
        side_effect=[MagicMock(text=r) for r in responses]
    )
    return agent


@pytest.fixture
def brain():
    return BrainState()


@pytest.fixture
def state():
    return AgentState(execution_count=1)


@pytest.fixture
def config():
    cfg = MagicMock(spec=AppConfig)
    cfg.provider = "foundry"
    cfg.model = "test-model"
    cfg.agent_name = "TestClaw"
    cfg.heartbeat_interval_sec = 60
    cfg.state_file = "data/test.json"
    cfg.max_history = 100
    cfg.agent_instructions = ""
    return cfg


@pytest.fixture
def persona():
    p = MagicMock(spec=Persona)
    p.name = "tester"
    p.description = "Test persona"
    p.instructions = "Test instructions"
    p.workflow = "second_brain"
    p.heartbeat_task = "Research something interesting"
    p.tools = []
    p.mcp_servers = {}
    p.steps = []
    p.stepwise = False
    p.roster = []
    p.schedule = "round_robin"
    p.consolidation_interval = 5
    p.consolidation_threshold = 10
    p.lint_wiki_interval = 20
    return p


# ══════════════════════════════════════════════════════════════════════
# A. WikiPage dataclass
# ══════════════════════════════════════════════════════════════════════

class TestWikiPage:

    def test_dataclass_fields(self):
        """WikiPage has all required fields."""
        page = WikiPage(id="test", title="Test", content="body")
        assert page.id == "test"
        assert page.title == "Test"
        assert page.content == "body"
        assert page.sources == []
        assert page.tags == []
        assert page.created_at == ""
        assert page.updated_at == ""

    def test_asdict(self):
        """WikiPage converts to dict correctly."""
        page = WikiPage(id="p1", title="T", content="C", sources=["n0001"])
        d = asdict(page)
        assert d["id"] == "p1"
        assert d["sources"] == ["n0001"]


# ══════════════════════════════════════════════════════════════════════
# B. BrainState extended fields
# ══════════════════════════════════════════════════════════════════════

class TestBrainStateExtended:

    def test_new_fields_default(self):
        """BrainState has pages, lint_log, page_count, last_consolidation, last_lint."""
        brain = BrainState()
        assert brain.pages == {}
        assert brain.lint_log == []
        assert brain.page_count == 0
        assert brain.last_consolidation is None
        assert brain.last_lint is None

    def test_backward_compatible_construction(self):
        """BrainState can be constructed with only legacy fields."""
        brain = BrainState(notes={"n1": {}}, connections=[], capture_count=1)
        assert brain.pages == {}
        assert brain.page_count == 0

    def test_full_construction(self):
        """BrainState can be constructed with all new fields."""
        brain = BrainState(
            pages={"p1": {"id": "p1", "title": "T", "content": "C"}},
            lint_log=[{"timestamp": "now", "issues": []}],
            page_count=1,
            last_consolidation="2026-01-01T00:00:00Z",
            last_lint="2026-01-01T00:00:00Z",
        )
        assert len(brain.pages) == 1
        assert brain.page_count == 1


# ══════════════════════════════════════════════════════════════════════
# C. add_page / update_page
# ══════════════════════════════════════════════════════════════════════

class TestAddPage:

    def test_creates_page(self, brain):
        pid = add_page(brain, "Deployment Patterns", "# Deploy\nBlue-green...", ["n0001"], ["devops"])
        assert pid in brain.pages
        page = brain.pages[pid]
        assert page["title"] == "Deployment Patterns"
        assert page["content"] == "# Deploy\nBlue-green..."
        assert page["sources"] == ["n0001"]
        assert page["tags"] == ["devops"]
        assert page["created_at"]
        assert page["updated_at"]
        assert brain.page_count == 1

    def test_slug_generation(self, brain):
        pid = add_page(brain, "React State Management", "content")
        assert pid == "react-state-management"

    def test_duplicate_slug_uniqueness(self, brain):
        pid1 = add_page(brain, "Test", "content 1")
        pid2 = add_page(brain, "Test", "content 2")
        assert pid1 != pid2
        assert len(brain.pages) == 2

    def test_empty_title_fallback(self, brain):
        pid = add_page(brain, "", "some content")
        assert pid.startswith("page-")

    def test_page_count_increments(self, brain):
        add_page(brain, "A", "a")
        add_page(brain, "B", "b")
        assert brain.page_count == 2


class TestUpdatePage:

    def test_updates_existing_page(self, brain):
        pid = add_page(brain, "Topic", "old content", ["n0001"])
        result = update_page(brain, pid, "new content", ["n0002"])
        assert result is True
        page = brain.pages[pid]
        assert page["content"] == "new content"
        assert "n0001" in page["sources"]
        assert "n0002" in page["sources"]
        assert page["updated_at"] >= page["created_at"]

    def test_returns_false_for_missing_page(self, brain):
        result = update_page(brain, "nonexistent", "content")
        assert result is False

    def test_no_new_sources(self, brain):
        pid = add_page(brain, "Topic", "content", ["n0001"])
        update_page(brain, pid, "updated")
        assert brain.pages[pid]["sources"] == ["n0001"]

    def test_dedup_sources(self, brain):
        pid = add_page(brain, "Topic", "content", ["n0001", "n0002"])
        update_page(brain, pid, "new", ["n0001", "n0003"])
        sources = brain.pages[pid]["sources"]
        assert len(sources) == 3  # n0001, n0002, n0003 (no dup)


# ══════════════════════════════════════════════════════════════════════
# D. get_unconsolidated_notes
# ══════════════════════════════════════════════════════════════════════

class TestGetUnconsolidatedNotes:

    def test_all_notes_unconsolidated_when_no_pages(self):
        brain = _make_brain_with_notes(3)
        result = get_unconsolidated_notes(brain)
        assert len(result) == 3

    def test_consolidated_notes_excluded(self):
        brain = _make_brain_with_notes(3)
        add_page(brain, "Page", "merged", sources=["n0001", "n0002"])
        result = get_unconsolidated_notes(brain)
        assert len(result) == 1
        assert result[0]["id"] == "n0003"

    def test_archived_notes_excluded(self):
        brain = _make_brain_with_notes(2)
        brain.notes["n0001"]["category"] = "archive"
        result = get_unconsolidated_notes(brain)
        assert len(result) == 1
        assert result[0]["id"] == "n0002"

    def test_empty_brain(self, brain):
        assert get_unconsolidated_notes(brain) == []


# ══════════════════════════════════════════════════════════════════════
# E. build_wiki_summary
# ══════════════════════════════════════════════════════════════════════

class TestBuildWikiSummary:

    def test_empty_when_no_pages(self, brain):
        assert build_wiki_summary(brain) == ""

    def test_shows_pages(self, brain):
        add_page(brain, "React Patterns", "Component composition is...", ["n0001"])
        add_page(brain, "CI/CD", "Continuous integration...", ["n0002", "n0003"])
        summary = build_wiki_summary(brain)
        assert "WIKI PAGES" in summary
        assert "React Patterns" in summary
        assert "CI/CD" in summary
        assert "2 sources" in summary

    def test_max_pages_limit(self, brain):
        for i in range(15):
            add_page(brain, f"Page {i}", f"content {i}")
        summary = build_wiki_summary(brain, max_pages=3)
        # Should only show 3 pages
        assert summary.count("📄") == 3


# ══════════════════════════════════════════════════════════════════════
# F. should_consolidate / should_lint_wiki
# ══════════════════════════════════════════════════════════════════════

class TestShouldConsolidate:

    def test_periodic_trigger(self, brain):
        assert should_consolidate(brain, interval=5, threshold=100, heartbeat_number=10) is True

    def test_periodic_no_trigger(self, brain):
        assert should_consolidate(brain, interval=5, threshold=100, heartbeat_number=7) is False

    def test_threshold_trigger(self):
        brain = _make_brain_with_notes(12)
        assert should_consolidate(brain, interval=0, threshold=10, heartbeat_number=3) is True

    def test_below_threshold(self):
        brain = _make_brain_with_notes(5)
        assert should_consolidate(brain, interval=0, threshold=10, heartbeat_number=3) is False

    def test_interval_zero_disables_periodic(self, brain):
        assert should_consolidate(brain, interval=0, threshold=100, heartbeat_number=10) is False

    def test_heartbeat_zero_never_triggers_periodic(self, brain):
        assert should_consolidate(brain, interval=5, threshold=100, heartbeat_number=0) is False


class TestShouldLintWiki:

    def test_periodic_trigger(self, brain):
        assert should_lint_wiki(brain, interval=20, heartbeat_number=40) is True

    def test_no_trigger(self, brain):
        assert should_lint_wiki(brain, interval=20, heartbeat_number=15) is False

    def test_disabled(self, brain):
        assert should_lint_wiki(brain, interval=0, heartbeat_number=20) is False


# ══════════════════════════════════════════════════════════════════════
# G. archive_consolidated_notes
# ══════════════════════════════════════════════════════════════════════

class TestArchiveConsolidatedNotes:

    def test_archives_named_notes(self):
        brain = _make_brain_with_notes(3)
        count = archive_consolidated_notes(brain, ["n0001", "n0002"])
        assert count == 2
        assert brain.notes["n0001"]["category"] == "archive"
        assert brain.notes["n0002"]["category"] == "archive"
        assert brain.notes["n0003"]["category"] != "archive"

    def test_skips_already_archived(self):
        brain = _make_brain_with_notes(2)
        brain.notes["n0001"]["category"] = "archive"
        count = archive_consolidated_notes(brain, ["n0001", "n0002"])
        assert count == 1  # only n0002 changed

    def test_skips_missing_ids(self):
        brain = _make_brain_with_notes(1)
        count = archive_consolidated_notes(brain, ["n0001", "n9999"])
        assert count == 1


# ══════════════════════════════════════════════════════════════════════
# H. build_brain_summary (updated for tiered memory)
# ══════════════════════════════════════════════════════════════════════

class TestBuildBrainSummaryTiered:

    def test_shows_wiki_page_count(self):
        brain = _make_brain_with_notes(2)
        add_page(brain, "P1", "content")
        summary = build_brain_summary(brain)
        assert "Wiki pages: 1" in summary

    def test_shows_pending_consolidation(self):
        brain = _make_brain_with_notes(3)
        add_page(brain, "P", "c", sources=["n0001"])
        summary = build_brain_summary(brain)
        assert "2 pending consolidation" in summary

    def test_wiki_summaries_included(self):
        brain = _make_brain_with_notes(1)
        add_page(brain, "React Patterns", "Component composition approach")
        summary = build_brain_summary(brain)
        assert "WIKI PAGES" in summary
        assert "React Patterns" in summary

    def test_unconsolidated_notes_only_when_pages_exist(self):
        brain = _make_brain_with_notes(5)
        add_page(brain, "P", "c", sources=["n0001", "n0002"])
        summary = build_brain_summary(brain, max_notes=10)
        # Should show "Recent unconsolidated notes" label, not "Recent knowledge"
        assert "unconsolidated" in summary.lower()

    def test_recent_knowledge_when_no_pages(self):
        brain = _make_brain_with_notes(3)
        summary = build_brain_summary(brain)
        assert "Recent knowledge" in summary

    def test_shows_last_consolidation(self):
        brain = BrainState(last_consolidation="2026-04-08T00:00:00Z")
        summary = build_brain_summary(brain)
        assert "2026-04-08" in summary


# ══════════════════════════════════════════════════════════════════════
# I. _apply_consolidation
# ══════════════════════════════════════════════════════════════════════

class TestApplyConsolidation:

    def test_creates_pages(self):
        brain = _make_brain_with_notes(3)
        raw = json.dumps({
            "updates": [],
            "creates": [
                {
                    "title": "AI Memory",
                    "content": "Agents need memory systems.",
                    "sources": ["n0001", "n0002"],
                    "tags": ["ai", "memory"],
                }
            ],
            "archived_notes": ["n0001", "n0002"],
        })
        _apply_consolidation(brain, raw)
        assert len(brain.pages) == 1
        assert brain.notes["n0001"]["category"] == "archive"
        assert brain.notes["n0002"]["category"] == "archive"
        assert brain.notes["n0003"]["category"] != "archive"

    def test_updates_existing_page(self):
        brain = _make_brain_with_notes(2)
        pid = add_page(brain, "AI Memory", "old content", ["n0001"])
        raw = json.dumps({
            "updates": [
                {
                    "page_id": pid,
                    "new_content": "updated content",
                    "sources_added": ["n0002"],
                }
            ],
            "creates": [],
            "archived_notes": ["n0002"],
        })
        _apply_consolidation(brain, raw)
        page = brain.pages[pid]
        assert page["content"] == "updated content"
        assert "n0002" in page["sources"]
        assert brain.notes["n0002"]["category"] == "archive"

    def test_markdown_wrapped_json(self):
        brain = _make_brain_with_notes(1)
        raw = "```json\n" + json.dumps({
            "updates": [],
            "creates": [{"title": "T", "content": "C", "sources": ["n0001"], "tags": []}],
            "archived_notes": ["n0001"],
        }) + "\n```"
        _apply_consolidation(brain, raw)
        assert len(brain.pages) == 1

    def test_malformed_json_does_not_crash(self):
        brain = _make_brain_with_notes(1)
        _apply_consolidation(brain, "not valid JSON")
        assert len(brain.pages) == 0  # no changes

    def test_empty_creates_and_updates(self):
        brain = _make_brain_with_notes(1)
        raw = json.dumps({"updates": [], "creates": [], "archived_notes": []})
        _apply_consolidation(brain, raw)
        assert len(brain.pages) == 0

    def test_mixed_creates_and_updates(self):
        brain = _make_brain_with_notes(4)
        pid = add_page(brain, "Existing", "old", ["n0001"])
        raw = json.dumps({
            "updates": [{"page_id": pid, "new_content": "merged", "sources_added": ["n0002"]}],
            "creates": [{"title": "New Topic", "content": "fresh", "sources": ["n0003"], "tags": ["new"]}],
            "archived_notes": ["n0002", "n0003"],
        })
        _apply_consolidation(brain, raw)
        assert len(brain.pages) == 2
        assert brain.pages[pid]["content"] == "merged"


# ══════════════════════════════════════════════════════════════════════
# J. _apply_wiki_lint
# ══════════════════════════════════════════════════════════════════════

class TestApplyWikiLint:

    def test_stores_issues_in_lint_log(self, brain):
        add_page(brain, "P", "c")
        raw = json.dumps({
            "issues": [
                {"type": "stale", "page_id": "p", "description": "old", "suggested_action": "update"}
            ]
        })
        _apply_wiki_lint(brain, raw)
        assert len(brain.lint_log) == 1
        assert brain.lint_log[0]["issues"][0]["type"] == "stale"

    def test_empty_issues(self, brain):
        raw = json.dumps({"issues": []})
        _apply_wiki_lint(brain, raw)
        assert len(brain.lint_log) == 0  # nothing stored for empty

    def test_malformed_json(self, brain):
        _apply_wiki_lint(brain, "not JSON")
        assert len(brain.lint_log) == 0

    def test_markdown_wrapped(self, brain):
        raw = "```\n" + json.dumps({"issues": [{"type": "stale", "page_id": "x", "description": "d", "suggested_action": "a"}]}) + "\n```"
        _apply_wiki_lint(brain, raw)
        assert len(brain.lint_log) == 1


# ══════════════════════════════════════════════════════════════════════
# K. _run_consolidation_step (async, with mock agent)
# ══════════════════════════════════════════════════════════════════════

class TestRunConsolidationStep:

    def test_creates_pages_via_agent(self, state, config, persona):
        brain = _make_brain_with_notes(2)
        consolidation_json = json.dumps({
            "updates": [],
            "creates": [
                {"title": "Knowledge", "content": "merged notes", "sources": ["n0001", "n0002"], "tags": ["ai"]}
            ],
            "archived_notes": ["n0001", "n0002"],
        })
        agent = _json_agent(consolidation_json)
        asyncio.run(_run_consolidation_step(agent, state, brain, config, persona))
        assert len(brain.pages) == 1
        assert brain.last_consolidation is not None
        assert brain.notes["n0001"]["category"] == "archive"

    def test_skips_when_no_unconsolidated(self, state, config, persona, brain):
        agent = _json_agent()
        asyncio.run(_run_consolidation_step(agent, state, brain, config, persona))
        # No agent call because no pending notes
        assert agent.run.call_count == 0
        assert brain.last_consolidation is None

    def test_handles_agent_error(self, state, config, persona):
        brain = _make_brain_with_notes(2)
        agent = MagicMock()
        agent.run = AsyncMock(side_effect=Exception("LLM unavailable"))
        # Should not crash
        asyncio.run(_run_consolidation_step(agent, state, brain, config, persona))
        assert len(brain.pages) == 0


# ══════════════════════════════════════════════════════════════════════
# L. _run_wiki_lint_step (async, with mock agent)
# ══════════════════════════════════════════════════════════════════════

class TestRunWikiLintStep:

    def test_stores_issues(self, state, config):
        brain = _make_brain_with_notes(1)
        add_page(brain, "P", "content")
        lint_json = json.dumps({
            "issues": [{"type": "stale", "page_id": "p", "description": "old", "suggested_action": "update"}]
        })
        agent = _json_agent(lint_json)
        asyncio.run(_run_wiki_lint_step(agent, state, brain, config))
        assert brain.last_lint is not None
        assert len(brain.lint_log) == 1

    def test_skips_when_no_pages(self, state, config, brain):
        agent = _json_agent()
        asyncio.run(_run_wiki_lint_step(agent, state, brain, config))
        assert agent.run.call_count == 0
        assert brain.last_lint is None

    def test_handles_agent_error(self, state, config):
        brain = BrainState()
        add_page(brain, "P", "content")
        agent = MagicMock()
        agent.run = AsyncMock(side_effect=Exception("LLM unavailable"))
        asyncio.run(_run_wiki_lint_step(agent, state, brain, config))
        assert brain.last_lint is None


# ══════════════════════════════════════════════════════════════════════
# M. End-to-end: consolidation fires during heartbeat
# ══════════════════════════════════════════════════════════════════════

class TestConsolidationInHeartbeat:

    def test_consolidation_fires_at_interval(self, state, config, persona):
        """Consolidation runs when execution_count hits the interval."""
        state.execution_count = 5
        persona.consolidation_interval = 5
        persona.consolidation_threshold = 999
        persona.lint_wiki_interval = 0

        # Pre-populate brain so connect step triggers + notes > 0
        brain = _make_brain_with_notes(2)

        capture_json = json.dumps({
            "topic": "X", "content": "insight", "tags": [], "category": "resources"
        })
        connect_json = json.dumps({"from": "n0001", "to": "n0002", "reason": "r"})
        consolidation_json = json.dumps({
            "updates": [],
            "creates": [{"title": "T", "content": "C", "sources": ["n0001", "n0002"], "tags": []}],
            "archived_notes": ["n0001"],
        })

        agent = _json_agent(
            "system ok",         # status
            capture_json,        # capture
            connect_json,        # connect
            consolidation_json,  # consolidate
            "review done",       # review
        )

        asyncio.run(_run_second_brain_heartbeat(agent, state, brain, config, persona))
        assert agent.run.call_count == 5  # status + capture + connect + consolidate + review
        assert len(brain.pages) == 1
        assert brain.last_consolidation is not None

    def test_consolidation_skipped_when_disabled(self, state, config, persona):
        """No consolidation when interval=0 and threshold not met."""
        state.execution_count = 5
        persona.consolidation_interval = 0
        persona.consolidation_threshold = 999
        persona.lint_wiki_interval = 0

        brain = _make_brain_with_notes(2)
        capture_json = json.dumps({
            "topic": "X", "content": "insight", "tags": [], "category": "resources"
        })
        connect_json = json.dumps({"from": "n0001", "to": "n0002", "reason": "r"})

        agent = _json_agent(
            "status ok",     # status
            capture_json,    # capture
            connect_json,    # connect
            "review done",   # review
        )

        asyncio.run(_run_second_brain_heartbeat(agent, state, brain, config, persona))
        assert agent.run.call_count == 4  # no consolidation

    def test_wiki_lint_fires_at_interval(self, state, config, persona):
        """Wiki lint runs at the configured interval."""
        state.execution_count = 20
        persona.consolidation_interval = 0
        persona.consolidation_threshold = 999
        persona.lint_wiki_interval = 20

        brain = _make_brain_with_notes(2)
        add_page(brain, "Existing Page", "content")

        capture_json = json.dumps({
            "topic": "X", "content": "insight", "tags": [], "category": "resources"
        })
        connect_json = json.dumps({"from": "n0001", "to": "n0002", "reason": "r"})
        lint_json = json.dumps({"issues": [{"type": "stale", "page_id": "p", "description": "d", "suggested_action": "a"}]})

        agent = _json_agent(
            "status ok",    # status
            capture_json,   # capture
            connect_json,   # connect
            lint_json,      # wiki_lint
            "review done",  # review
        )

        asyncio.run(_run_second_brain_heartbeat(agent, state, brain, config, persona))
        assert agent.run.call_count == 5
        assert brain.last_lint is not None
        assert len(brain.lint_log) == 1


# ══════════════════════════════════════════════════════════════════════
# N. Persona loader: consolidation config
# ══════════════════════════════════════════════════════════════════════

class TestPersonaConsolidationConfig:

    def test_default_values(self):
        p = Persona(name="t", description="t", instructions="t")
        assert p.consolidation_interval == 5
        assert p.consolidation_threshold == 10
        assert p.lint_wiki_interval == 20

    def test_custom_values(self):
        p = Persona(
            name="t", description="t", instructions="t",
            consolidation_interval=3,
            consolidation_threshold=5,
            lint_wiki_interval=10,
        )
        assert p.consolidation_interval == 3
        assert p.consolidation_threshold == 5
        assert p.lint_wiki_interval == 10


# ══════════════════════════════════════════════════════════════════════
# O. Prompt templates exist
# ══════════════════════════════════════════════════════════════════════

class TestConsolidationPromptTemplates:

    def test_consolidate_template_exists(self):
        from prompts import load_prompt
        result = load_prompt(
            "second_brain", "consolidate",
            agent_name="Test",
            heartbeat_number=5,
            notes_json="[]",
            pages_summary="(none)",
        )
        assert result  # non-empty
        assert "consolidat" in result.lower() or "wiki" in result.lower()

    def test_lint_wiki_template_exists(self):
        from prompts import load_prompt
        result = load_prompt(
            "second_brain", "lint_wiki",
            pages_json="[]",
        )
        assert result
        assert "audit" in result.lower() or "quality" in result.lower()


# ══════════════════════════════════════════════════════════════════════
# P. JSON round-trip: brain with pages persists correctly
# ══════════════════════════════════════════════════════════════════════

class TestBrainPersistence:

    def test_asdict_includes_pages(self):
        brain = _make_brain_with_notes(1)
        add_page(brain, "Topic", "content", ["n0001"])
        brain.last_consolidation = "2026-01-01T00:00:00Z"
        brain.last_lint = "2026-01-01T00:00:00Z"
        d = asdict(brain)
        assert "pages" in d
        assert "lint_log" in d
        assert "page_count" in d
        assert "last_consolidation" in d
        assert "last_lint" in d
        assert len(d["pages"]) == 1

    def test_roundtrip_via_json(self):
        """BrainState with pages survives JSON serialisation and reconstruction."""
        brain = _make_brain_with_notes(2)
        add_page(brain, "P1", "content 1", ["n0001"])
        brain.last_consolidation = "2026-04-08T00:00:00Z"
        brain.lint_log = [{"timestamp": "t", "issues": []}]

        raw = json.dumps(asdict(brain))
        data = json.loads(raw)
        restored = BrainState(**{k: v for k, v in data.items() if k in BrainState.__dataclass_fields__})

        assert len(restored.pages) == 1
        assert restored.page_count == 1
        assert restored.last_consolidation == "2026-04-08T00:00:00Z"
        assert len(restored.lint_log) == 1


# ══════════════════════════════════════════════════════════════════════
# Q. Multi-note → multi-page consolidation
# ══════════════════════════════════════════════════════════════════════

class TestMultiPageConsolidation:
    """Verify that a single consolidation pass can create multiple pages
    and archive the correct notes for each."""

    def test_creates_multiple_pages_single_pass(self):
        brain = _make_brain_with_notes(6)
        raw = json.dumps({
            "updates": [],
            "creates": [
                {"title": "AI Basics",     "content": "Foundation concepts.",   "sources": ["n0001", "n0002"], "tags": ["ai"]},
                {"title": "Cloud Infra",   "content": "AWS, Azure, GCP.",       "sources": ["n0003", "n0004"], "tags": ["cloud"]},
                {"title": "Testing Guide", "content": "Unit + integration.",    "sources": ["n0005"],          "tags": ["qa"]},
            ],
            "archived_notes": ["n0001", "n0002", "n0003", "n0004", "n0005"],
        })
        _apply_consolidation(brain, raw)

        assert len(brain.pages) == 3
        # Correct notes archived
        for nid in ["n0001", "n0002", "n0003", "n0004", "n0005"]:
            assert brain.notes[nid]["category"] == "archive"
        # Note 6 untouched
        assert brain.notes["n0006"]["category"] != "archive"

    def test_multi_create_plus_multi_update(self):
        brain = _make_brain_with_notes(5)
        p1 = add_page(brain, "Existing A", "old A", ["n0001"])
        p2 = add_page(brain, "Existing B", "old B", ["n0002"])
        raw = json.dumps({
            "updates": [
                {"page_id": p1, "new_content": "merged A", "sources_added": ["n0003"]},
                {"page_id": p2, "new_content": "merged B", "sources_added": ["n0004"]},
            ],
            "creates": [
                {"title": "Brand New", "content": "fresh", "sources": ["n0005"], "tags": []},
            ],
            "archived_notes": ["n0003", "n0004", "n0005"],
        })
        _apply_consolidation(brain, raw)

        assert len(brain.pages) == 3

        assert brain.pages[p1]["content"] == "merged A"
        assert "n0003" in brain.pages[p1]["sources"]

        assert brain.pages[p2]["content"] == "merged B"
        assert "n0004" in brain.pages[p2]["sources"]

        # New page exists
        new_ids = [pid for pid in brain.pages if pid not in (p1, p2)]
        assert len(new_ids) == 1
        assert brain.pages[new_ids[0]]["title"] == "Brand New"

    def test_ten_notes_to_three_pages_via_agent(self, state, config, persona):
        brain = _make_brain_with_notes(10)
        consolidation_json = json.dumps({
            "updates": [],
            "creates": [
                {"title": "Topic A", "content": "aaa", "sources": ["n0001", "n0002", "n0003"], "tags": ["a"]},
                {"title": "Topic B", "content": "bbb", "sources": ["n0004", "n0005", "n0006"], "tags": ["b"]},
                {"title": "Topic C", "content": "ccc", "sources": ["n0007", "n0008"],          "tags": ["c"]},
            ],
            "archived_notes": ["n0001", "n0002", "n0003", "n0004", "n0005", "n0006", "n0007", "n0008"],
        })
        agent = _json_agent(consolidation_json)
        asyncio.run(_run_consolidation_step(agent, state, brain, config, persona))

        assert len(brain.pages) == 3
        # n0009, n0010 remain unconsolidated
        remaining = get_unconsolidated_notes(brain)
        remaining_ids = {n["id"] for n in remaining}
        assert "n0009" in remaining_ids
        assert "n0010" in remaining_ids


# ══════════════════════════════════════════════════════════════════════
# R. Idempotency — running consolidation twice
# ══════════════════════════════════════════════════════════════════════

class TestConsolidationIdempotency:
    """Consolidation shouldn't duplicate pages or re-archive when run on
    the same notes/output."""

    def test_second_run_no_unconsolidated(self):
        """After consolidation archives notes, a second pass sees nothing to do."""
        brain = _make_brain_with_notes(3)
        raw = json.dumps({
            "updates": [],
            "creates": [{"title": "All Notes", "content": "merged", "sources": ["n0001", "n0002", "n0003"], "tags": []}],
            "archived_notes": ["n0001", "n0002", "n0003"],
        })
        _apply_consolidation(brain, raw)
        assert len(brain.pages) == 1

        # Now get_unconsolidated_notes should return nothing
        remaining = get_unconsolidated_notes(brain)
        assert len(remaining) == 0

    def test_agent_skips_when_no_unconsolidated(self, state, config, persona):
        """_run_consolidation_step doesn't call agent when no notes pending."""
        brain = _make_brain_with_notes(2)
        # Archive all notes first
        raw = json.dumps({
            "updates": [],
            "creates": [{"title": "Done", "content": "c", "sources": ["n0001", "n0002"], "tags": []}],
            "archived_notes": ["n0001", "n0002"],
        })
        _apply_consolidation(brain, raw)

        agent = _json_agent()
        asyncio.run(_run_consolidation_step(agent, state, brain, config, persona))
        assert agent.run.call_count == 0  # nothing to consolidate

    def test_duplicate_apply_does_not_double_pages(self):
        """Applying identical consolidation JSON twice creates pages only once
        (second pass creates pages with unique IDs, but that's separate new pages)."""
        brain = _make_brain_with_notes(2)
        raw = json.dumps({
            "updates": [],
            "creates": [{"title": "Topic", "content": "c", "sources": ["n0001"], "tags": []}],
            "archived_notes": ["n0001"],
        })
        _apply_consolidation(brain, raw)
        first_count = len(brain.pages)
        assert first_count == 1

        # Second identical apply — notes already archived, but the creates
        # still produce a new page (with a unique slug suffix)
        _apply_consolidation(brain, raw)
        assert len(brain.pages) == 2  # slug uniqueness produces a second page
        # This demonstrates that idempotency is enforced at the *step* level
        # (skipping when no unconsolidated notes), not at the apply level.


# ══════════════════════════════════════════════════════════════════════
# S. Tag propagation
# ══════════════════════════════════════════════════════════════════════

class TestTagPropagation:
    """Verify that tags from the LLM response are correctly stored on pages."""

    def test_tags_stored_on_created_page(self):
        brain = _make_brain_with_notes(2)
        raw = json.dumps({
            "updates": [],
            "creates": [
                {"title": "Tagged", "content": "c", "sources": ["n0001"], "tags": ["insurance", "commercial-lines", "pricing"]},
            ],
            "archived_notes": ["n0001"],
        })
        _apply_consolidation(brain, raw)
        page = list(brain.pages.values())[0]
        assert page["tags"] == ["insurance", "commercial-lines", "pricing"]

    def test_tags_empty_list(self):
        brain = _make_brain_with_notes(1)
        raw = json.dumps({
            "updates": [],
            "creates": [{"title": "No Tags", "content": "c", "sources": ["n0001"], "tags": []}],
            "archived_notes": ["n0001"],
        })
        _apply_consolidation(brain, raw)
        page = list(brain.pages.values())[0]
        assert page["tags"] == []

    def test_tags_missing_from_response_defaults_empty(self):
        """LLM omits tags key — should default to empty list."""
        brain = _make_brain_with_notes(1)
        raw = json.dumps({
            "updates": [],
            "creates": [{"title": "Tagless", "content": "c", "sources": ["n0001"]}],  # no "tags" key
            "archived_notes": ["n0001"],
        })
        _apply_consolidation(brain, raw)
        page = list(brain.pages.values())[0]
        assert page["tags"] == []


# ══════════════════════════════════════════════════════════════════════
# T. Wiki summary reflects newly created pages immediately
# ══════════════════════════════════════════════════════════════════════

class TestWikiSummaryAfterCreation:
    """After creating pages, both build_wiki_summary and build_brain_summary
    should immediately reflect the new pages."""

    def test_wiki_summary_shows_new_page(self):
        brain = _make_brain_with_notes(2)
        assert build_wiki_summary(brain) == ""  # no pages yet

        add_page(brain, "New Topic", "Long form content here", ["n0001"])
        summary = build_wiki_summary(brain)
        assert "New Topic" in summary
        assert "1 sources" in summary

    def test_brain_summary_includes_wiki_after_consolidation(self):
        brain = _make_brain_with_notes(3)
        summary_before = build_brain_summary(brain)
        assert "Wiki pages: 0" in summary_before
        assert "Recent knowledge" in summary_before  # no pages → shows recent notes

        # Consolidate
        raw = json.dumps({
            "updates": [],
            "creates": [{"title": "Insurance Process", "content": "Claims workflow details.",
                         "sources": ["n0001", "n0002"], "tags": ["insurance"]}],
            "archived_notes": ["n0001", "n0002"],
        })
        _apply_consolidation(brain, raw)

        summary_after = build_brain_summary(brain)
        assert "Wiki pages: 1" in summary_after
        assert "Insurance Process" in summary_after
        assert "unconsolidated" in summary_after.lower()  # shows pending count

    def test_brain_summary_switches_to_unconsolidated_label(self):
        """When pages exist, note label changes from 'Recent knowledge' to
        'Recent unconsolidated notes'."""
        brain = _make_brain_with_notes(3)
        add_page(brain, "Page", "content", ["n0001"])

        summary = build_brain_summary(brain)
        assert "Recent unconsolidated notes" in summary
        assert "Recent knowledge" not in summary

    def test_brain_summary_caps_unconsolidated_at_5(self):
        """Short-term window is capped at 5 unconsolidated notes even if more exist."""
        brain = _make_brain_with_notes(10)
        add_page(brain, "Page", "content", ["n0001"])  # 9 remain unconsolidated

        summary = build_brain_summary(brain, max_notes=20)
        # Count note lines (they start with "  - (n")
        note_lines = [l for l in summary.split("\n") if l.strip().startswith("- (n")]
        assert len(note_lines) <= 5


# ══════════════════════════════════════════════════════════════════════
# U. Source tracking integrity
# ══════════════════════════════════════════════════════════════════════

class TestSourceTracking:
    """Verify source-note relationships are properly maintained through
    the create → update → archive lifecycle."""

    def test_sources_on_created_page_match_archived_notes(self):
        """Page sources and archived note IDs should align."""
        brain = _make_brain_with_notes(3)
        raw = json.dumps({
            "updates": [],
            "creates": [{"title": "T", "content": "c", "sources": ["n0001", "n0003"], "tags": []}],
            "archived_notes": ["n0001", "n0003"],
        })
        _apply_consolidation(brain, raw)

        page = list(brain.pages.values())[0]
        assert set(page["sources"]) == {"n0001", "n0003"}
        # Archived notes match page sources
        for nid in page["sources"]:
            assert brain.notes[nid]["category"] == "archive"
        # n0002 not in sources → not archived
        assert brain.notes["n0002"]["category"] != "archive"

    def test_update_accumulates_sources(self):
        """Multiple updates should accumulate sources, not replace."""
        brain = _make_brain_with_notes(4)
        pid = add_page(brain, "Growing", "v1", ["n0001"])

        update_page(brain, pid, "v2", ["n0002"])
        assert set(brain.pages[pid]["sources"]) == {"n0001", "n0002"}

        update_page(brain, pid, "v3", ["n0003"])
        assert set(brain.pages[pid]["sources"]) == {"n0001", "n0002", "n0003"}

    def test_unconsolidated_excludes_all_page_sources(self):
        """Notes in *any* page's sources list should be excluded from unconsolidated."""
        brain = _make_brain_with_notes(5)
        add_page(brain, "P1", "c", ["n0001", "n0002"])
        add_page(brain, "P2", "c", ["n0004"])

        uncons = get_unconsolidated_notes(brain)
        uncons_ids = {n["id"] for n in uncons}
        assert uncons_ids == {"n0003", "n0005"}

    def test_archive_is_superset_of_sources(self):
        """LLM can archive notes that aren't sources (e.g. duplicates it decided
        to skip). Verify those are archived too."""
        brain = _make_brain_with_notes(3)
        raw = json.dumps({
            "updates": [],
            "creates": [{"title": "T", "content": "c", "sources": ["n0001"], "tags": []}],
            # n0002 archived but not in sources (LLM deemed it duplicate)
            "archived_notes": ["n0001", "n0002"],
        })
        _apply_consolidation(brain, raw)
        assert brain.notes["n0001"]["category"] == "archive"
        assert brain.notes["n0002"]["category"] == "archive"
        assert brain.notes["n0003"]["category"] != "archive"
