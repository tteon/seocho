"""Low-cardinality production metrics for SEOCHO runtime boundaries.

Metrics intentionally carry aggregate operational dimensions only. Exact
request causality belongs in traces and auditable receipts, never metric labels.
"""

from __future__ import annotations

import atexit
import os
import threading
from dataclasses import dataclass
from typing import Any, Mapping, Protocol


METRICS_BACKEND_ENV = "SEOCHO_METRICS_BACKEND"
METRICS_OTLP_ENDPOINT_ENV = "SEOCHO_METRICS_OTLP_ENDPOINT"
_FORBIDDEN_ATTRIBUTE_FRAGMENTS = (
    "workspace",
    "user",
    "session",
    "conversation",
    "wallet",
    "account",
    "order",
    "transaction",
    "event_id",
    "memory_id",
    "trace_id",
    "prompt",
    "completion",
    "response_text",
    "query_text",
    "cypher",
    "sql",
    "arguments",
    "payload",
)

# Default OTel histogram boundaries are far too coarse for graph retrieval and
# agent hot paths (sub-second observations collapse into the first bucket and
# produce misleading multi-second p95 values in Prometheus).  Keep boundaries
# explicit and low-cardinality so dashboards reflect the latency actually seen
# by callers.
_DURATION_SECONDS_BUCKETS = (
    0.001,
    0.0025,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
)
_LATENCY_MILLISECONDS_BUCKETS = (
    1.0,
    2.5,
    5.0,
    10.0,
    25.0,
    50.0,
    100.0,
    250.0,
    500.0,
    1000.0,
    2500.0,
    5000.0,
)


@dataclass(frozen=True, slots=True)
class MetricSpec:
    name: str
    kind: str
    unit: str
    attributes: frozenset[str]
    description: str


def _spec(
    name: str,
    kind: str,
    unit: str,
    attributes: tuple[str, ...],
    description: str,
) -> MetricSpec:
    return MetricSpec(name, kind, unit, frozenset(attributes), description)


METRIC_SPECS: dict[str, MetricSpec] = {
    spec.name: spec
    for spec in (
        _spec("seocho.agent.request.duration", "histogram", "s", ("operation", "outcome", "error.type"), "Agent request duration."),
        _spec("seocho.agent.request.count", "counter", "{request}", ("operation", "outcome"), "Completed agent requests."),
        _spec("seocho.agent.request.inflight", "up_down_counter", "{request}", ("operation",), "In-flight agent requests."),
        _spec("seocho.agent.timeout.count", "counter", "{timeout}", ("operation", "stage"), "Agent stage timeouts."),
        _spec("seocho.agent.partial.count", "counter", "{request}", ("operation", "reason"), "Explicit partial answers."),
        _spec("seocho.answer.freshness_violation.count", "counter", "{answer}", ("query.class",), "Answers blocked for freshness violations."),
        _spec("seocho.answer.provenance.coverage", "histogram", "1", ("query.class",), "Fraction of answer claims with provenance."),
        _spec("seocho.memory.commit.duration", "histogram", "s", ("outcome", "error.type"), "Authoritative memory commit duration."),
        _spec("seocho.memory.commit.count", "counter", "{commit}", ("outcome",), "Authoritative memory commits."),
        _spec("seocho.memory.sequence", "gauge", "{event}", (), "Latest authoritative memory sequence."),
        _spec("seocho.memory.idempotency_replay.count", "counter", "{event}", ("outcome",), "Idempotent memory replays."),
        _spec("seocho.memory.transition_conflict.count", "counter", "{conflict}", ("event.type",), "Rejected memory transitions."),
        _spec("seocho.projection.watermark", "gauge", "{event}", ("projection",), "Latest applied projection sequence."),
        _spec("seocho.projection.outbox.pending", "gauge", "{entry}", ("projection",), "Pending projection outbox entries."),
        _spec("seocho.projection.outbox.oldest_age", "gauge", "s", ("projection",), "Age of oldest pending outbox entry."),
        _spec("seocho.projection.batch.duration", "histogram", "s", ("projection", "outcome"), "Projection batch duration."),
        _spec("seocho.projection.batch.entry_count", "histogram", "{entry}", ("projection",), "Entries processed per projection batch."),
        _spec("seocho.projection.replay.count", "counter", "{entry}", ("projection", "outcome"), "Projection replay results."),
        _spec("seocho.projection.fencing_rejection.count", "counter", "{rejection}", ("projection",), "Rejected stale projector owners."),
        _spec("seocho.retrieval.duration", "histogram", "s", ("source", "outcome"), "Retrieval operation duration."),
        _spec("seocho.retrieval.inflight", "up_down_counter", "{query}", ("source",), "In-flight retrieval queries."),
        _spec("seocho.retrieval.admission_rejection.count", "counter", "{rejection}", ("source", "reason"), "Retrieval queries rejected before backend execution."),
        _spec("seocho.retrieval.candidate_count", "histogram", "{item}", ("source",), "Retrieval candidates."),
        _spec("seocho.retrieval.selected_count", "histogram", "{item}", ("source",), "Selected retrieval results."),
        _spec("seocho.federation.target.duration", "histogram", "s", ("target", "outcome"), "Federated target duration."),
        _spec("seocho.federation.partial.count", "counter", "{request}", ("reason",), "Partial federation results."),
        _spec("seocho.text2cypher.duration", "histogram", "s", ("stage", "outcome"), "Text2Cypher stage duration."),
        _spec("seocho.text2cypher.validation_failure.count", "counter", "{failure}", ("reason",), "Rejected generated Cypher."),
        _spec("seocho.text2cypher.execution_failure.count", "counter", "{failure}", ("error.type",), "Cypher execution failures."),
        _spec("seocho.query.plan.speedup", "histogram", "1", ("cohort",), "Baseline-to-candidate query-plan speedup after semantic parity."),
        _spec("seocho.query.plan.db_hits_reduction", "histogram", "1", ("cohort",), "Fractional DB-hit reduction for a semantically equivalent query plan."),
        _spec("seocho.query.plan.finding.count", "counter", "{finding}", ("variant", "finding"), "Execution-plan findings attributed by the GOpt-inspired audit."),
        _spec("seocho.context.assembly.duration", "histogram", "s", ("strategy", "outcome"), "Context assembly duration."),
        _spec("seocho.context.candidate_token_count", "histogram", "{token}", ("strategy",), "Candidate context tokens."),
        _spec("seocho.context.selected_token_count", "histogram", "{token}", ("strategy",), "Selected context tokens."),
        _spec("seocho.context.item_count", "histogram", "{item}", ("strategy", "state"), "Candidate or selected context items."),
        _spec("seocho.context.budget_exceeded.count", "counter", "{request}", ("strategy",), "Context budget exceedances."),
        _spec("seocho.context.policy_filtered.count", "counter", "{item}", ("reason",), "Context items removed by policy."),
        _spec("gen_ai.client.operation.duration", "histogram", "s", ("gen_ai.provider.name", "gen_ai.request.model", "gen_ai.operation.name", "error.type"), "GenAI client operation duration."),
        _spec("gen_ai.client.token.usage", "histogram", "{token}", ("gen_ai.provider.name", "gen_ai.request.model", "gen_ai.token.type"), "Provider-reported token usage."),
        _spec("seocho.gen_ai.time_to_first_token", "histogram", "s", ("gen_ai.provider.name", "gen_ai.request.model"), "Streaming time to first token."),
        _spec("seocho.gen_ai.retry.count", "counter", "{retry}", ("gen_ai.provider.name", "gen_ai.request.model", "reason"), "GenAI retries."),
        _spec("seocho.gen_ai.structured_output_repair.count", "counter", "{repair}", ("gen_ai.provider.name", "gen_ai.request.model", "reason"), "Structured-output repairs."),
        _spec("seocho.governance.disclosure_violation.count", "counter", "{violation}", ("stage", "policy.disposition"), "Disclosure violations."),
        _spec("seocho.governance.policy_decision.count", "counter", "{decision}", ("policy.version", "policy.disposition"), "Policy decisions."),
        _spec("seocho.governance.policy_version_mismatch.count", "counter", "{mismatch}", ("stage",), "Policy version mismatches."),
        _spec("seocho.governance.ontology_version_mismatch.count", "counter", "{mismatch}", ("stage",), "Ontology version mismatches."),
        _spec("seocho.observability.export_failure.count", "counter", "{failure}", ("signal", "exporter"), "Telemetry export failures."),
        _spec("seocho.critical.scenario.runs", "counter", "{run}", ("scenario_id", "support_status"), "Critical evaluation scenario runs."),
        _spec("seocho.critical.scenario.passed", "counter", "{run}", ("scenario_id",), "Passed critical evaluation scenarios."),
        _spec("seocho.critical.silent_stale", "counter", "{answer}", ("scenario_id",), "Silent stale answers in evaluation."),
        _spec("seocho.critical.disclosure_violations", "counter", "{violation}", ("scenario_id",), "Disclosure violations in evaluation."),
        _spec("seocho.critical.memory_sequence", "gauge", "{event}", ("scenario_id",), "Evaluation authoritative sequence."),
        _spec("seocho.critical.projection_watermark", "gauge", "{event}", ("scenario_id",), "Evaluation projection watermark."),
        _spec("seocho.critical.projection_lag", "gauge", "{event}", ("scenario_id",), "Evaluation projection lag."),
        _spec("seocho.critical.scenario.info", "gauge", "1", ("scenario_id", "support_status"), "Evaluation scenario support state."),
        _spec("seocho.critical.latency", "histogram", "ms", ("scenario_id", "stage"), "Evaluation stage latency."),
    )
}


class _Instrument(Protocol):
    def add(self, amount: int | float, attributes: Mapping[str, Any] | None = None) -> None: ...
    def record(self, amount: int | float, attributes: Mapping[str, Any] | None = None) -> None: ...
    def set(self, amount: int | float, attributes: Mapping[str, Any] | None = None) -> None: ...


class _NoopInstrument:
    def add(self, amount: int | float, attributes: Mapping[str, Any] | None = None) -> None:
        return None

    record = add
    set = add


class ProductionMetrics:
    """Validated instrument registry backed by an OpenTelemetry meter."""

    def __init__(self, meter: Any | None = None) -> None:
        self._instruments: dict[str, _Instrument] = {}
        for spec in METRIC_SPECS.values():
            if meter is None:
                instrument: _Instrument = _NoopInstrument()
            elif spec.kind == "counter":
                instrument = meter.create_counter(spec.name, unit=spec.unit, description=spec.description)
            elif spec.kind == "up_down_counter":
                instrument = meter.create_up_down_counter(spec.name, unit=spec.unit, description=spec.description)
            elif spec.kind == "gauge":
                instrument = meter.create_gauge(spec.name, unit=spec.unit, description=spec.description)
            else:
                instrument = meter.create_histogram(spec.name, unit=spec.unit, description=spec.description)
            self._instruments[spec.name] = instrument

    @staticmethod
    def _attributes(spec: MetricSpec, attributes: Mapping[str, Any] | None) -> dict[str, Any]:
        values = dict(attributes or {})
        unknown = set(values) - spec.attributes
        if unknown:
            raise ValueError(f"unsupported attributes for {spec.name}: {', '.join(sorted(unknown))}")
        for key, value in values.items():
            lowered = key.lower()
            if any(fragment in lowered for fragment in _FORBIDDEN_ATTRIBUTE_FRAGMENTS):
                raise ValueError(f"forbidden metric attribute: {key}")
            if not isinstance(value, (str, bool, int, float)):
                raise TypeError(f"metric attribute {key} must be scalar")
            if isinstance(value, str) and len(value) > 80:
                raise ValueError(f"metric attribute {key} exceeds 80 characters")
        return values

    def add(self, name: str, amount: int | float = 1, attributes: Mapping[str, Any] | None = None) -> None:
        spec = METRIC_SPECS[name]
        if spec.kind not in {"counter", "up_down_counter"}:
            raise TypeError(f"{name} is not an additive instrument")
        self._instruments[name].add(amount, self._attributes(spec, attributes))

    def record(self, name: str, value: int | float, attributes: Mapping[str, Any] | None = None) -> None:
        spec = METRIC_SPECS[name]
        if spec.kind != "histogram":
            raise TypeError(f"{name} is not a histogram")
        if value < 0:
            raise ValueError("histogram values must be non-negative")
        self._instruments[name].record(value, self._attributes(spec, attributes))

    def set(self, name: str, value: int | float, attributes: Mapping[str, Any] | None = None) -> None:
        spec = METRIC_SPECS[name]
        if spec.kind != "gauge":
            raise TypeError(f"{name} is not a gauge")
        self._instruments[name].set(value, self._attributes(spec, attributes))


_lock = threading.Lock()
_metrics = ProductionMetrics()
_provider: Any | None = None


def enable_metrics(*, backend: str | None = None, endpoint: str | None = None) -> ProductionMetrics:
    """Enable the process-wide production metrics registry."""

    global _metrics, _provider
    selected = (backend or os.getenv(METRICS_BACKEND_ENV, "none")).strip().lower()
    with _lock:
        if _provider is not None:
            _provider.shutdown()
            _provider = None
        if selected == "none":
            _metrics = ProductionMetrics()
            return _metrics
        if selected != "otlp":
            raise ValueError("metrics backend must be 'none' or 'otlp'")
        target = (endpoint or os.getenv(METRICS_OTLP_ENDPOINT_ENV, "")).strip()
        if not target:
            raise ValueError("OTLP metrics endpoint is required")
        try:
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
            from opentelemetry.sdk.metrics.view import (
                ExplicitBucketHistogramAggregation,
                View,
            )
            from opentelemetry.sdk.resources import Resource
        except ImportError as exc:
            raise ImportError("OTLP metrics require the seocho[otel] extra") from exc
        exporter = OTLPMetricExporter(endpoint=target, insecure=target.startswith("http://"))
        reader = PeriodicExportingMetricReader(exporter, export_interval_millis=5000)
        resource = Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME", "seocho")})
        views = (
            View(
                instrument_name="*.duration",
                instrument_unit="s",
                aggregation=ExplicitBucketHistogramAggregation(
                    _DURATION_SECONDS_BUCKETS
                ),
            ),
            View(
                instrument_name="*.latency",
                instrument_unit="ms",
                aggregation=ExplicitBucketHistogramAggregation(
                    _LATENCY_MILLISECONDS_BUCKETS
                ),
            ),
        )
        _provider = MeterProvider(
            resource=resource,
            metric_readers=(reader,),
            views=views,
        )
        _metrics = ProductionMetrics(_provider.get_meter("seocho", "1"))
        return _metrics


def get_metrics() -> ProductionMetrics:
    return _metrics


def shutdown_metrics() -> None:
    global _provider
    with _lock:
        if _provider is not None:
            _provider.shutdown()
            _provider = None


atexit.register(shutdown_metrics)


__all__ = ["METRIC_SPECS", "MetricSpec", "ProductionMetrics", "enable_metrics", "get_metrics", "shutdown_metrics"]
