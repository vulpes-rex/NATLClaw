"""Prompt template loader for workflow steps.

Templates live in ``prompts/<mode>/<step>.txt`` as plain-text files with
Python ``str.format_map`` placeholders (e.g. ``{agent_name}``).  Using
text files instead of embedded f-strings means prompt edits don't
require touching Python code and A/B testing is straightforward.

Usage::

    from prompts import load_prompt

    text = load_prompt("second_brain", "status_check",
                       agent_name="NATLClaw", execution_count=5, ...)
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

# Root of the prompts directory, relative to this file
_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


@lru_cache(maxsize=64)
def _read_template(mode: str, step: str) -> str:
    """Read and cache a raw template from disk.

    Returns the file contents as a string, or ``""`` if the file is
    missing so callers can fall back to inline prompts.
    """
    path = _PROMPTS_DIR / mode / f"{step}.txt"
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Prompt template not found: %s", path)
        return ""
    except OSError as e:
        logger.error("Failed to read prompt template %s: %s", path, e)
        return ""


def load_prompt(mode: str, step: str, **context: object) -> str:
    """Load a prompt template and substitute ``{key}`` placeholders.

    Parameters
    ----------
    mode:
        Workflow mode directory name — ``second_brain``, ``freeform``,
        ``steps``, or ``coordinator``.
    step:
        Step name — e.g. ``status_check``, ``capture``, ``review``.
    **context:
        Key-value pairs that will replace ``{key}`` placeholders in
        the template.

    Returns
    -------
    str
        The rendered prompt.  If the template file is missing, returns
        ``""`` so callers can fall back to an inline prompt.
    """
    template = _read_template(mode, step)
    if not template:
        return ""
    try:
        return template.format_map(_SafeDict(context))
    except (KeyError, ValueError, IndexError) as e:
        logger.error("Failed to render prompt %s/%s: %s", mode, step, e)
        return ""


def clear_cache() -> None:
    """Clear the template cache (useful for hot-reload / testing)."""
    _read_template.cache_clear()


class _SafeDict(dict):
    """dict subclass that returns ``{key}`` for missing keys rather than raising.

    This prevents ``KeyError`` when a template contains a placeholder that
    the caller didn't provide — the raw ``{key}`` string is left in place,
    which is easier to debug than a traceback.
    """

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"
