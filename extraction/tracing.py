"""
Centralized tracing integration for extraction/runtime services.

The cross-repository trace contract is vendor-neutral:
``SEOCHO_TRACE_BACKEND=none|console|jsonl|opik``.

This module currently activates the Opik exporter path only when
``SEOCHO_TRACE_BACKEND=opik``. Other values keep runtime behavior intact
and simply disable Opik-specific instrumentation.
"""

import logging
import inspect

from config import (
    OPIK_API_KEY,
    OPIK_ENABLED,
    OPIK_MODE,
    OPIK_PROJECT_NAME,
    OPIK_URL,
    OPIK_WORKSPACE,
    TRACE_BACKEND,
)

logger = logging.getLogger(__name__)

_opik_configured = False


def configure_opik() -> None:
    """Initialise the Opik client.  Safe to call multiple times."""
    global _opik_configured
    if _opik_configured or not OPIK_ENABLED:
        return
    try:
        import opik
        configure_params = inspect.signature(opik.configure).parameters
        kwargs = {}

        # Opik configure signature changed across releases:
        # - legacy: url_override / project_name
        # - current: url (project is selected via runtime context/env)
        if "url_override" in configure_params:
            kwargs["url_override"] = OPIK_URL
        elif "url" in configure_params:
            kwargs["url"] = OPIK_URL

        # Newer Opik SDKs can explicitly run in self-hosted mode.
        if OPIK_MODE == "self_host":
            if "use_local" in configure_params:
                kwargs["use_local"] = True
            elif "api_key" in configure_params:
                kwargs["api_key"] = None  # legacy self-hosted path
        elif OPIK_API_KEY and "api_key" in configure_params:
            kwargs["api_key"] = OPIK_API_KEY

        if "workspace" in configure_params:
            kwargs["workspace"] = OPIK_WORKSPACE
        if "project_name" in configure_params:
            kwargs["project_name"] = OPIK_PROJECT_NAME

        opik.configure(**kwargs)
        _opik_configured = True
        logger.info(
            "Opik tracing configured: backend=%s mode=%s url=%s project=%s",
            TRACE_BACKEND,
            OPIK_MODE,
            OPIK_URL,
            OPIK_PROJECT_NAME,
        )
    except Exception as exc:
        logger.warning("Failed to configure Opik – tracing disabled: %s", exc)


def wrap_openai_client(client):
    """Wrap an OpenAI client with Opik auto-tracing.

    Returns the original client unchanged unless Opik tracing is explicitly enabled.
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

    No-ops gracefully unless Opik tracing is explicitly enabled.
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


def update_current_span(**kwargs) -> None:
    """Attach metadata/tags to the currently active Opik span.

    Accepted keyword arguments (all optional):
        metadata: dict  — arbitrary key-value pairs shown in the Opik UI
        input: dict     — structured input data
        output: dict    — structured output data
        tags: list[str] — searchable tags
    """
    if not OPIK_ENABLED:
        return
    try:
        from opik import opik_context

        opik_context.update_current_span(**kwargs)
    except Exception as exc:
        logger.debug("update_current_span failed (no active span?): %s", exc)


def update_current_trace(**kwargs) -> None:
    """Attach metadata/tags to the currently active Opik trace.

    Accepted keyword arguments (all optional):
        metadata: dict  — arbitrary key-value pairs
        tags: list[str] — searchable tags
    """
    if not OPIK_ENABLED:
        return
    try:
        from opik import opik_context

        opik_context.update_current_trace(**kwargs)
    except Exception as exc:
        logger.debug("update_current_trace failed (no active trace?): %s", exc)
