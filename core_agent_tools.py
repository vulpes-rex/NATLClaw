"""Core agent tools merged with every persona’s extension tools (unless opted out).

See :func:`merge_base_and_extension_tools` and ``inheritBaseTools`` in ``mcp.json``.
"""

from __future__ import annotations

from typing import Any, Callable


def tool_name(fn: Callable[..., Any]) -> str:
    """Stable name for deduplication (persona extension overrides base on match)."""
    return getattr(fn, "__name__", type(fn).__name__)


def merge_base_and_extension_tools(
    base: list[Callable[..., Any]],
    extension: list[Callable[..., Any]],
    *,
    inherit_base: bool = True,
) -> list[Callable[..., Any]]:
    """Combine base and persona tools; **extension wins** when names collide.

    Order: non-overridden base tools first (preserving base order), then all
    extension tools (preserving extension order).
    """
    if not inherit_base:
        return list(extension)
    override_names = {tool_name(f) for f in extension}
    out: list[Callable[..., Any]] = []
    for f in base:
        if tool_name(f) not in override_names:
            out.append(f)
    out.extend(extension)
    return out


def agent_capabilities() -> str:
    """Summarize built-in agent capabilities (stable, low-token).

    Personas may expose a function with the same name to replace this string.
    """
    return (
        "NATLClaw agent: scheduler heartbeats, second-brain capture, optional MCP; "
        "persona supplies additional tools from its manifest."
    )


def get_base_tools() -> list[Callable[..., Any]]:
    """Return callables always considered for merge (see ``inheritBaseTools``)."""
    return [agent_capabilities]
