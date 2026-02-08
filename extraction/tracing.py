"""
Centralized Opik tracing module.

All tracing integration is gated behind ``OPIK_ENABLED`` so that the
extraction pipeline works identically when Opik services are not running.
"""

import logging

from config import OPIK_ENABLED, OPIK_URL, OPIK_WORKSPACE, OPIK_PROJECT_NAME

logger = logging.getLogger(__name__)

_opik_configured = False


def configure_opik() -> None:
    """Initialise the Opik client.  Safe to call multiple times."""
    global _opik_configured
    if _opik_configured or not OPIK_ENABLED:
        return
    try:
        import opik

        opik.configure(
            api_key=None,  # self-hosted, no key needed
            url_override=OPIK_URL,
            workspace=OPIK_WORKSPACE,
            project_name=OPIK_PROJECT_NAME,
        )
        _opik_configured = True
        logger.info("Opik tracing configured: %s (project=%s)", OPIK_URL, OPIK_PROJECT_NAME)
    except Exception as exc:
        logger.warning("Failed to configure Opik – tracing disabled: %s", exc)


def wrap_openai_client(client):
    """Wrap an OpenAI client with Opik auto-tracing.

    Returns the original client unchanged when Opik is disabled.
    """
    if not OPIK_ENABLED:
        return client
    try:
        from opik.integrations.openai import track_openai

        return track_openai(client)
    except Exception as exc:
        logger.warning("Could not wrap OpenAI client with Opik: %s", exc)
        return client


def track(name: str):
    """Decorator for function-level tracing.

    No-ops gracefully when Opik is disabled.
    """
    def decorator(fn):
        if not OPIK_ENABLED:
            return fn
        try:
            from opik import track as opik_track

            return opik_track(name=name)(fn)
        except Exception:
            return fn
    return decorator
