"""
SDK-level tracing — pluggable observability for indexing, querying,
and experiment workbench runs.

Multiple backends supported::

    from seocho.tracing import enable_tracing

    # Opik (hosted or self-hosted)
    enable_tracing(backend="opik", project_name="my-project")

    # Raw JSON lines file (no dependencies)
    enable_tracing(backend="jsonl", output="./traces/seocho.jsonl")

    # Console output (debugging)
    enable_tracing(backend="console")

    # Multiple backends at once
    enable_tracing(backend=["opik", "jsonl"], output="./traces/seocho.jsonl")

    # Custom backend
    class MyTracer(TracingBackend):
        def log_span(self, name, **kwargs): ...

    enable_tracing(backend=MyTracer())
"""

from __future__ import annotations

import functools
import json
import logging
import os
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

logger = logging.getLogger(__name__)

# Module-level state
_BACKENDS: List["TracingBackend"] = []


# ======================================================================
# Abstract backend
# ======================================================================

class TracingBackend(ABC):
    """Base class for tracing backends."""

    @abstractmethod
    def log_span(
        self,
        name: str,
        *,
        input_data: Optional[Dict[str, Any]] = None,
        output_data: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> None:
        """Log a single span/event."""

    def close(self) -> None:
        """Cleanup resources."""


# ======================================================================
# Built-in backends
# ======================================================================

class OpikBackend(TracingBackend):
    """Opik tracing backend."""

    def __init__(
        self,
        *,
        url: Optional[str] = None,
        workspace: Optional[str] = None,
        project_name: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        try:
            import opik as _opik
            self._opik = _opik
        except ImportError:
            raise ImportError("OpikBackend requires opik: pip install opik")

        self._url = url or os.getenv("OPIK_URL", os.getenv("OPIK_URL_OVERRIDE", ""))
        self._workspace = workspace or os.getenv("OPIK_WORKSPACE", "default")
        self._project = project_name or os.getenv("OPIK_PROJECT_NAME", "seocho-sdk")
        self._api_key = api_key or os.getenv("OPIK_API_KEY", "")

        try:
            # Configure Opik (supports both cloud and self-hosted)
            kwargs: Dict[str, Any] = {}
            if self._api_key:
                kwargs["api_key"] = self._api_key
            if self._url:
                kwargs["url_override"] = self._url
            if self._workspace and self._workspace != "default":
                kwargs["workspace"] = self._workspace

            self._opik.configure(
                project_name=self._project,
                **kwargs,
            )
            self._client = True  # configured via global state
        except Exception as exc:
            logger.warning("Opik configure failed: %s", exc)
            self._client = None

    def log_span(
        self,
        name: str,
        *,
        input_data: Optional[Dict[str, Any]] = None,
        output_data: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> None:
        if self._client is None:
            return
        try:
            # Use opik.track as a function call for cloud compatibility
            self._opik.track(
                name=name,
                input=input_data or {},
                output=output_data or {},
                metadata=metadata or {},
                tags=tags or [],
            )
        except AttributeError:
            # Fallback: try direct trace logging
            try:
                trace = self._opik.Opik().trace(name=name)
                trace.span(
                    name=name,
                    input=input_data or {},
                    output=output_data or {},
                    metadata=metadata or {},
                    tags=tags or [],
                )
                trace.end()
            except Exception:
                pass
        except Exception as exc:
            logger.debug("Opik log failed: %s", exc)


class JSONLBackend(TracingBackend):
    """Write traces as JSON lines to a file. No dependencies needed."""

    def __init__(self, output: Union[str, Path] = "./traces/seocho.jsonl") -> None:
        self._path = Path(output)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._path, "a", encoding="utf-8")

    def log_span(
        self,
        name: str,
        *,
        input_data: Optional[Dict[str, Any]] = None,
        output_data: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "name": name,
            "input": input_data or {},
            "output": output_data or {},
            "metadata": metadata or {},
            "tags": tags or [],
        }
        try:
            self._file.write(json.dumps(record, default=str) + "\n")
            self._file.flush()
        except Exception as exc:
            logger.debug("JSONL write failed: %s", exc)

    def close(self) -> None:
        self._file.close()


class ConsoleBackend(TracingBackend):
    """Print traces to stdout. Useful for debugging."""

    def log_span(
        self,
        name: str,
        *,
        input_data: Optional[Dict[str, Any]] = None,
        output_data: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        out = output_data or {}
        meta = metadata or {}
        parts = [f"[{ts}] {name}"]
        if tags:
            parts.append(f"tags={tags}")
        for k, v in out.items():
            parts.append(f"{k}={v}")
        for k, v in meta.items():
            if k not in ("elapsed_seconds",):
                parts.append(f"{k}={v}")
        elapsed = meta.get("elapsed_seconds")
        if elapsed is not None:
            parts.append(f"({elapsed:.1f}s)")
        print("  ".join(parts))


# ======================================================================
# Public API
# ======================================================================

_BACKEND_MAP = {
    "opik": OpikBackend,
    "jsonl": JSONLBackend,
    "console": ConsoleBackend,
}


def enable_tracing(
    *,
    backend: Union[str, TracingBackend, List[Union[str, TracingBackend]]] = "console",
    output: Optional[str] = None,
    url: Optional[str] = None,
    workspace: Optional[str] = None,
    project_name: Optional[str] = None,
    api_key: Optional[str] = None,
) -> bool:
    """Enable tracing with one or more backends.

    Parameters
    ----------
    backend:
        Backend name(s) or instance(s):
        - ``"opik"`` — Opik hosted/self-hosted
        - ``"jsonl"`` — raw JSON lines file
        - ``"console"`` — stdout
        - ``TracingBackend`` instance — custom
        - list of above — multiple backends
    output:
        File path for JSONL backend.
    url, workspace, project_name:
        Opik-specific configuration.

    Returns True if at least one backend was enabled.
    """
    global _BACKENDS

    backends_input = backend if isinstance(backend, list) else [backend]
    new_backends: List[TracingBackend] = []

    for b in backends_input:
        if isinstance(b, TracingBackend):
            new_backends.append(b)
            continue

        if isinstance(b, str):
            try:
                if b == "opik":
                    new_backends.append(OpikBackend(
                        url=url, workspace=workspace,
                        project_name=project_name, api_key=api_key,
                    ))
                elif b == "jsonl":
                    new_backends.append(JSONLBackend(output=output or "./traces/seocho.jsonl"))
                elif b == "console":
                    new_backends.append(ConsoleBackend())
                else:
                    logger.warning("Unknown tracing backend: %s", b)
            except Exception as exc:
                logger.warning("Failed to init backend %s: %s", b, exc)

    _BACKENDS = new_backends
    if new_backends:
        names = [type(b).__name__ for b in new_backends]
        logger.info("Tracing enabled: %s", ", ".join(names))
    return len(new_backends) > 0


def disable_tracing() -> None:
    """Disable all tracing backends."""
    global _BACKENDS
    for b in _BACKENDS:
        try:
            b.close()
        except Exception:
            pass
    _BACKENDS = []


def is_tracing_enabled() -> bool:
    """Check if any tracing backend is active."""
    return len(_BACKENDS) > 0


# ---------------------------------------------------------------------------
# Internal logging functions (called by SDK modules)
# ---------------------------------------------------------------------------

def log_span(
    name: str,
    *,
    input_data: Optional[Dict[str, Any]] = None,
    output_data: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    tags: Optional[List[str]] = None,
) -> None:
    """Log a span to ALL active backends."""
    for b in _BACKENDS:
        try:
            b.log_span(
                name,
                input_data=input_data,
                output_data=output_data,
                metadata=metadata,
                tags=tags,
            )
        except Exception:
            pass


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
    """Log an extraction event."""
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
    """Log a query event."""
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
    """Log a Workbench experiment run."""
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
            **({"usage": usage} if usage else {}),
        },
        tags=["experiment", f"score:{score:.0%}"],
    )


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------

def traced(name: str) -> Callable:
    """Decorator that logs a span for the decorated function."""
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not _BACKENDS:
                return fn(*args, **kwargs)

            start = time.time()
            try:
                result = fn(*args, **kwargs)
                elapsed = time.time() - start
                log_span(
                    name,
                    metadata={"elapsed_seconds": round(elapsed, 2)},
                    tags=["function"],
                )
                return result
            except Exception as exc:
                elapsed = time.time() - start
                log_span(
                    name,
                    metadata={"elapsed_seconds": round(elapsed, 2), "error": str(exc)},
                    tags=["function", "error"],
                )
                raise

        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# CSV Export
# ---------------------------------------------------------------------------

def export_traces_csv(
    jsonl_path: str,
    csv_path: str,
    *,
    fields: Optional[List[str]] = None,
) -> int:
    """Convert a JSONL trace file to CSV.

    Parameters
    ----------
    jsonl_path:
        Path to the .jsonl trace file.
    csv_path:
        Output CSV path.
    fields:
        CSV column names. Defaults to standard set.

    Returns number of records exported.
    """
    import csv as csv_mod

    if fields is None:
        fields = [
            "timestamp", "name", "model",
            "input_tokens", "output_tokens", "total_tokens",
            "nodes", "relationships", "score", "validation_errors",
            "result_count", "reasoning_attempts",
            "elapsed_seconds",
        ]

    records = []
    with open(jsonl_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue

            out = raw.get("output", {})
            meta = raw.get("metadata", {})
            usage = meta.get("usage", {})

            record = {
                "timestamp": raw.get("timestamp", ""),
                "name": raw.get("name", ""),
                "model": meta.get("model", raw.get("input", {}).get("model", "")),
                "input_tokens": usage.get("prompt_tokens", ""),
                "output_tokens": usage.get("completion_tokens", ""),
                "total_tokens": usage.get("total_tokens", ""),
                "nodes": out.get("nodes", ""),
                "relationships": out.get("relationships", ""),
                "score": out.get("score", ""),
                "validation_errors": out.get("validation_errors", ""),
                "result_count": out.get("result_count", ""),
                "reasoning_attempts": out.get("reasoning_attempts", ""),
                "elapsed_seconds": meta.get("elapsed_seconds", ""),
            }
            records.append(record)

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv_mod.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)

    return len(records)
