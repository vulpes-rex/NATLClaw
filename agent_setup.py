from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Sequence

from agent_framework_github_copilot import GitHubCopilotAgent
from copilot import MCPLocalServerConfig, MCPRemoteServerConfig, PermissionHandler

if TYPE_CHECKING:
    from agent_framework._agents import BaseAgent

from config import AppConfig
import logging

_LOGGER = logging.getLogger(__name__)


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
    try:
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
    except (KeyError, ValueError, TypeError) as e:
        _LOGGER.error("Failed to build MCP servers: %s", str(e))
        _LOGGER.debug("_build_mcp_servers error details:", exc_info=True)
        # Return empty dict to allow fallback or continue without MCP
        return {}
    return result


def _create_secure_permission_handler() -> Callable[[dict], bool]:
    """Create a secure permission handler with user confirmation for sensitive operations.

    Returns:
        A callable that evaluates permission requests based on predefined policies.
    """
    # Define sensitive permission types that require user confirmation
    SENSITIVE_PERMISSIONS = {
        "io/fs/write",      # File system write access
        "io/fs/scan",       # File system scan access
        "system/environment", # Access to environment variables
        "network/request",  # Network request capability
        "process/*",        # Process execution
        "fs/*",             # General file system access
        "os/*",             # Operating system access
        "storage/*",        # Storage access
    }

    def permission_handler(permission_request: dict) -> bool:
        """Handle permission requests securely.

        Args:
            permission_request: A dictionary containing permission details.

        Returns:
            True if permission is granted, False if denied.
        """
        try:
            permission_name = permission_request.get("name", "")

            # Check if this is a sensitive permission
            is_sensitive = any(
                SENSITIVE_PERMISSIONS.intersection(
                    {permission_name, permission_name.split("/")[0] + "/*"}
                )
            )

            if not is_sensitive:
                # Automatically approve non-sensitive permissions
                _LOGGER.debug("Auto-approving non-sensitive permission: %s", permission_name)
                return True

            # For sensitive permissions, ask for user confirmation
            _LOGGER.warning("SENSITIVE PERMISSION REQUESTED: %s", permission_name)
            print(f"\n⚠️  Security Notice: Permission requested for '{permission_name}'")

            # Provide context about what's being requested
            description = permission_request.get("description", "No description provided")
            print(f"Description: {description}")

            try:
                # Get user confirmation
                response = input("Do you want to grant this permission? (yes/no): ").strip().lower()
                granted = response in ("yes", "y", "true", "t")
            except (EOFError, KeyboardInterrupt):
                # If input is not available (non-interactive) or interrupted, deny permission
                _LOGGER.warning("Permission request interrupted or non-interactive environment, denying: %s", permission_name)
                print("\nPermission request interrupted. Denying by default. ❌\n")
                return False

            # Log the decision
            if granted:
                _LOGGER.info("User granted sensitive permission: %s", permission_name)
                print("Permission granted. ✅\n")
            else:
                _LOGGER.warning("User DENIED sensitive permission: %s", permission_name)
                print("Permission denied. ❌\n")

            return granted
        except Exception as e:
            _LOGGER.error("Unexpected error during permission handling: %s", str(e))
            _LOGGER.debug("Permission handler error details:", exc_info=True)
            print("\nAn unexpected error occurred. Denying by default. ❌\n")
            return False

    return permission_handler


def create_agent(
    config: AppConfig,
    instructions: str,
    tools: Sequence[Callable[..., Any]] | None = None,
    mcp_servers: dict[str, dict] | None = None,
) -> BaseAgent:
    """Build an agent for the configured provider, with optional tools and MCP servers."""
    tool_list = list(tools) if tools else []

    try:
        if config.provider == "copilot":
            # If a GitHub PAT is available (env var or `gh auth token`),
            # use the OpenAI-compatible Copilot endpoint directly.  This
            # mirrors CodeIntel's GitHubCopilotLlmProvider approach and
            # works from any terminal without VS Code.
            try:
                from copilot_auth import CopilotTokenManager, COPILOT_BASE_URL, COPILOT_DEFAULT_HEADERS
                token_mgr = CopilotTokenManager(config.github_pat or None)
                from agent_framework import Agent
                from agent_framework.openai import OpenAIChatCompletionClient

                return Agent(
                    client=OpenAIChatCompletionClient(
                        model=config.model,
                        api_key=token_mgr.get_token,
                        base_url=COPILOT_BASE_URL,
                        default_headers=COPILOT_DEFAULT_HEADERS,
                    ),
                    name=config.agent_name,
                    instructions=instructions,
                    tools=tool_list or None,
                )
            except RuntimeError:
                _LOGGER.info("No GitHub token found, falling back to GitHubCopilotAgent SDK")

            # Fallback: use the GitHubCopilotAgent SDK (requires VS Code Copilot context)
            opts: dict[str, Any] = {
                "model": config.model,
                "on_permission_request": _create_secure_permission_handler(),
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

        if config.provider == "openrouter":
            from agent_framework import Agent
            from agent_framework.openai import OpenAIChatCompletionClient

            return Agent(
                client=OpenAIChatCompletionClient(
                    api_key=config.openrouter_api_key,
                    base_url="https://openrouter.ai/api/v1",
                    model=config.model,
                ),
                name=config.agent_name,
                instructions=instructions,
                tools=tool_list or None,
            )

        if config.provider == "azure_openai":
            from agent_framework import Agent
            from agent_framework.openai import OpenAIChatCompletionClient

            return Agent(
                client=OpenAIChatCompletionClient(
                    api_key=config.azure_openai_api_key,
                    azure_endpoint=config.azure_openai_endpoint,
                    api_version=config.azure_openai_api_version,
                    model=config.azure_openai_deployment,
                ),
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

        raise ValueError(f"Unknown provider: {config.provider!r}. Use copilot|foundry|openai|openrouter|azure_openai|ollama.")
    except Exception as e:
        _LOGGER.error("Failed to create agent: %s", str(e))
        _LOGGER.debug("Agent creation error details:", exc_info=True)
        raise  # Re-raise after logging so caller can handle
