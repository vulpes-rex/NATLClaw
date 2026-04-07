from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Sequence

from agent_framework_github_copilot import GitHubCopilotAgent
from copilot import MCPLocalServerConfig, MCPRemoteServerConfig, PermissionHandler

if TYPE_CHECKING:
    from agent_framework._agents import BaseAgent

from config import AppConfig


def _build_mcp_servers(
    raw: dict[str, dict],
) -> dict[str, MCPLocalServerConfig | MCPRemoteServerConfig]:
    """Convert raw persona config dicts into typed MCP server configs.

    Notes:
    - ``tools`` defaults to ``["*"]`` (all tools) when omitted. An explicit
      empty list ``[]`` means *no tools* per the SDK spec.
    - ``timeout`` in persona config is in **seconds**; the SDK expects
      **milliseconds**, so we multiply by 1000.
    """
    result: dict[str, MCPLocalServerConfig | MCPRemoteServerConfig] = {}
    for name, cfg in raw.items():
        server_type = cfg.get("type", "stdio")
        raw_tools = cfg.get("tools")
        tools = raw_tools if raw_tools is not None else ["*"]
        timeout_ms = int(cfg.get("timeout", 30) * 1000)

        if server_type in ("local", "stdio"):
            entry: MCPLocalServerConfig = {
                "command": cfg["command"],
                "args": cfg.get("args", []),
                "tools": tools,
            }
            if cfg.get("env"):
                entry["env"] = cfg["env"]
            if cfg.get("cwd"):
                entry["cwd"] = cfg["cwd"]
            if timeout_ms:
                entry["timeout"] = timeout_ms
            result[name] = entry
        elif server_type in ("http", "sse"):
            remote: MCPRemoteServerConfig = {
                "url": cfg["url"],
                "type": server_type,
                "tools": tools,
            }
            if cfg.get("headers"):
                remote["headers"] = cfg["headers"]
            if timeout_ms:
                remote["timeout"] = timeout_ms
            result[name] = remote
    return result


def create_agent(
    config: AppConfig,
    instructions: str,
    tools: Sequence[Callable[..., Any]] | None = None,
    mcp_servers: dict[str, dict] | None = None,
) -> BaseAgent:
    """Build an agent for the configured provider, with optional tools and MCP servers."""
    tool_list = list(tools) if tools else []

    if config.provider == "copilot":
        opts: dict[str, Any] = {
            "model": config.model,
            "on_permission_request": PermissionHandler.approve_all,
        }
        if mcp_servers:
            opts["mcp_servers"] = _build_mcp_servers(mcp_servers)

        return GitHubCopilotAgent(
            instructions=instructions,
            name=config.agent_name,
            tools=tool_list or None,
            default_options=opts,
        )

    if config.provider == "foundry":
        from agent_framework import Agent
        from agent_framework.foundry import FoundryChatClient
        from azure.identity import AzureCliCredential

        return Agent(
            client=FoundryChatClient(
                credential=AzureCliCredential(),
                project_endpoint=config.project_endpoint,
                model=config.model,
            ),
            name=config.agent_name,
            instructions=instructions,
            tools=tool_list or None,
        )

    if config.provider == "openai":
        from agent_framework import Agent
        from agent_framework.openai import OpenAIChatClient

        return Agent(
            client=OpenAIChatClient(api_key=config.openai_api_key, model=config.model),
            name=config.agent_name,
            instructions=instructions,
            tools=tool_list or None,
        )

    if config.provider == "ollama":
        from agent_framework import Agent
        from agent_framework.ollama import OllamaChatClient

        return Agent(
            client=OllamaChatClient(host=config.ollama_host, model=config.model),
            name=config.agent_name,
            instructions=instructions,
            tools=tool_list or None,
        )

    raise ValueError(f"Unknown provider: {config.provider!r}. Use copilot|foundry|openai|ollama.")
