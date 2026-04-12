"""
SDK-level Opik tracing — optional observability for indexing, querying,
and experiment workbench runs.

All tracing is gated behind ``enable_tracing``. When Opik is not
installed or not configured, everything is a no-op.

Usage::

    from seocho import Seocho
    from seocho.tracing import enable_tracing

    enable_tracing(project_name="my-project")

    s = Seocho(ontology=onto, graph_store=store, llm=llm)
    s.add("text")   # ← traced automatically
    s.ask("q?")     # ← traced automatically

Workbench integration::

    from seocho.experiment import Workbench
    wb = Workbench(input_texts=["..."])
    wb.vary("model", ["gpt-4o", "gpt-4o-mini"])
    results = wb.run_all()  # each run traced as Opik experiment
"""

from __future__ import annotations

import functools
import logging
import os
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# Module-level state
_TRACING_ENABLED = False
_OPIK_CLIENT = None


def enable_tracing(
    *,
    url: Optional[str] = None,
    workspace: Optional[str] = None,
    project_name: Optional[str] = None,
) -> bool:
    """Enable Opik tracing for the SDK.

    Parameters
    ----------
    url:
        Opik backend URL. Defaults to ``OPIK_URL`` env var.
    workspace:
        Opik workspace. Defaults to ``OPIK_WORKSPACE`` env var or "default".
    project_name:
        Opik project name. Defaults to ``OPIK_PROJECT_NAME`` env var or "seocho-sdk".

    Returns
    -------
    True if tracing was successfully enabled.
    """
    global _TRACING_ENABLED, _OPIK_CLIENT

    try:
        import opik
    except ImportError:
        logger.info("Opik not installed — tracing disabled. Install with: pip install opik")
        return False

    resolved_url = url or os.getenv("OPIK_URL", os.getenv("OPIK_URL_OVERRIDE", ""))
    resolved_workspace = workspace or os.getenv("OPIK_WORKSPACE", "default")
    resolved_project = project_name or os.getenv("OPIK_PROJECT_NAME", "seocho-sdk")

    try:
        _OPIK_CLIENT = opik.Opik(
            project_name=resolved_project,
            workspace=resolved_workspace,
        )
        _TRACING_ENABLED = True
        logger.info("Opik tracing enabled (project=%s)", resolved_project)
        return True
    except Exception as exc:
        logger.warning("Failed to enable Opik tracing: %s", exc)
        return False


def disable_tracing() -> None:
    """Disable Opik tracing."""
    global _TRACING_ENABLED, _OPIK_CLIENT
    _TRACING_ENABLED = False
    _OPIK_CLIENT = None


def is_tracing_enabled() -> bool:
    """Check if tracing is active."""
    return _TRACING_ENABLED


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------

def traced(name: str) -> Callable:
    """Decorator that creates an Opik trace for the decorated function.

    No-op when tracing is disabled.
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not _TRACING_ENABLED:
                return fn(*args, **kwargs)

            try:
                import opik
                trace = opik.track(name=name)(fn)
                return trace(*args, **kwargs)
            except Exception:
                return fn(*args, **kwargs)

        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Manual span helpers
# ---------------------------------------------------------------------------

def log_span(
    name: str,
    *,
    input_data: Optional[Dict[str, Any]] = None,
    output_data: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    tags: Optional[list] = None,
    duration_seconds: Optional[float] = None,
) -> None:
    """Log a standalone span to Opik.

    Useful for tracking individual pipeline steps (extraction, validation,
    query generation) without using the decorator pattern.
    """
    if not _TRACING_ENABLED or _OPIK_CLIENT is None:
        return

    try:
        _OPIK_CLIENT.log_spans([{
            "name": name,
            "input": input_data or {},
            "output": output_data or {},
            "metadata": metadata or {},
            "tags": tags or [],
        }])
    except Exception as exc:
        logger.debug("Failed to log span: %s", exc)


def log_extraction(
    *,
    text_preview: str,
    ontology_name: str,
    model: str,
    nodes_count: int,
    relationships_count: int,
    score: float,
    validation_errors: int,
    elapsed_seconds: float,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Log an extraction event to Opik."""
    log_span(
        "sdk.extraction",
        input_data={"text_preview": text_preview[:200], "ontology": ontology_name, "model": model},
        output_data={
            "nodes": nodes_count,
            "relationships": relationships_count,
            "score": round(score, 3),
            "validation_errors": validation_errors,
        },
        metadata={"elapsed_seconds": round(elapsed_seconds, 2), **(metadata or {})},
        tags=["extraction", f"model:{model}"],
    )


def log_query(
    *,
    question: str,
    ontology_name: str,
    model: str,
    cypher: str = "",
    result_count: int = 0,
    reasoning_attempts: int = 0,
    elapsed_seconds: float = 0.0,
) -> None:
    """Log a query event to Opik."""
    log_span(
        "sdk.query",
        input_data={"question": question[:200], "ontology": ontology_name},
        output_data={
            "cypher_preview": cypher[:200],
            "result_count": result_count,
            "reasoning_attempts": reasoning_attempts,
        },
        metadata={"model": model, "elapsed_seconds": round(elapsed_seconds, 2)},
        tags=["query", f"model:{model}"],
    )


def log_experiment_run(
    *,
    params: Dict[str, Any],
    score: float,
    nodes_count: int,
    relationships_count: int,
    elapsed_seconds: float,
    usage: Optional[Dict[str, int]] = None,
) -> None:
    """Log a Workbench experiment run to Opik."""
    param_str = " | ".join(f"{k}={v}" for k, v in params.items())
    log_span(
        "sdk.experiment",
        input_data={"params": params},
        output_data={
            "score": round(score, 3),
            "nodes": nodes_count,
            "relationships": relationships_count,
        },
        metadata={
            "elapsed_seconds": round(elapsed_seconds, 2),
            "param_string": param_str,
            **({"usage": usage} if usage else {}),
        },
        tags=["experiment", f"score:{score:.0%}"],
    )
