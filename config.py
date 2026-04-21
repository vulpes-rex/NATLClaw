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
    watch_path: str = "."
    max_events_per_heartbeat: int = 50
    queue_depth_warn_threshold: int = 200
    # Coworker: rank injected brain notes by relevance to active work (Phase 2b)
    brain_summary_relevance_from_active_work: bool = True
    brain_summary_semantic: bool = True
    # When True, dismissing inbox messages with ``brain_note_ids`` demotes those notes
    inbox_dismiss_brain_feedback: bool = True
    # When True, marking a message read (CLI show / API read) boosts cited notes once
    inbox_read_brain_feedback: bool = True

    # State
    state_file: str = "data/agent_state.json"
    max_history: int = 100

    # Agent
    agent_name: str = "NATLClaw"
    persona: str = "default"
    agent_instructions: str = ""  # populated from persona if empty

    # API authentication (optional — if set, all API endpoints require Bearer token)
    api_key: str = ""

    # Proactive notifications
    # Comma-separated HTTP(S) URLs; messages at/above min urgency are POSTed here.
    notification_webhooks: tuple[str, ...] = field(default_factory=tuple)
    notification_os_toast: bool = False
    notification_min_urgency: str = "normal"  # low | normal | high | urgent

    # Task negotiation (Move B): when True, agents can accept/redirect tasks before starting
    task_negotiation_enabled: bool = False

    # ── Azure DevOps connector ─────────────────────────────────────────
    # Cloud:   https://dev.azure.com/{org}
    # On-prem: https://tfs.company.com/DefaultCollection
    ado_url: str = ""
    ado_pat: str = ""            # Personal Access Token
    ado_org: str = ""            # organization name (informational only)
    ado_project: str = ""        # project name
    ado_team: str = ""           # team name (for iteration queries)
    ado_api_version: str = "7.1"
    # Comma-separated ADO assignee email(s) to pull work items for
    ado_assignees: tuple[str, ...] = field(default_factory=tuple)

    # ── Microsoft Graph (Teams + Outlook) ─────────────────────────────
    ms_tenant_id: str = ""
    ms_client_id: str = ""
    ms_client_secret: str = ""

    # ── Teams connector ────────────────────────────────────────────────
    teams_webhook_url: str = ""   # Incoming webhook URL (send-only, no app reg)
    teams_team_id: str = ""       # Team ID (Graph mode)
    teams_channel_id: str = ""    # Channel ID (Graph mode)

    # ── Outlook connector ──────────────────────────────────────────────
    outlook_sender: str = ""      # UPN of service mailbox, e.g. agent@company.com
    outlook_reply_to: str = ""    # Optional reply-to address
    # Comma-separated recipients for standup email
    outlook_standup_recipients: tuple[str, ...] = field(default_factory=tuple)

    # Standup protocol
    standup_hour: int = 9          # Hour of day (local time) to trigger standup

    # Sprint context injection (Feature F) — requires ADO connector
    sprint_context_enabled: bool = False
    sprint_context_ttl_minutes: int = 30  # How often to re-fetch from ADO

    # Surface ingress (OpenClaw MVP bridge)
    surface_ingress_enabled: bool = False
    surface_channels_enabled: tuple[str, ...] = field(default_factory=tuple)

    # Telemetry (optional)
    sentry_dsn: str = ""
    sentry_environment: str = "development"
    sentry_release: str = ""
    sentry_enable_logs: bool = False
    sentry_traces_sample_rate: float = 0.0
    sentry_profiles_sample_rate: float = 0.0
    sentry_profile_session_sample_rate: float = 0.0


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
    if config.max_events_per_heartbeat < 1:
        errors.append(
            f"max_events_per_heartbeat={config.max_events_per_heartbeat} must be >= 1"
        )
    if config.queue_depth_warn_threshold < 1:
        errors.append(
            f"queue_depth_warn_threshold={config.queue_depth_warn_threshold} must be >= 1"
        )
    if not 0.0 <= config.sentry_traces_sample_rate <= 1.0:
        errors.append(
            "SENTRY_TRACES_SAMPLE_RATE must be between 0.0 and 1.0"
        )
    if not 0.0 <= config.sentry_profiles_sample_rate <= 1.0:
        errors.append(
            "SENTRY_PROFILES_SAMPLE_RATE must be between 0.0 and 1.0"
        )
    if not 0.0 <= config.sentry_profile_session_sample_rate <= 1.0:
        errors.append(
            "SENTRY_PROFILE_SESSION_SAMPLE_RATE must be between 0.0 and 1.0"
        )
    return errors


def load_config(env_path: str = ".env") -> AppConfig:
    """Load configuration from .env file and environment variables."""
    load_dotenv(env_path)

    def _parse_bool(value: str | None, default: bool = False) -> bool:
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    def _parse_float(value: str | None, default: float) -> float:
        if value is None:
            return default
        try:
            return float(value)
        except ValueError:
            return default

    channels_raw = os.getenv("SURFACE_CHANNELS_ENABLED", "")
    channels = tuple(
        part.strip()
        for part in channels_raw.split(",")
        if part.strip()
    )

    webhooks_raw = os.getenv("NOTIFICATION_WEBHOOKS", "")
    webhooks = tuple(
        part.strip()
        for part in webhooks_raw.split(",")
        if part.strip()
    )

    def _parse_tuple(raw: str | None) -> tuple[str, ...]:
        if not raw:
            return ()
        return tuple(p.strip() for p in raw.split(",") if p.strip())

    ado_assignees = _parse_tuple(os.getenv("ADO_ASSIGNEES", ""))
    standup_recipients = _parse_tuple(os.getenv("OUTLOOK_STANDUP_RECIPIENTS", ""))

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
        max_events_per_heartbeat=int(os.getenv("MAX_EVENTS_PER_HEARTBEAT", "50")),
        queue_depth_warn_threshold=int(os.getenv("QUEUE_DEPTH_WARN_THRESHOLD", "200")),
        brain_summary_relevance_from_active_work=_parse_bool(
            os.getenv("BRAIN_SUMMARY_RELEVANCE_FROM_ACTIVE_WORK"), default=True
        ),
        brain_summary_semantic=_parse_bool(os.getenv("BRAIN_SUMMARY_SEMANTIC"), default=True),
        inbox_dismiss_brain_feedback=_parse_bool(
            os.getenv("INBOX_DISMISS_BRAIN_FEEDBACK"), default=True
        ),
        inbox_read_brain_feedback=_parse_bool(
            os.getenv("INBOX_READ_BRAIN_FEEDBACK"), default=True
        ),
        state_file=os.getenv("STATE_FILE", "data/agent_state.json"),
        max_history=int(os.getenv("MAX_HISTORY", "100")),
        agent_name=os.getenv("AGENT_NAME", "NATLClaw"),
        persona=os.getenv("PERSONA", "default"),
        agent_instructions=os.getenv("AGENT_INSTRUCTIONS", ""),
        api_key=os.getenv("NATL_API_KEY", ""),
        notification_webhooks=webhooks,
        notification_os_toast=_parse_bool(os.getenv("NOTIFICATION_OS_TOAST"), default=False),
        notification_min_urgency=os.getenv("NOTIFICATION_MIN_URGENCY", "normal"),
        task_negotiation_enabled=_parse_bool(os.getenv("TASK_NEGOTIATION_ENABLED"), default=False),
        # ADO connector
        ado_url=os.getenv("ADO_URL", ""),
        ado_pat=os.getenv("ADO_PAT", ""),
        ado_org=os.getenv("ADO_ORG", ""),
        ado_project=os.getenv("ADO_PROJECT", ""),
        ado_team=os.getenv("ADO_TEAM", ""),
        ado_api_version=os.getenv("ADO_API_VERSION", "7.1"),
        ado_assignees=ado_assignees,
        # Microsoft Graph
        ms_tenant_id=os.getenv("MS_TENANT_ID", ""),
        ms_client_id=os.getenv("MS_CLIENT_ID", ""),
        ms_client_secret=os.getenv("MS_CLIENT_SECRET", ""),
        # Teams connector
        teams_webhook_url=os.getenv("TEAMS_WEBHOOK_URL", ""),
        teams_team_id=os.getenv("TEAMS_TEAM_ID", ""),
        teams_channel_id=os.getenv("TEAMS_CHANNEL_ID", ""),
        # Outlook connector
        outlook_sender=os.getenv("OUTLOOK_SENDER", ""),
        outlook_reply_to=os.getenv("OUTLOOK_REPLY_TO", ""),
        outlook_standup_recipients=standup_recipients,
        # Standup
        standup_hour=int(os.getenv("STANDUP_HOUR", "9")),
        # Sprint context
        sprint_context_enabled=_parse_bool(os.getenv("SPRINT_CONTEXT_ENABLED"), default=False),
        sprint_context_ttl_minutes=int(os.getenv("SPRINT_CONTEXT_TTL_MINUTES", "30")),
        # Surface ingress
        surface_ingress_enabled=_parse_bool(os.getenv("SURFACE_INGRESS_ENABLED"), default=False),
        surface_channels_enabled=channels,
        sentry_dsn=os.getenv("SENTRY_DSN", ""),
        sentry_environment=os.getenv("SENTRY_ENVIRONMENT", "development"),
        sentry_release=os.getenv("SENTRY_RELEASE", ""),
        sentry_enable_logs=_parse_bool(os.getenv("SENTRY_ENABLE_LOGS"), default=False),
        sentry_traces_sample_rate=_parse_float(os.getenv("SENTRY_TRACES_SAMPLE_RATE"), 0.0),
        sentry_profiles_sample_rate=_parse_float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE"), 0.0),
        sentry_profile_session_sample_rate=_parse_float(
            os.getenv("SENTRY_PROFILE_SESSION_SAMPLE_RATE"), 0.0
        ),
    )
