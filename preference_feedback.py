"""Inbox actions → brain relevance feedback (coworker preference learning)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from messaging import Message

logger = logging.getLogger(__name__)


async def apply_inbox_read_relevance_feedback(
    state_file: str,
    message: "Message",
    *,
    enabled: bool = True,
    previous_status: str | None = None,
) -> int:
    """Boost notes cited on *message* when the user marks it read (first time only).

    Uses ``payload["brain_note_ids"]`` / legacy ``note_ids``. Only runs when
    *previous_status* was ``unread`` so re-reading does not stack boosts.
    """
    if not enabled:
        return 0
    if previous_status != "unread":
        return 0

    from messaging import brain_note_ids_from_message
    from second_brain import apply_relevance_feedback, load_brain, save_brain

    ids = brain_note_ids_from_message(message)
    if not ids:
        return 0

    brain = await load_brain(state_file)
    updated = 0
    for nid in ids:
        if apply_relevance_feedback(
            brain, nid, relevant=True, reason="inbox_read",
        ):
            updated += 1
        else:
            logger.debug(
                "Inbox read feedback skipped: note %s not in brain", nid,
            )
    if updated:
        await save_brain(brain, state_file)
    return updated


async def apply_inbox_dismiss_relevance_feedback(
    state_file: str,
    message: "Message",
    *,
    enabled: bool = True,
    previous_status: str | None = None,
) -> int:
    """Demote notes cited on *message* when the user dismisses it from the inbox.

    Uses ``payload["brain_note_ids"]`` (and legacy ``note_ids``). Only runs when
    *previous_status* is ``unread`` or ``read`` so feedback is not duplicated for
    already-dismissed messages.

    Returns the number of notes updated.
    """
    if not enabled:
        return 0
    if previous_status is not None and previous_status not in ("unread", "read"):
        return 0

    from messaging import brain_note_ids_from_message
    from second_brain import apply_relevance_feedback, load_brain, save_brain

    ids = brain_note_ids_from_message(message)
    if not ids:
        return 0

    brain = await load_brain(state_file)
    updated = 0
    for nid in ids:
        if apply_relevance_feedback(
            brain, nid, relevant=False, reason="inbox_dismiss",
        ):
            updated += 1
        else:
            logger.debug(
                "Inbox dismiss feedback skipped: note %s not in brain", nid,
            )
    if updated:
        await save_brain(brain, state_file)
    return updated
