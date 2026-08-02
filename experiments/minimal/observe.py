"""Tracing and logging for the minimal harness.

The point of this module is that a reader should never have to take an
experiment's word for anything. Every stage records what went in, what came
out, and how long it took, in three forms:

  run.log      human narrative, readable top to bottom
  trace.jsonl  one JSON object per stage, with full input and output payloads
  OTel spans   the same structure, for a collector if one is running

The JSONL record is the source of truth for auditing a run. It is written
incrementally, so a run that dies halfway still leaves everything it did.
"""
from __future__ import annotations

import hashlib
import json
import os
import logging
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import semconv
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor, SpanExporter,
    SpanExportResult)


class JsonlSpanExporter(SpanExporter):
    """Persist OTel spans as JSON lines.

    A collector is a moving part that has to be running when the experiment
    runs, and the one on this machine belongs to a different checkout. Spans are
    therefore written to the run directory, where they survive restarts and can
    be read months later with nothing installed. Set SEOCHO_OTLP_ENDPOINT to
    additionally ship them to a real backend.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.write_text("")

    def export(self, spans) -> SpanExportResult:
        with self._path.open("a", encoding="utf-8") as fh:
            for span in spans:
                ctx = span.get_span_context()
                fh.write(json.dumps({
                    "name": span.name,
                    "trace_id": format(ctx.trace_id, "032x"),
                    "span_id": format(ctx.span_id, "016x"),
                    "parent_span_id": (format(span.parent.span_id, "016x")
                                       if span.parent else None),
                    "start_unix_nano": span.start_time,
                    "end_unix_nano": span.end_time,
                    "duration_ms": round((span.end_time - span.start_time) / 1e6, 3),
                    "status": span.status.status_code.name,
                    "attributes": {k: v for k, v in (span.attributes or {}).items()},
                }, ensure_ascii=False, default=str) + "\n")
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None

_LOG = logging.getLogger("minimal")
_MAX_INLINE = 4000  # payload characters kept verbatim in the trace

# The driver switches quote style when the statement itself contains one, so
# both have to be accepted; matching only the apostrophe form silently dropped
# 24 of 26 statements in the first run.
_RUN_LINE = re.compile(r"C: RUN (?P<q>['\"])(?P<cypher>.*)(?P=q) (?P<rest>\{.*\})$")
_TIMING_LINE = re.compile(r"S: SUCCESS (?P<meta>\{.*\})$")


class DriverLogHandler(logging.Handler):
    """Capture what the Neo4j driver itself reports, not what we think it did.

    The driver logs the Bolt exchange under the `neo4j`, `neo4j.io`, and
    `neo4j.pool` loggers. Two things there cannot be obtained any other way:
    the statement exactly as it went over the wire, parameters included, and
    the server's own `t_first` and `t_last` timings. Our wrapper can only time
    the round trip, which folds in client-side result materialisation.

    The driver's own docs say the log format is for human consumption and may
    change, so parsing is best-effort: an unparsed line is still written
    verbatim, and a missed timing leaves the field absent rather than zero.
    """

    LOGGERS = ("neo4j", "neo4j.io", "neo4j.pool")

    def __init__(self, path: Path) -> None:
        super().__init__(level=logging.DEBUG)
        self._path = path
        self._path.write_text("")
        self.queries: list[dict[str, Any]] = []
        self._pending: dict[str, dict[str, Any]] = {}
        self.counts: dict[str, int] = {}

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        self.counts[record.name] = self.counts.get(record.name, 0) + 1
        entry: dict[str, Any] = {"logger": record.name, "level": record.levelname,
                                 "message": message}
        # The driver prefixes every line with the connection id in brackets,
        # which is what lets a RUN be matched to the SUCCESS that answers it.
        conn = message[1:6] if message.startswith("[#") else ""
        run = _RUN_LINE.search(message)
        if run:
            entry["kind"] = "query"
            entry["cypher"] = run.group("cypher")
            entry["params"] = run.group("rest")
            self._pending[conn] = entry
            span = trace.get_current_span()
            span.set_attribute("db.system", "neo4j")
            span.set_attribute("db.query.text", run.group("cypher")[:2000])
        else:
            timing = _TIMING_LINE.search(message)
            if timing and ("t_last" in message or "t_first" in message):
                meta = timing.group("meta")
                entry["kind"] = "result"
                for field in ("t_first", "t_last"):
                    found = re.search(rf"'{field}': (\d+)", meta)
                    if found:
                        entry[field] = int(found.group(1))
                        span = trace.get_current_span()
                        span.set_attribute(f"db.neo4j.{field}_ms", int(found.group(1)))
                waiting = self._pending.get(conn)
                if waiting is not None and "t_last" in entry:
                    self.queries.append({**waiting, "t_first_ms": entry.get("t_first"),
                                         "t_last_ms": entry.get("t_last")})
                    self._pending.pop(conn, None)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def attach(self) -> None:
        for name in self.LOGGERS:
            logger = logging.getLogger(name)
            logger.setLevel(logging.DEBUG)
            logger.addHandler(self)

    def detach(self) -> None:
        for name in self.LOGGERS:
            logging.getLogger(name).removeHandler(self)

    def summary(self) -> dict[str, Any]:
        served = [q for q in self.queries if q.get("t_last_ms") is not None]
        total = sum(q["t_last_ms"] for q in served)
        return {"records": sum(self.counts.values()), "by_logger": dict(self.counts),
                "queries": len(self.queries),
                "server_ms_total": total,
                "server_ms_slowest": max((q["t_last_ms"] for q in served), default=0)}


def _shrink(value: Any) -> Any:
    """Keep payloads readable without silently losing that they were cut."""
    if isinstance(value, str):
        if len(value) <= _MAX_INLINE:
            return value
        return {"_truncated": True, "chars": len(value),
                "head": value[:_MAX_INLINE]}
    if isinstance(value, dict):
        return {k: _shrink(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        if len(value) <= 50:
            return [_shrink(v) for v in value]
        return {"_truncated": True, "items": len(value),
                "head": [_shrink(v) for v in value[:50]]}
    return value


class Run:
    """One traced execution, writing into its own directory."""

    def __init__(self, root: Path, name: str, config: dict[str, Any],
                 console_spans: bool = False) -> None:
        # Split what decides the result from what merely describes the run. Two
        # runs sharing a fingerprint must produce identical numbers; if they do
        # not, something outside the declared factors moved, and that is the
        # defect to chase rather than a result to report.
        decisive = config.get("decisive", {})
        self.fingerprint = hashlib.sha256(
            json.dumps(decisive, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.dir = Path(root) / f"{stamp}-{name}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.trace_path = self.dir / "trace.jsonl"
        self.trace_path.write_text("")
        self.stages: list[str] = []
        self._t0 = time.perf_counter()

        (self.dir / "config.resolved.json").write_text(
            json.dumps({"fingerprint": self.fingerprint, **config},
                       indent=2, ensure_ascii=False, default=str) + "\n")
        (self.dir / "decisive.json").write_text(
            json.dumps(decisive, indent=2, sort_keys=True,
                       ensure_ascii=False, default=str) + "\n")

        handler = logging.FileHandler(self.dir / "run.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-5s %(message)s", "%H:%M:%S"))
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(message)s"))
        _LOG.handlers.clear()
        _LOG.setLevel(logging.INFO)
        _LOG.addHandler(handler)
        _LOG.addHandler(stream)

        provider = TracerProvider(resource=Resource.create(
            {"service.name": "seocho-minimal", "run.name": name,
             "run.fingerprint": self.fingerprint}))
        # Always persist spans next to the run; a backend is optional.
        self.spans_path = self.dir / "spans.jsonl"
        provider.add_span_processor(SimpleSpanProcessor(JsonlSpanExporter(self.spans_path)))
        endpoint = os.environ.get("SEOCHO_OTLP_ENDPOINT")
        if endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter)
                provider.add_span_processor(
                    BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
            except ImportError:
                pass
        if console_spans:
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(provider)
        self._tracer = trace.get_tracer("minimal")
        self._provider = provider

        # Driver-level observability: what Neo4j was actually asked, and how
        # long the server said it took. Attached for the whole run so a query
        # issued outside a stage still leaves a record.
        self.driver_log = DriverLogHandler(self.dir / "driver.jsonl")
        self.driver_log.attach()

        self.log(f"run directory: {self.dir}")
        self.log(f"decisive fingerprint: {self.fingerprint}")
        for key, value in sorted(decisive.items()):
            self.log(f"  decisive.{key} = {json.dumps(value, default=str)[:200]}")

    def log(self, message: str) -> None:
        _LOG.info(message)

    def _append(self, record: dict[str, Any]) -> None:
        with self.trace_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"fingerprint": self.fingerprint, **record},
                                ensure_ascii=False, default=str) + "\n")

    @contextmanager
    def stage(self, name: str, /, **inputs: Any) -> Iterator[dict[str, Any]]:
        """Record one pipeline stage. Assign to the yielded dict to log outputs."""
        self.stages.append(name)
        outputs: dict[str, Any] = {}
        started = time.perf_counter()
        self.log(f"[{name}] in  {json.dumps(_shrink(inputs), ensure_ascii=False, default=str)[:600]}")
        with self._tracer.start_as_current_span(name) as span:
            span.set_attribute(semconv.FINGERPRINT, self.fingerprint)
            for key, value in inputs.items():
                declared = semconv.attribute_for(key)
                span.set_attribute(declared or f"in.{key}", semconv.coerce(value))
            try:
                yield outputs
            except Exception as exc:
                elapsed = time.perf_counter() - started
                span.record_exception(exc)
                self.log(f"[{name}] FAILED after {elapsed:.2f}s: {exc}")
                self._append({"stage": name, "status": "error", "error": repr(exc),
                              "seconds": round(elapsed, 4),
                              "input": _shrink(inputs)})
                raise
            elapsed = time.perf_counter() - started
            for key, value in outputs.items():
                declared = semconv.attribute_for(key)
                span.set_attribute(declared or f"out.{key}", semconv.coerce(value))
            self.log(f"[{name}] out {json.dumps(_shrink(outputs), ensure_ascii=False, default=str)[:600]}"
                     f"  ({elapsed:.2f}s)")
            self._append({"stage": name, "status": "ok",
                          "seconds": round(elapsed, 4),
                          "input": _shrink(inputs), "output": _shrink(outputs)})

    def record_llm_request(self, endpoint: str | None, request: dict[str, Any]) -> None:
        """Persist a model request before it is issued.

        The gateway is OpenAI-compatible and exposes no internal telemetry, so
        the boundary is the limit of what can be observed. Writing the request
        first means a call that hangs or dies still leaves what was sent.
        """
        span = trace.get_current_span()
        span.set_attribute("gen_ai.system", "openai_compatible")
        span.set_attribute("gen_ai.request.model", str(request.get("model", "")))
        span.set_attribute("gen_ai.request.temperature", float(request.get("temperature", 0)))
        span.set_attribute("gen_ai.request.max_tokens", int(request.get("max_tokens", 0)))
        span.set_attribute("server.address", endpoint or "default")
        self._append({"stage": "llm.request", "status": "sent",
                      "endpoint": endpoint, "request": _shrink(request)})

    def record_llm_response(self, model: str, text: str, finish_reason: str | None,
                            prompt_tokens: int | None, completion_tokens: int | None,
                            latency_s: float) -> None:
        span = trace.get_current_span()
        span.set_attribute("gen_ai.response.model", str(model))
        if finish_reason:
            span.set_attribute("gen_ai.response.finish_reasons", str(finish_reason))
        if prompt_tokens is not None:
            span.set_attribute("gen_ai.usage.input_tokens", int(prompt_tokens))
        if completion_tokens is not None:
            span.set_attribute("gen_ai.usage.output_tokens", int(completion_tokens))
        self.log(f"    response model={model} finish={finish_reason} "
                 f"tokens in={prompt_tokens} out={completion_tokens} "
                 f"latency={latency_s}s")
        self._append({"stage": "llm.response", "status": "ok", "model": model,
                      "finish_reason": finish_reason,
                      "prompt_tokens": prompt_tokens,
                      "completion_tokens": completion_tokens,
                      "seconds": latency_s, "text": _shrink(text)})

    def finish(self, result: dict[str, Any]) -> Path:
        total = time.perf_counter() - self._t0
        driver = self.driver_log.summary()
        self.driver_log.detach()
        payload = {"fingerprint": self.fingerprint, "stages": self.stages,
                   "seconds": round(total, 3), "driver": driver, **result}
        (self.dir / "result.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n")
        if driver["queries"]:
            self.log(f"driver: {driver['queries']} queries, "
                     f"{driver['server_ms_total']}ms on the server, "
                     f"slowest {driver['server_ms_slowest']}ms")
        self.log(f"done in {total:.2f}s over {len(self.stages)} stages -> {self.dir}")
        self._provider.shutdown()
        return self.dir
