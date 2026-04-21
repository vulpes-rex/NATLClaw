"""Tests for inbox dismiss → brain relevance feedback."""

from __future__ import annotations

import asyncio

import pytest

from messaging import BRAIN_NOTE_IDS_KEY, brain_note_ids_from_message, create_message
from preference_feedback import (
    apply_inbox_dismiss_relevance_feedback,
    apply_inbox_read_relevance_feedback,
)
from second_brain import add_note, load_brain, save_brain


def _run(coro):
    return asyncio.run(coro)


def test_apply_inbox_read_boosts_cited_note(tmp_path):
    state_file = str(tmp_path / "agent_state.json")
    brain = _run(load_brain(state_file))
    nid = add_note(brain, "content", summary="s")
    _run(save_brain(brain, state_file))

    msg = create_message("fyi", "x", payload={BRAIN_NOTE_IDS_KEY: [nid]})
    n = _run(
        apply_inbox_read_relevance_feedback(
            state_file, msg, enabled=True, previous_status="unread",
        )
    )
    assert n == 1

    brain2 = _run(load_brain(state_file))
    assert brain2.notes[nid]["positive_feedback"] >= 1


def test_apply_inbox_read_skips_non_unread(tmp_path):
    state_file = str(tmp_path / "agent_state.json")
    brain = _run(load_brain(state_file))
    nid = add_note(brain, "c")
    _run(save_brain(brain, state_file))
    msg = create_message("fyi", "x", payload={BRAIN_NOTE_IDS_KEY: [nid]})
    n = _run(
        apply_inbox_read_relevance_feedback(
            state_file, msg, previous_status="read",
        )
    )
    assert n == 0


def test_apply_inbox_dismiss_demotes_cited_note(tmp_path):
    state_file = str(tmp_path / "agent_state.json")
    brain = _run(load_brain(state_file))
    nid = add_note(brain, "content", summary="s")
    _run(save_brain(brain, state_file))

    msg = create_message("fyi", "x", payload={BRAIN_NOTE_IDS_KEY: [nid]})
    n = _run(
        apply_inbox_dismiss_relevance_feedback(
            state_file, msg, enabled=True, previous_status="read",
        )
    )
    assert n == 1

    brain2 = _run(load_brain(state_file))
    assert brain2.notes[nid]["negative_feedback"] >= 1


def test_apply_skips_when_disabled(tmp_path):
    state_file = str(tmp_path / "agent_state.json")
    brain = _run(load_brain(state_file))
    nid = add_note(brain, "c")
    _run(save_brain(brain, state_file))
    msg = create_message("fyi", "x", payload={BRAIN_NOTE_IDS_KEY: [nid]})
    n = _run(
        apply_inbox_dismiss_relevance_feedback(
            state_file, msg, enabled=False, previous_status="read",
        )
    )
    assert n == 0


def test_apply_skips_wrong_previous_status(tmp_path):
    msg = create_message("fyi", "x", payload={BRAIN_NOTE_IDS_KEY: ["n0001"]})
    n = _run(
        apply_inbox_dismiss_relevance_feedback(
            str(tmp_path / "agent_state.json"), msg, previous_status="dismissed",
        )
    )
    assert n == 0


def test_brain_note_ids_from_message_legacy_note_ids():
    m = create_message("fyi", "t", payload={"note_ids": ["n1", "n2"]})
    assert brain_note_ids_from_message(m) == ["n1", "n2"]


def test_brain_note_ids_merges_keys():
    m = create_message(
        "fyi",
        "t",
        payload={BRAIN_NOTE_IDS_KEY: ["n1"], "note_ids": ["n1", "n2"]},
    )
    assert brain_note_ids_from_message(m) == ["n1", "n2"]
