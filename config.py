from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


@dataclass
class AppConfig:
    """Application configuration loaded from environment variables."""

    # LLM Provider
    provider: str = "copilot"
    model: str = "claude-sonnet-4"
    project_endpoint: str = ""
    openai_api_key: str = ""
    ollama_host: str = "http://localhost:11434"

    # GitHub Copilot (PAT-based CLI auth)
    github_pat: str = ""  # or use `gh auth login`

    # OpenRouter
    openrouter_api_key: str = ""

    # Azure OpenAI
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_deployment: str = ""
    azure_openai_api_version: str = "2024-06-01"
    azure_openai_embedding_deployment: str = ""
    azure_openai_embedding_api_version: str = "2023-05-15"

    # Scheduler
    heartbeat_interval_sec: int = 120

    # State
    state_file: str = "data/agent_state.json"
    max_history: int = 100

    # Agent
    agent_name: str = "NATLClaw"
    persona: str = "default"
    agent_instructions: str = ""  # populated from persona if empty


VALID_PROVIDERS = ("copilot", "foundry", "openai", "openrouter", "azure_openai", "ollama")


def validate_config(config: AppConfig) -> list[str]:
    """Return a list of validation errors, empty if config is valid."""
    errors = []
    if config.heartbeat_interval_sec < 10:
        errors.append(f"heartbeat_interval_sec={config.heartbeat_interval_sec} is too low (min 10)")
    if config.provider not in VALID_PROVIDERS:
        errors.append(f"Unknown provider: {config.provider!r}")
    if config.provider == "foundry" and not config.project_endpoint:
        errors.append("AZURE_AI_PROJECT_ENDPOINT is required for provider=foundry")
    if config.provider == "openai" and not config.openai_api_key:
        errors.append("OPENAI_API_KEY is required for provider=openai")
    if config.provider == "openrouter" and not config.openrouter_api_key:
        errors.append("OPENROUTER_API_KEY is required for provider=openrouter")
    if config.provider == "azure_openai":
        if not config.azure_openai_endpoint:
            errors.append("AZURE_OPENAI_ENDPOINT is required for provider=azure_openai")
        if not config.azure_openai_api_key:
            errors.append("AZURE_OPENAI_API_KEY is required for provider=azure_openai")
        if not config.azure_openai_deployment:
            errors.append("AZURE_OPENAI_DEPLOYMENT is required for provider=azure_openai")
    if config.max_history < 1:
        errors.append(f"max_history={config.max_history} must be >= 1")
    return errors


def load_config(env_path: str = ".env") -> AppConfig:
    """Load configuration from .env file and environment variables."""
    load_dotenv(env_path)

    return AppConfig(
        provider=os.getenv("PROVIDER", "copilot").lower(),
        model=os.getenv("GITHUB_COPILOT_MODEL")
        or os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME")
        or os.getenv("OPENAI_MODEL")
        or os.getenv("OLLAMA_MODEL")
        or "claude-sonnet-4",
        project_endpoint=os.getenv("AZURE_AI_PROJECT_ENDPOINT", ""),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        github_pat=os.getenv("GITHUB_PAT", ""),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
        azure_openai_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", ""),
        azure_openai_api_key=os.getenv("AZURE_OPENAI_API_KEY", ""),
        azure_openai_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT", ""),
        azure_openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-06-01"),
        azure_openai_embedding_deployment=os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", ""),
        azure_openai_embedding_api_version=os.getenv("AZURE_OPENAI_EMBEDDING_API_VERSION", "2023-05-15"),
        heartbeat_interval_sec=int(os.getenv("HEARTBEAT_INTERVAL_SEC", "120")),
        state_file=os.getenv("STATE_FILE", "data/agent_state.json"),
        max_history=int(os.getenv("MAX_HISTORY", "100")),
        agent_name=os.getenv("AGENT_NAME", "NATLClaw"),
        persona=os.getenv("PERSONA", "default"),
        agent_instructions=os.getenv("AGENT_INSTRUCTIONS", ""),
    )
