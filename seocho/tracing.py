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
    """Opik tracing backend — follows icml2026 verified patterns.

    Relies on ``~/.opik.config`` for workspace/url configuration.
    Does NOT call ``opik.configure()`` to avoid config conflicts.

    Set these in ``~/.opik.config``::

        [opik]
        url_override = https://www.comet.com/opik/api/
        workspace = your_workspace
        api_key = your_api_key

    Or via env vars: ``OPIK_API_KEY``, ``OPIK_PROJECT_NAME``.
    """

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

        self._project = project_name or os.getenv("OPIK_PROJECT_NAME", "seocho-sdk")
        self._api_key = api_key or os.getenv("OPIK_API_KEY", "")

        # Set project name via env (avoids configure() conflicts)
        os.environ["OPIK_PROJECT_NAME"] = self._project
        if self._api_key:
            os.environ["OPIK_API_KEY"] = self._api_key

        try:
            self._client = self._opik.Opik(project_name=self._project)
        except Exception as exc:
            logger.warning("Opik client init failed: %s", exc)
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
            trace = self._client.trace(
                name=name,
                input=input_data or {},
                output=output_data or {},
                metadata=metadata or {},
                tags=tags or [],
            )
            trace.end()
        except Exception as exc:
            logger.debug("Opik trace failed: %s", exc)

    def flush(self) -> None:
        """Flush pending traces to Opik cloud."""
        if self._client is None:
            return
        try:
            self._client.flush()
        except Exception:
            pass

    def close(self) -> None:
        self.flush()


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


def flush_tracing() -> None:
    """Flush all pending traces to backends."""
    for b in _BACKENDS:
        if hasattr(b, "flush"):
            try:
                b.flush()
            except Exception:
                pass
    # Also flush opik global tracker if available
    try:
        import opik
        opik.flush_tracker()
    except Exception:
        pass


def disable_tracing() -> None:
    """Flush and disable all tracing backends."""
    flush_tracing()
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
# Session-level tracing
# ---------------------------------------------------------------------------

class SessionTrace:
    """A session-level parent trace that groups operations.

    All spans logged within a session are children of this trace,
    giving a single workflow view in Opik / JSONL.
    """

    def __init__(self, session_id: str, name: str = "") -> None:
        self.session_id = session_id
        self.name = name or f"session:{session_id}"
        self._spans: List[Dict[str, Any]] = []
        self._start_time = datetime.now(timezone.utc)
        self._opik_trace: Any = None

        # Start Opik parent trace if backend is active
        for b in _BACKENDS:
            if isinstance(b, OpikBackend) and b._client is not None:
                try:
                    self._opik_trace = b._client.trace(
                        name=self.name,
                        input={"session_id": session_id},
                        metadata={"session": True},
                        tags=["session"],
                    )
                except Exception as exc:
                    logger.debug("Opik session trace start failed: %s", exc)
                break

    def log_span(
        self,
        name: str,
        *,
        input_data: Optional[Dict[str, Any]] = None,
        output_data: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> None:
        """Log a span as a child of this session."""
        record = {
            "session_id": self.session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "name": name,
            "input": input_data or {},
            "output": output_data or {},
            "metadata": metadata or {},
            "tags": tags or [],
        }
        self._spans.append(record)

        # Log to Opik as child span
        if self._opik_trace is not None:
            try:
                self._opik_trace.span(
                    name=name,
                    input=input_data or {},
                    output=output_data or {},
                    metadata=metadata or {},
                    tags=tags or [],
                )
            except Exception:
                pass

        # Also log to all backends
        enriched_meta = {**(metadata or {}), "session_id": self.session_id}
        log_span(name, input_data=input_data, output_data=output_data,
                 metadata=enriched_meta, tags=tags)

    def end(self) -> Dict[str, Any]:
        """End the session trace and return summary."""
        elapsed = (datetime.now(timezone.utc) - self._start_time).total_seconds()

        summary = {
            "session_id": self.session_id,
            "name": self.name,
            "total_spans": len(self._spans),
            "elapsed_seconds": round(elapsed, 2),
        }

        # End Opik parent trace
        if self._opik_trace is not None:
            try:
                self._opik_trace.end(
                    output=summary,
                    metadata={"elapsed_seconds": round(elapsed, 2)},
                )
            except Exception:
                pass

        log_span(
            "sdk.session.end",
            output_data=summary,
            metadata={"elapsed_seconds": round(elapsed, 2)},
            tags=["session"],
        )
        return summary

    @property
    def spans(self) -> List[Dict[str, Any]]:
        return list(self._spans)


def begin_session(session_id: str, name: str = "") -> SessionTrace:
    """Start a new session-level trace.

    Returns a SessionTrace that groups all subsequent operations
    into a single parent trace in Opik.
    """
    trace = SessionTrace(session_id, name)
    log_span(
        "sdk.session.start",
        input_data={"session_id": session_id, "name": name},
        tags=["session"],
    )
    return trace


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
