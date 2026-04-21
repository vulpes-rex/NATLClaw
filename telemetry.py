from __future__ import annotations

import logging

from config import AppConfig

logger = logging.getLogger(__name__)

_SENTRY_INITIALIZED = False
# Set when import of sentry_sdk fails so repeated init_sentry calls do not log tracebacks.
_SENTRY_SDK_MISSING = False


def init_sentry(config: AppConfig) -> bool:
    """Initialize Sentry once for the current process.

    Returns True when Sentry is enabled (or already initialized), False otherwise.
    The function is intentionally fail-safe: telemetry issues never crash the app.
    """

    global _SENTRY_INITIALIZED, _SENTRY_SDK_MISSING

    if _SENTRY_INITIALIZED:
        return True

    dsn = config.sentry_dsn.strip()
    if not dsn:
        logger.debug("Sentry disabled: SENTRY_DSN not configured.")
        return False

    if _SENTRY_SDK_MISSING:
        return False

    try:
        import sentry_sdk
    except ModuleNotFoundError:
        _SENTRY_SDK_MISSING = True
        logger.warning(
            "Sentry disabled: package sentry-sdk is not installed. "
            "Install project dependencies (e.g. pip install -e .) or: pip install 'sentry-sdk[fastapi]>=2'. "
            "Continuing without telemetry.",
        )
        return False

    try:
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration

        log_event_level = logging.WARNING if config.sentry_enable_logs else logging.ERROR
        integrations = [
            FastApiIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=log_event_level),
        ]

        sentry_sdk.init(
            dsn=dsn,
            # Mirrors the requested Sentry snippet behavior.
            send_default_pii=True,
            enable_logs=config.sentry_enable_logs,
            environment=(config.sentry_environment or "").strip() or None,
            release=(config.sentry_release or "").strip() or None,
            traces_sample_rate=config.sentry_traces_sample_rate,
            profiles_sample_rate=config.sentry_profiles_sample_rate,
            profile_session_sample_rate=config.sentry_profile_session_sample_rate,
            integrations=integrations,
        )
        _SENTRY_INITIALIZED = True
        logger.info("Sentry telemetry initialized.")
        return True
    except Exception:
        logger.warning("Sentry initialization failed; continuing without telemetry.", exc_info=True)
        return False


def send_test_exception(config: AppConfig | None = None) -> str | None:
    """Send a synthetic exception to Sentry and flush immediately.

    Without a prior ``sentry_sdk.init()``, ``capture_exception`` returns nothing useful.
    When ``config`` is passed and includes ``SENTRY_DSN``, this calls ``init_sentry`` first.
    """
    try:
        import sentry_sdk

        if config is not None:
            if not init_sentry(config):
                return None

        try:
            raise RuntimeError("NATLClaw Sentry test exception")
        except Exception as exc:
            event_id = sentry_sdk.capture_exception(exc)
        sentry_sdk.flush(timeout=3.0)
        return str(event_id) if event_id else None
    except Exception:
        logger.debug("Sentry test exception capture failed.", exc_info=True)
        return None


def start_sentry_profiler() -> bool:
    """Start Sentry's manual profiler if available."""
    try:
        import sentry_sdk

        sentry_sdk.profiler.start_profiler()
        return True
    except Exception:
        logger.debug("Sentry profiler start unavailable.", exc_info=True)
        return False


def stop_sentry_profiler() -> bool:
    """Stop Sentry's manual profiler if available."""
    try:
        import sentry_sdk

        sentry_sdk.profiler.stop_profiler()
        return True
    except Exception:
        logger.debug("Sentry profiler stop unavailable.", exc_info=True)
        return False
