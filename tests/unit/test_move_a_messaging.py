"""Tests for Move A: bidirectional inbox (messaging.py additions)."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile

import pytest

from messaging import (
    Message,
    append_and_save_inbox,
    append_message,
    build_inbound_message_block,
    create_message,
    emit_inbound_message,
    find_message,
    format_inbox,
    get_inbound,
    get_thread,
    load_inbox,
    save_inbox,
)


# ── Helpers ───────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _state_file(tmp_path):
    """Return a fake state_file path whose parent is tmp_path."""
    return str(tmp_path / "state.json")


# ── emit_inbound_message ──────────────────────────────────────────────

class TestEmitInboundMessage:
    def test_basic_defaults(self):
        m = emit_inbound_message("Hello agent")
        assert m.body == "Hello agent"
        assert m.sender == "developer"
        assert m.addressed_to == ""
        assert m.status == "unread"
        assert m.id.startswith("m")

    def test_thread_id_equals_own_id_for_root(self):
        m = emit_inbound_message("Root message")
        assert m.thread_id == m.id

    def test_reply_inherits_thread_id(self):
        root = emit_inbound_message("First")
        reply = emit_inbound_message("Second", reply_to=root.id, thread_id=root.thread_id)
        assert reply.thread_id == root.thread_id
        assert reply.reply_to == root.id

    def test_addressed_to_set(self):
        m = emit_inbound_message("Hey", addressed_to="workspace_observer")
        assert m.addressed_to == "workspace_observer"

    def test_title_defaults_to_body_prefix(self):
        m = emit_inbound_message("A" * 100)
        assert m.title == "A" * 80

    def test_explicit_title_used(self):
        m = emit_inbound_message("long body text", title="Short title")
        assert m.title == "Short title"

    def test_urgency_propagated(self):
        m = emit_inbound_message("urgent!", urgency="high")
        assert m.urgency == "high"

    def test_backward_compat_defaults(self):
        """New fields must not break Message creation from existing dict (no new required fields)."""
        old_dict = {
            "id": "m123", "type": "fyi", "urgency": "normal",
            "title": "old", "body": "", "status": "unread",
            "requires_response": False, "task_id": "", "persona": "",
            "heartbeat": 0, "created_at": "2024-01-01T00:00:00+00:00",
            "read_at": None, "dismissed_at": None, "payload": {},
        }
        m = Message(**{k: v for k, v in old_dict.items() if k in Message.__dataclass_fields__})
        assert m.sender == "agent"
        assert m.addressed_to == ""
        assert m.thread_id == ""
        assert m.reply_to == ""


# ── get_inbound ───────────────────────────────────────────────────────

class TestGetInbound:
    def _msgs(self):
        outbound = create_message("status", "Done")          # sender="agent" (default)
        inbound_broadcast = emit_inbound_message("Hi all")   # addressed_to=""
        inbound_targeted = emit_inbound_message(             # addressed_to="obs"
            "Hey obs", addressed_to="obs"
        )
        inbound_other = emit_inbound_message(                # addressed_to="other"
            "Not for obs", addressed_to="other"
        )
        return [outbound, inbound_broadcast, inbound_targeted, inbound_other]

    def test_filters_out_agent_messages(self):
        msgs = self._msgs()
        result = get_inbound(msgs)
        assert all(m.sender != "agent" for m in result)

    def test_no_persona_filter_returns_all_inbound(self):
        msgs = self._msgs()
        result = get_inbound(msgs)
        assert len(result) == 3  # broadcast + targeted + other

    def test_persona_filter_includes_broadcast_and_targeted(self):
        msgs = self._msgs()
        result = get_inbound(msgs, addressed_to="obs")
        ids = {m.addressed_to for m in result}
        assert "" in ids        # broadcast included
        assert "obs" in ids     # targeted included
        assert "other" not in ids

    def test_returns_only_unread(self):
        m = emit_inbound_message("Msg")
        m.status = "read"
        result = get_inbound([m])
        assert result == []


# ── get_thread ────────────────────────────────────────────────────────

class TestGetThread:
    def test_groups_by_thread_id(self):
        root = emit_inbound_message("Root")
        reply1 = emit_inbound_message("R1", reply_to=root.id, thread_id=root.thread_id)
        reply2 = create_message("fyi", "Agent reply")
        reply2.thread_id = root.thread_id
        unrelated = emit_inbound_message("Other")

        thread = get_thread([root, reply1, reply2, unrelated], root.thread_id)
        assert len(thread) == 3
        assert unrelated not in thread

    def test_ordered_by_created_at(self):
        root = emit_inbound_message("A")
        reply = emit_inbound_message("B", thread_id=root.thread_id, reply_to=root.id)
        # Ensure ordering even if list is reversed
        thread = get_thread([reply, root], root.thread_id)
        assert thread[0].created_at <= thread[1].created_at

    def test_empty_when_no_match(self):
        m = emit_inbound_message("msg")
        assert get_thread([m], "nonexistent") == []


# ── inbox.json persistence ────────────────────────────────────────────

class TestInboxPersistence:
    def test_load_empty_when_file_missing(self, tmp_path):
        sf = _state_file(tmp_path)
        msgs = _run(load_inbox(sf))
        assert msgs == []

    def test_save_and_load_roundtrip(self, tmp_path):
        sf = _state_file(tmp_path)
        m = emit_inbound_message("Hello", sender="developer", addressed_to="obs")
        _run(save_inbox([m], sf))
        loaded = _run(load_inbox(sf))
        assert len(loaded) == 1
        assert loaded[0].id == m.id
        assert loaded[0].sender == "developer"
        assert loaded[0].addressed_to == "obs"
        assert loaded[0].thread_id == m.thread_id

    def test_backward_compat_load_without_new_fields(self, tmp_path):
        """inbox.json written without Move A fields loads with safe defaults."""
        sf = _state_file(tmp_path)
        inbox_path = str(tmp_path / "inbox.json")
        old_entry = {
            "id": "m999", "type": "fyi", "urgency": "normal",
            "title": "old msg", "body": "body", "status": "unread",
            "requires_response": False, "task_id": "", "persona": "",
            "heartbeat": 0, "created_at": "2024-01-01T00:00:00+00:00",
            "read_at": None, "dismissed_at": None, "payload": {},
        }
        with open(inbox_path, "w") as f:
            json.dump([old_entry], f)
        msgs = _run(load_inbox(sf))
        assert len(msgs) == 1
        assert msgs[0].sender == "agent"   # default
        assert msgs[0].thread_id == ""     # default

    def test_append_and_save_deduplicates(self, tmp_path):
        sf = _state_file(tmp_path)
        m = emit_inbound_message("once")
        _run(append_and_save_inbox(m, sf))
        appended = _run(append_and_save_inbox(m, sf))
        assert appended is False
        loaded = _run(load_inbox(sf))
        assert len(loaded) == 1

    def test_append_and_save_returns_true_on_new(self, tmp_path):
        sf = _state_file(tmp_path)
        m = emit_inbound_message("new")
        appended = _run(append_and_save_inbox(m, sf))
        assert appended is True


# ── build_inbound_message_block ───────────────────────────────────────

class TestBuildInboundMessageBlock:
    def test_empty_when_no_inbound(self):
        outbound = create_message("status", "Done")
        block = build_inbound_message_block([outbound])
        assert block == ""

    def test_empty_when_all_read(self):
        m = emit_inbound_message("Msg")
        m.status = "read"
        block = build_inbound_message_block([m])
        assert block == ""

    def test_contains_message_id_and_sender(self):
        m = emit_inbound_message("Tell me about the PR", sender="developer")
        block = build_inbound_message_block([m])
        assert m.id in block
        assert "developer" in block

    def test_includes_reply_hint(self):
        root = emit_inbound_message("Question")
        reply = emit_inbound_message("Follow-up", reply_to=root.id, thread_id=root.thread_id)
        block = build_inbound_message_block([reply])
        assert root.id in block  # re: <original_id>

    def test_persona_filter_applied(self):
        m_obs = emit_inbound_message("For obs", addressed_to="obs")
        m_other = emit_inbound_message("For other", addressed_to="other")
        block = build_inbound_message_block([m_obs, m_other], persona_name="obs")
        assert m_obs.id in block
        assert m_other.id not in block

    def test_reply_instruction_present(self):
        m = emit_inbound_message("What's the status?")
        block = build_inbound_message_block([m])
        assert "REPLY TO" in block

    def test_respects_max_messages(self):
        msgs = [emit_inbound_message(f"msg {i}") for i in range(10)]
        block = build_inbound_message_block(msgs, max_messages=3)
        # Only 3 message IDs should appear
        present = [m for m in msgs if m.id in block]
        assert len(present) == 3


# ── format_inbox direction arrows ─────────────────────────────────────

class TestFormatInboxDirectionArrows:
    def test_outbound_shows_left_arrow(self):
        m = create_message("status", "Done")  # sender="agent"
        result = format_inbox([m], show_read=True)
        assert "<-" in result

    def test_inbound_shows_right_arrow(self):
        m = emit_inbound_message("Hello")  # sender="developer"
        result = format_inbox([m], show_read=True)
        assert "->" in result

    def test_addressed_to_shown_when_set(self):
        m = emit_inbound_message("Hey", addressed_to="obs")
        result = format_inbox([m], show_read=True)
        assert "@obs" in result

    def test_reply_to_shown_when_set(self):
        root = emit_inbound_message("Root")
        reply = emit_inbound_message("Reply", reply_to=root.id, thread_id=root.thread_id)
        result = format_inbox([reply], show_read=True)
        assert f"re:{root.id}" in result


# ── _extract_replies (workflow helper) ───────────────────────────────

class TestExtractReplies:
    def test_single_reply(self):
        from workflow import _extract_replies
        text = "REPLY TO m1a2b3: I looked into it and found X."
        replies = _extract_replies(text)
        assert len(replies) == 1
        assert replies[0]["reply_to"] == "m1a2b3"
        assert "found X" in replies[0]["body"]

    def test_multiple_replies(self):
        from workflow import _extract_replies
        text = (
            "REPLY TO mAAA: First answer.\n"
            "REPLY TO mBBB: Second answer."
        )
        replies = _extract_replies(text)
        assert len(replies) == 2
        ids = {r["reply_to"] for r in replies}
        assert "mAAA" in ids
        assert "mBBB" in ids

    def test_no_reply_blocks(self):
        from workflow import _extract_replies
        text = "Nothing to reply to here. Just a normal status update."
        assert _extract_replies(text) == []

    def test_case_insensitive(self):
        from workflow import _extract_replies
        text = "reply to mXXX: lowercase works too"
        replies = _extract_replies(text)
        assert len(replies) == 1
        assert replies[0]["reply_to"] == "mXXX"

    def test_empty_body_excluded(self):
        from workflow import _extract_replies
        text = "REPLY TO mYYY: "
        replies = _extract_replies(text)
        assert replies == []
