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

    # Scheduler
    heartbeat_interval_sec: int = 120

    # State
    state_file: str = "data/agent_state.json"
    max_history: int = 100

    # Agent
    agent_name: str = "NATLClaw"
    persona: str = "default"
    agent_instructions: str = ""  # populated from persona if empty


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
        heartbeat_interval_sec=int(os.getenv("HEARTBEAT_INTERVAL_SEC", "120")),
        state_file=os.getenv("STATE_FILE", "data/agent_state.json"),
        max_history=int(os.getenv("MAX_HISTORY", "100")),
        agent_name=os.getenv("AGENT_NAME", "NATLClaw"),
        persona=os.getenv("PERSONA", "default"),
        agent_instructions=os.getenv("AGENT_INSTRUCTIONS", ""),
    )
