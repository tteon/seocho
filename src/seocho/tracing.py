"""
SDK-level tracing — pluggable observability for indexing, querying,
and experiment workbench runs.

Multiple backends supported::

    from seocho.tracing import enable_tracing

    # Disable tracing explicitly
    enable_tracing(backend="none")

    # Raw JSON lines file (canonical neutral artifact)
    enable_tracing(backend="jsonl", output="./traces/seocho.jsonl")

    # Console output (debugging)
    enable_tracing(backend="console")

    # Multiple backends at once
    enable_tracing(backend=["otlp", "jsonl"], output="./traces/seocho.jsonl")

    # Custom backend
    class MyTracer(TracingBackend):
        def log_span(self, name, **kwargs): ...

    enable_tracing(backend=MyTracer())
"""

from __future__ import annotations

import contextvars
import functools
import json
import logging
import os
import time
import uuid
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Union

logger = logging.getLogger(__name__)

TRACE_BACKEND_ENV = "SEOCHO_TRACE_BACKEND"
TRACE_JSONL_PATH_ENV = "SEOCHO_TRACE_JSONL_PATH"
TRACE_OTLP_ENDPOINT_ENV = "SEOCHO_TRACE_OTLP_ENDPOINT"
TRACE_CONTENT_CAPTURE_ENV = "SEOCHO_TRACE_CAPTURE_CONTENT"
_VALID_BACKEND_NAMES = {"none", "console", "jsonl", "otlp"}
_TRUTHY = {"1", "true", "yes", "on"}

# Module-level state
_BACKENDS: List["TracingBackend"] = []
_BACKEND_NAMES: List[str] = []

# Nesting stack for start_span(): a tuple ``(trace_id, span_id, span_id, ...)``
# where element 0 is the trace id and the last element is the immediate parent.
_span_stack: contextvars.ContextVar = contextvars.ContextVar(
    "seocho_span_stack", default=()
)


# ======================================================================
# Content-capture policy (ADR-0144)
# ======================================================================

def content_capture_enabled() -> bool:
    """True when full content (prompts, Cypher, retrieved bodies) may be traced.

    Span *attributes* (hashes, versions, counts, ids) are always emitted; large
    *content* is gated behind ``SEOCHO_TRACE_CAPTURE_CONTENT`` so the default
    trace stays light. This is the root mitigation for "tracing is too heavy".
    """
    return str(os.getenv(TRACE_CONTENT_CAPTURE_ENV, "") or "").strip().lower() in _TRUTHY


def capture_text(text: Any, *, max_chars: int = 2000) -> Optional[str]:
    """Return text for tracing only when content capture is on, else ``None``.

    Truncates to ``max_chars`` with a marker. Callers should omit the field when
    this returns ``None`` so disabled-capture traces carry no content at all.
    """
    if text is None or not content_capture_enabled():
        return None
    s = text if isinstance(text, str) else str(text)
    if len(s) > max_chars:
        return s[:max_chars] + f"...[+{len(s) - max_chars} chars]"
    return s


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


def _flatten_attributes(
    data: Optional[Dict[str, Any]], prefix: str = ""
) -> Dict[str, Any]:
    """Flatten a nested dict into OTel-safe attributes (primitives / lists).

    OpenTelemetry attributes must be primitives or homogeneous sequences of
    primitives. Nested dicts become dotted keys; mixed/complex values are
    JSON-encoded; ``None`` values are dropped.
    """
    attrs: Dict[str, Any] = {}
    if not data:
        return attrs
    for key, value in data.items():
        attr_key = f"{prefix}{key}"
        if value is None:
            continue
        if isinstance(value, bool) or isinstance(value, (str, int, float)):
            attrs[attr_key] = value
        elif isinstance(value, dict):
            attrs.update(_flatten_attributes(value, prefix=f"{attr_key}."))
        elif isinstance(value, (list, tuple)):
            if all(isinstance(x, (str, bool, int, float)) for x in value):
                attrs[attr_key] = list(value)
            else:
                attrs[attr_key] = json.dumps(value, default=str)
        else:
            attrs[attr_key] = str(value)
    return attrs


class OTLPBackend(TracingBackend):
    """Export spans over OTLP gRPC to an OpenTelemetry Collector (ADR-0144).

    The vendor-neutral default: spans flow to a Collector and on to
    Tempo (traces) / Prometheus (metrics) / Grafana. A hosted SaaS backend
    stays the cloud team backend; both can run together via a backend list.

    Requires the OTel SDK + OTLP exporter::

        pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc

    Config: ``SEOCHO_TRACE_OTLP_ENDPOINT`` (default ``http://localhost:4317``),
    ``OTEL_SERVICE_NAME`` (default ``seocho``).

    Unlike the flat backends, this one supports real parent/child nesting via
    :func:`start_span` (``open_span`` / ``close_span`` drive the OTel context),
    so a single ``ask()`` lands as a span tree in Tempo.
    """

    def __init__(
        self,
        *,
        endpoint: Optional[str] = None,
        service_name: Optional[str] = None,
    ) -> None:
        try:
            from opentelemetry import context as _ot_context
            from opentelemetry import trace as _ot_trace
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.trace import Status, StatusCode
        except ImportError as exc:
            raise ImportError(
                "OTLPBackend requires opentelemetry: pip install "
                "opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc"
            ) from exc

        self._endpoint = (
            endpoint
            or os.getenv(TRACE_OTLP_ENDPOINT_ENV, "")
            or "http://localhost:4317"
        )
        self._service_name = (
            service_name or os.getenv("OTEL_SERVICE_NAME", "") or "seocho"
        )
        self._ot_trace = _ot_trace
        self._ot_context = _ot_context
        self._Status = Status
        self._StatusCode = StatusCode

        # Counters that used to live on a private meter here now flow through
        # the ADR-0146 registry (seocho.metrics) — one provider, one naming
        # scheme, one env switch. This backend owns spans only.
        try:
            resource_attributes = {"service.name": self._service_name}
            if instance_id := os.getenv("OTEL_SERVICE_INSTANCE_ID"):
                resource_attributes["service.instance.id"] = instance_id
            resource = Resource.create(resource_attributes)
            provider = TracerProvider(resource=resource)
            exporter = OTLPSpanExporter(
                endpoint=self._endpoint,
                insecure=self._endpoint.startswith("http://"),
            )
            provider.add_span_processor(BatchSpanProcessor(exporter))
            self._provider = provider
            self._tracer = provider.get_tracer("seocho.tracing")
            self._init_error: Optional[str] = None
        except Exception as exc:
            logger.warning("OTLP backend init failed: %s", exc)
            self._provider = None
            self._tracer = None
            self._init_error = f"{type(exc).__name__}: {exc}"

    def _attributes(
        self,
        input_data: Optional[Dict[str, Any]],
        output_data: Optional[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]],
        tags: Optional[List[str]],
    ) -> Dict[str, Any]:
        attrs: Dict[str, Any] = {}
        attrs.update(_flatten_attributes(input_data, prefix="input."))
        attrs.update(_flatten_attributes(output_data, prefix="output."))
        attrs.update(_flatten_attributes(metadata))
        if tags:
            attrs["seocho.tags"] = list(tags)
        return attrs

    def log_span(
        self,
        name: str,
        *,
        input_data: Optional[Dict[str, Any]] = None,
        output_data: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> None:
        # Leaf/point event: a one-shot span under whatever context is current,
        # so it nests under any active start_span().
        if self._tracer is None:
            return
        try:
            span = self._tracer.start_span(name)
            for k, v in self._attributes(input_data, output_data, metadata, tags).items():
                span.set_attribute(k, v)
            span.end()
        except Exception as exc:
            logger.debug("OTLP log_span failed: %s", exc)

    def open_span(self, name: str, *, attributes: Optional[Dict[str, Any]] = None) -> Any:
        """Open a span, attach it as current context, return a handle for close_span."""
        if self._tracer is None:
            return None
        try:
            span = self._tracer.start_span(name)
            if attributes:
                for k, v in attributes.items():
                    span.set_attribute(k, v)
            ctx = self._ot_trace.set_span_in_context(span)
            token = self._ot_context.attach(ctx)
            return (span, token)
        except Exception as exc:
            logger.debug("OTLP open_span failed: %s", exc)
            return None

    def close_span(
        self,
        handle: Any,
        *,
        attributes: Optional[Dict[str, Any]] = None,
        error: Optional[BaseException] = None,
    ) -> None:
        if not handle:
            return
        span, token = handle
        try:
            if attributes:
                for k, v in attributes.items():
                    span.set_attribute(k, v)
            if error is not None:
                span.record_exception(error)
                span.set_status(self._Status(self._StatusCode.ERROR))
        except Exception as exc:
            logger.debug("OTLP close_span failed: %s", exc)
        finally:
            try:
                self._ot_context.detach(token)
            except Exception:
                pass
            try:
                span.end()
            except Exception:
                pass

    def flush(self) -> None:
        if self._provider is not None:
            try:
                self._provider.force_flush()
            except Exception:
                self._count_export_failure("traces")

    @staticmethod
    def _count_export_failure(signal: str) -> None:
        """Count a telemetry export failure via the *other* pipeline.

        Deliberately routed through the ADR-0146 registry, not this backend's
        own meter: when this exporter is the thing failing, incrementing a
        counter that exports through it would report nothing. Import is lazy
        to keep tracing importable without the metrics module and vice versa.
        """
        try:
            from .metrics import get_metrics

            get_metrics().add(
                "seocho.observability.export_failure.count",
                attributes={"signal": signal, "exporter": "otlp"},
            )
        except Exception:  # pragma: no cover - last-resort guard
            pass

    def close(self) -> None:
        if self._provider is not None:
            try:
                self._provider.shutdown()
            except Exception:
                pass


# ======================================================================
# Public API
# ======================================================================

_BACKEND_MAP = {
    "jsonl": JSONLBackend,
    "console": ConsoleBackend,
    "otlp": OTLPBackend,
}


def _normalized_backend_names(
    backend: Union[str, TracingBackend, List[Union[str, TracingBackend]]],
) -> List[str]:
    items = backend if isinstance(backend, list) else [backend]
    normalized: List[str] = []
    for item in items:
        if not isinstance(item, str):
            continue
        for raw_name in str(item).split(","):
            name = raw_name.strip().lower()
            if not name or name == "none":
                continue
            if name in _VALID_BACKEND_NAMES and name not in normalized:
                normalized.append(name)
    return normalized


def current_backend_names() -> List[str]:
    """Return the active built-in backend names."""
    return list(_BACKEND_NAMES)


def is_backend_enabled(name: str) -> bool:
    """Return True when the given built-in backend is active."""
    normalized = str(name).strip().lower()
    return normalized in _BACKEND_NAMES


def tracing_degraded_reasons() -> List[str]:
    """Return reasons why tracing is silently dropping spans (empty if healthy).

    Closes seocho-qr74. A backend is "degraded" when it is enabled but its
    underlying client failed to initialize — every subsequent ``log_span``
    becomes a no-op and the caller sees nothing. The Session layer stamps
    ``degraded_observability=True`` on results when this returns non-empty.
    """
    reasons: List[str] = []
    for backend, name in zip(_BACKENDS, _BACKEND_NAMES):
        # Some backends expose _init_error; others don't (currently) fail init silently.
        init_error = getattr(backend, "_init_error", None)
        if init_error:
            reasons.append(f"{name}: {init_error}")
    return reasons


def is_observability_degraded() -> bool:
    """True if at least one enabled backend is silently dropping spans."""
    return bool(tracing_degraded_reasons())


def configure_tracing_from_env() -> bool:
    """Enable tracing from the repository's env contract.

    Supported values for ``SEOCHO_TRACE_BACKEND``:
    ``none | console | jsonl | otlp``.
    """
    backend_name = str(os.getenv(TRACE_BACKEND_ENV, "none") or "none").strip().lower()
    if backend_name not in _VALID_BACKEND_NAMES:
        logger.warning(
            "Unsupported %s=%s; expected one of %s. Tracing disabled.",
            TRACE_BACKEND_ENV,
            backend_name,
            ", ".join(sorted(_VALID_BACKEND_NAMES)),
        )
        disable_tracing()
        return False

    if backend_name == "none":
        disable_tracing()
        return False

    return enable_tracing(
        backend=backend_name,
        output=os.getenv(TRACE_JSONL_PATH_ENV) or "./traces/seocho.jsonl",
    )


def enable_tracing(
    *,
    backend: Union[str, TracingBackend, List[Union[str, TracingBackend]]] = "console",
    output: Optional[str] = None,
    endpoint: Optional[str] = None,
    service_name: Optional[str] = None,
) -> bool:
    """Enable tracing with one or more backends.

    Parameters
    ----------
    backend:
        Backend name(s) or instance(s):
        - ``"none"`` — disable tracing
        - ``"jsonl"`` — raw JSON lines file
        - ``"console"`` — stdout
        - ``TracingBackend`` instance — custom
        - list of above — multiple backends
    output:
        File path for JSONL backend.
    endpoint, service_name:
        OTLP collector endpoint and OTel service name. Environment defaults are
        used when omitted.

    Returns True if at least one backend was enabled.
    """
    global _BACKENDS, _BACKEND_NAMES

    backends_input = backend if isinstance(backend, list) else [backend]
    new_backends: List[TracingBackend] = []
    requested_backend_names = _normalized_backend_names(backend)
    active_backend_names: List[str] = []

    if not requested_backend_names and all(isinstance(item, str) for item in backends_input):
        disable_tracing()
        return False

    for b in backends_input:
        if isinstance(b, TracingBackend):
            new_backends.append(b)
            continue

        if isinstance(b, str):
            try:
                if b == "jsonl":
                    new_backends.append(JSONLBackend(output=output or "./traces/seocho.jsonl"))
                    active_backend_names.append("jsonl")
                elif b == "console":
                    new_backends.append(ConsoleBackend())
                    active_backend_names.append("console")
                elif b == "otlp":
                    new_backends.append(
                        OTLPBackend(endpoint=endpoint, service_name=service_name)
                    )
                    active_backend_names.append("otlp")
                elif b == "none":
                    continue
                else:
                    logger.warning("Unknown tracing backend: %s", b)
            except Exception as exc:
                logger.warning("Failed to init backend %s: %s", b, exc)

    _BACKENDS = new_backends
    _BACKEND_NAMES = active_backend_names
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


def disable_tracing() -> None:
    """Flush and disable all tracing backends."""
    flush_tracing()
    global _BACKENDS, _BACKEND_NAMES
    for b in _BACKENDS:
        try:
            b.close()
        except Exception:
            pass
    _BACKENDS = []
    _BACKEND_NAMES = []


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


# ADR-0144 §6 counters were emitted through this module's own OTel meter with
# their own naming scheme, env switch, and no label validation — a second
# metrics pipeline next to the ADR-0146 registry. The legacy names now map
# onto catalog specs and everything funnels through seocho.metrics (one
# provider, one naming convention, label budget enforced everywhere).
_LEGACY_METRIC_MAP = {
    "seocho_validation_errors": "seocho.index.validation_errors.count",
    "seocho_observations_reified": "seocho.index.observations_reified.count",
    "seocho_arbiter_route": "seocho.arbiter.route.count",
    "seocho_query_plan": "seocho.query.plan.count",
    "seocho_query_db_hits": "seocho.query.db_hits.count",
    "seocho_query_scan": "seocho.query.scan.count",
    "seocho_query_plan_route": "seocho.query.plan_route.count",
    "seocho_query_generation_declined": "seocho.query.generation_declined.count",
}


def record_metric(
    name: str,
    value: float = 1,
    *,
    attributes: Optional[Dict[str, Any]] = None,
) -> None:
    """Legacy counter shim: delegate to the ADR-0146 metrics registry.

    Accepts either a legacy ADR-0144 snake_case name or a catalog spec name.
    Anything outside the catalog is dropped with a debug log — an arbitrary
    name would bypass the registry's label budget, which is the whole reason
    the two pipelines were unified. New code should call
    :func:`seocho.metrics.get_metrics` directly.
    """
    from .metrics import METRIC_SPECS, get_metrics

    spec_name = _LEGACY_METRIC_MAP.get(name, name)
    if spec_name not in METRIC_SPECS:
        logger.debug("record_metric dropped uncataloged metric: %s", name)
        return
    try:
        get_metrics().add(spec_name, value, attributes=attributes)
    except Exception as exc:
        logger.debug("record_metric failed for %s: %s", name, exc)


# ---------------------------------------------------------------------------
# Structured spans with parent/child nesting (ADR-0144)
# ---------------------------------------------------------------------------

class _NullSpan:
    """Returned by start_span() when tracing is disabled — zero overhead."""

    def set_input(self, *_a: Any, **_kw: Any) -> None: ...
    def set_output(self, *_a: Any, **_kw: Any) -> None: ...
    def set_metadata(self, *_a: Any, **_kw: Any) -> None: ...
    def set_tags(self, *_tags: str) -> None: ...


class SpanHandle:
    """Mutable handle yielded by :func:`start_span` to enrich a span in-flight."""

    def __init__(self, name: str, span_id: str, parent_span_id: Optional[str], trace_id: str) -> None:
        self.name = name
        self.span_id = span_id
        self.parent_span_id = parent_span_id
        self.trace_id = trace_id
        self.error: Optional[BaseException] = None
        self._input: Dict[str, Any] = {}
        self._output: Dict[str, Any] = {}
        self._metadata: Dict[str, Any] = {}
        self._tags: List[str] = []

    def set_input(self, mapping: Optional[Dict[str, Any]] = None, **kw: Any) -> None:
        if mapping:
            self._input.update(mapping)
        if kw:
            self._input.update(kw)

    def set_output(self, mapping: Optional[Dict[str, Any]] = None, **kw: Any) -> None:
        if mapping:
            self._output.update(mapping)
        if kw:
            self._output.update(kw)

    def set_metadata(self, mapping: Optional[Dict[str, Any]] = None, **kw: Any) -> None:
        # ``mapping`` carries dotted OTel keys (db.name, gen_ai.*); kwargs are
        # the convenience form for plain identifiers.
        if mapping:
            self._metadata.update(mapping)
        if kw:
            self._metadata.update(kw)

    def set_tags(self, *tags: str) -> None:
        self._tags.extend(tags)


@contextmanager
def start_span(
    name: str,
    *,
    input_data: Optional[Dict[str, Any]] = None,
    output_data: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    tags: Optional[List[str]] = None,
) -> Iterator[Any]:
    """Open a timed span with parent/child nesting, across all backends.

    Backends exposing ``open_span``/``close_span`` (OTLP) get real nested spans
    via the OTel context, so an ``ask()`` lands as a tree in Tempo. Flat
    backends (JSONL, console, OTLP) receive one record at span close, carrying
    ``span_id`` / ``parent_span_id`` / ``trace_id`` / ``duration_ms`` so the
    tree is reconstructable offline.

    No-op with zero overhead when tracing is disabled. Body exceptions
    propagate (they are recorded on the span first); tracing-internal failures
    never escape.
    """
    if not _BACKENDS:
        yield _NullSpan()
        return

    parent = _span_stack.get()
    if parent:
        trace_id = parent[0]
        parent_span_id: Optional[str] = parent[-1]
        new_stack = parent
    else:
        trace_id = uuid.uuid4().hex
        parent_span_id = None
        new_stack = (trace_id,)
    span_id = uuid.uuid4().hex[:16]
    new_stack = new_stack + (span_id,)

    handle = SpanHandle(name, span_id, parent_span_id, trace_id)
    handle._input = dict(input_data or {})
    handle._output = dict(output_data or {})
    handle._metadata = dict(metadata or {})
    handle._tags = list(tags or [])

    # Open native nested spans on capable backends (OTLP).
    opened: List[Any] = []
    for b in _BACKENDS:
        opener = getattr(b, "open_span", None)
        if opener is None:
            continue
        try:
            init_attrs = (
                b._attributes(handle._input, None, handle._metadata, handle._tags)
                if hasattr(b, "_attributes")
                else None
            )
            native = opener(name, attributes=init_attrs)
            if native is not None:
                opened.append((b, native))
        except Exception:
            pass

    # OTLP creates the authoritative trace identity. Expose that identity on
    # the public handle so receipts and Grafana links address the trace Tempo
    # actually stores, rather than SEOCHO's fallback correlation UUID.
    if parent_span_id is None and opened:
        try:
            native_handle = opened[0][1]
            native_span = (
                native_handle[0]
                if isinstance(native_handle, tuple)
                and native_handle
                and hasattr(native_handle[0], "get_span_context")
                else native_handle
            )
            native_context = native_span.get_span_context()
            native_trace_id = f"{native_context.trace_id:032x}"
            if native_trace_id != "0" * 32:
                trace_id = native_trace_id
                handle.trace_id = native_trace_id
                new_stack = (native_trace_id, span_id)
        except Exception:
            pass

    stack_token = _span_stack.set(new_stack)
    start = time.perf_counter()
    try:
        yield handle
    except BaseException as exc:  # record then re-raise — never swallow business errors
        handle.error = exc
        raise
    finally:
        duration_ms = round((time.perf_counter() - start) * 1000.0, 2)
        try:
            _span_stack.reset(stack_token)
        except Exception:
            pass

        # Close native spans (OTLP): final output + duration + error status.
        for b, native in opened:
            try:
                close_attrs = (
                    b._attributes(None, handle._output, {"duration_ms": duration_ms}, None)
                    if hasattr(b, "_attributes")
                    else None
                )
                b.close_span(native, attributes=close_attrs, error=handle.error)
            except Exception:
                pass

        # Emit one record to flat backends (those without open_span).
        meta = {
            **handle._metadata,
            "span_id": span_id,
            "trace_id": trace_id,
            "duration_ms": duration_ms,
        }
        if parent_span_id:
            meta["parent_span_id"] = parent_span_id
        if handle.error is not None:
            meta["error"] = str(handle.error)
        out_tags = handle._tags + (["error"] if handle.error is not None else [])
        for b in _BACKENDS:
            if getattr(b, "open_span", None) is not None:
                continue
            try:
                b.log_span(
                    name,
                    input_data=handle._input or None,
                    output_data=handle._output or None,
                    metadata=meta,
                    tags=out_tags or None,
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
    ontology_package: str = "",
    model: str,
    cypher: str = "",
    result_count: int = 0,
    reasoning_attempts: int = 0,
    elapsed_seconds: float = 0.0,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Log a query event."""
    log_span(
        "sdk.query",
        input_data={
            "question": question[:200],
            "ontology": ontology_name,
            **({"ontology_package": ontology_package} if ontology_package else {}),
        },
        output_data={
            "cypher_preview": cypher[:200],
            "result_count": result_count,
            "reasoning_attempts": reasoning_attempts,
        },
        metadata={
            "model": model,
            "elapsed_seconds": round(elapsed_seconds, 2),
            **(metadata or {}),
        },
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
    giving a single workflow view in the JSONL and OTLP backends.
    """

    def __init__(self, session_id: str, name: str = "") -> None:
        self.session_id = session_id
        self.name = name or f"session:{session_id}"
        self._spans: List[Dict[str, Any]] = []
        self._start_time = datetime.now(timezone.utc)

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

        # Fan out to all backends
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
    into a single parent trace.
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
# Trace read / query (closes the observe loop — seocho-6q9.1)
# ---------------------------------------------------------------------------

def default_jsonl_path() -> str:
    """Resolve the canonical JSONL trace path from the env contract.

    Returns ``$SEOCHO_TRACE_JSONL_PATH`` when set, else the conventional
    ``./traces/seocho.jsonl``. This is the same default the JSONL backend
    writes to, so ``read_jsonl(default_jsonl_path())`` round-trips.
    """
    return os.getenv(TRACE_JSONL_PATH_ENV) or "./traces/seocho.jsonl"


def span_latency_ms(record: Dict[str, Any]) -> Optional[float]:
    """Best-effort latency for one JSONL span, in milliseconds.

    The SDK loggers stamp ``metadata.elapsed_seconds`` (seconds); StageTimer
    output lands as ``*_ms`` keys. Prefer the canonical seconds field, then
    fall back to common millisecond keys. Returns ``None`` when no timing is
    present (e.g. session.start markers).
    """
    meta = record.get("metadata") or {}
    secs = meta.get("elapsed_seconds")
    if secs is not None:
        try:
            return float(secs) * 1000.0
        except (TypeError, ValueError):
            pass
    for key in ("elapsed_ms", "total_ms", "latency_ms"):
        val = meta.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    return None


def read_jsonl(
    path: Union[str, Path],
    *,
    min_latency_ms: Optional[float] = None,
    name: Optional[str] = None,
    name_contains: Optional[str] = None,
    tags: Optional[Sequence[str]] = None,
    since: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Read and filter spans from a JSONL trace file.

    This is the read side of the otherwise write-only tracing layer: it lets
    an agent ask "show spans over 2s" or "did this run breach a budget" by
    reading back the canonical JSONL artifact. Read-safe — no side effects.

    Each returned record is the raw span dict augmented with a derived
    ``latency_ms`` field (see :func:`span_latency_ms`), so callers can sort or
    assert on it directly.

    Parameters
    ----------
    path:
        JSONL trace file (one span per line).
    min_latency_ms:
        Keep only spans whose derived ``latency_ms`` is >= this. Spans with no
        timing are dropped when this filter is set.
    name:
        Exact span-name match.
    name_contains:
        Substring match on the span name.
    tags:
        Require every listed tag to be present on the span.
    since:
        ISO-8601 lower bound on ``timestamp`` (UTC strings sort lexically).

    Raises
    ------
    FileNotFoundError:
        When ``path`` does not exist.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"trace file not found: {p}")

    want_tags = set(tags) if tags else None
    out: List[Dict[str, Any]] = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            latency = span_latency_ms(record)
            record["latency_ms"] = latency

            if name is not None and record.get("name") != name:
                continue
            if name_contains is not None and name_contains not in (record.get("name") or ""):
                continue
            if want_tags is not None and not want_tags.issubset(set(record.get("tags") or [])):
                continue
            if since is not None and str(record.get("timestamp", "")) < since:
                continue
            if min_latency_ms is not None and (latency is None or latency < min_latency_ms):
                continue

            out.append(record)
    return out


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
