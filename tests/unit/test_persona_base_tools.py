"""Tests for core base tools + persona extension merge."""

from __future__ import annotations

import json

from core_agent_tools import (
    agent_capabilities,
    get_base_tools,
    merge_base_and_extension_tools,
    tool_name,
)
from persona_loader import load_persona


def test_tool_name_uses_function_name():
    def sample():
        return 1

    assert tool_name(sample) == "sample"


def test_merge_extension_only_when_inherit_false():
    def a():
        return "a"

    def b():
        return "b"

    base = [a]
    ext = [b]
    out = merge_base_and_extension_tools(base, ext, inherit_base=False)
    assert out == [b]


def test_merge_base_plus_extension_preserves_order_persona_last():
    def x():
        return 0

    def y():
        return 1

    out = merge_base_and_extension_tools([x], [y], inherit_base=True)
    assert out == [x, y]


def test_merge_persona_overrides_base_on_same_name():
    def base_fn():
        return "base"

    def ext_fn():
        return "ext"

    base_fn.__name__ = "shared"
    ext_fn.__name__ = "shared"

    merged = merge_base_and_extension_tools([base_fn], [ext_fn], inherit_base=True)
    assert len(merged) == 1
    assert merged[0] is ext_fn
    assert merged[0]() == "ext"


def test_get_base_tools_includes_agent_capabilities():
    names = [tool_name(f) for f in get_base_tools()]
    assert "agent_capabilities" in names


def test_load_persona_merged_includes_base_tool():
    p = load_persona("python_developer")
    names = [tool_name(t) for t in p.tools]
    assert "agent_capabilities" in names
    ext_names = [tool_name(t) for t in p.extension_tools]
    assert "agent_capabilities" not in ext_names
    assert p.inherit_base_tools is True


def test_inline_inherit_base_tools_false_excludes_core_tool(tmp_path):
    """Persona with inheritBaseTools false should not surface get_base_tools()."""
    (tmp_path / "ins.md").write_text("# P\n", encoding="utf-8")
    cfg = {
        "mcpServers": {},
        "personas": {
            "only_ext": {
                "description": "t",
                "instructions": "ins.md",
                "heartbeatTask": "task",
                "inheritBaseTools": False,
                "tools": {
                    "module": "personas.python_developer.tools",
                    "functions": ["list_files"],
                },
            }
        },
    }
    mcp_path = tmp_path / "mcp.json"
    mcp_path.write_text(json.dumps(cfg), encoding="utf-8")
    p = load_persona("only_ext", config_path=str(mcp_path))
    names = [tool_name(t) for t in p.tools]
    assert "list_files" in names
    assert "agent_capabilities" not in names
    assert p.inherit_base_tools is False


def test_agent_capabilities_callable():
    assert "NATLClaw" in agent_capabilities()
