"""
Benchmark harness for ontology-delivery evaluation.

Captures per-call timings, token usage (when reported), and degraded /
fallback / observability flags into JSONL artefacts. Replay + summary
helpers produce the per-policy / per-config aggregates needed to grade
enhancements (KV-cache, slicing, response cache, factories).
"""

from __future__ import annotations

import json
import os
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence


@dataclass
class StageTimings:
    """Wall-clock timings per logical stage of a single benchmark call."""

    compile_ontology_seconds: float = 0.0
    extract_seconds: float = 0.0
    validate_seconds: float = 0.0
    write_seconds: float = 0.0
    query_seconds: float = 0.0
    total_seconds: float = 0.0

    def to_dict(self) -> Dict[str, float]:
        return {
            "compile_ontology_seconds": self.compile_ontology_seconds,
            "extract_seconds": self.extract_seconds,
            "validate_seconds": self.validate_seconds,
            "write_seconds": self.write_seconds,
            "query_seconds": self.query_seconds,
            "total_seconds": self.total_seconds,
        }


@dataclass
class BenchmarkSpan:
    """Single benchmark call (one document indexed or one question asked)."""

    operation: str  # "index" | "query"
    config_label: str  # caller-supplied tag, e.g. "fast", "thorough"
    workspace_id: str
    ontology_identity_hash: str
    user_id: str
    input_preview: str
    output_preview: str
    stage_timings: StageTimings
    prompt_tokens: int = 0
    completion_tokens: int = 0
    degraded: bool = False
    fallback_from: str = ""
    degraded_observability: bool = False
    cache_prefix_hash: str = ""  # seocho-x0t5 — hash of stable_prefix at call time
    extra: Dict[str, Any] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "operation": self.operation,
            "config_label": self.config_label,
            "workspace_id": self.workspace_id,
            "ontology_identity_hash": self.ontology_identity_hash,
            "user_id": self.user_id,
            "input_preview": self.input_preview[:200],
            "output_preview": self.output_preview[:300],
            "stage_timings": self.stage_timings.to_dict(),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "degraded": self.degraded,
            "fallback_from": self.fallback_from,
            "degraded_observability": self.degraded_observability,
            "cache_prefix_hash": self.cache_prefix_hash,
            "started_at": self.started_at,
            "extra": dict(self.extra),
        }


@dataclass
class BenchmarkCorpus:
    """Fixed corpus for reproducible runs.

    Documents are indexed in order; queries are asked after indexing
    completes. Both lists are caller-supplied so the harness stays
    domain-agnostic — the FIBO BE minimal slice from tutorial 3 makes
    a good default.
    """

    name: str
    documents: List[str]
    queries: List[str]
    seed: int = 42

    def __post_init__(self) -> None:
        # Defensive copies so the corpus is immutable from the runner's view.
        self.documents = list(self.documents)
        self.queries = list(self.queries)


class BenchmarkRunner:
    """Run a :class:`BenchmarkCorpus` and emit BenchmarkSpan records.

    The runner is intentionally callback-driven so it stays decoupled
    from any specific Seocho configuration. The caller supplies:

    - ``index_fn(text) -> result_dict``: typically wraps Session.add.
    - ``query_fn(question) -> answer_str``: typically wraps Session.ask.
    - ``config_label``: free-form tag persisted on every span (e.g.
      "kv-cache=on", "slicing=v1", "policy=thorough").
    """

    def __init__(
        self,
        *,
        config_label: str,
        workspace_id: str,
        ontology_identity_hash: str,
        user_id: str = "",
        cache_prefix_hash: str = "",
        output_path: Optional[str] = None,
    ) -> None:
        self.config_label = str(config_label)
        self.workspace_id = str(workspace_id)
        self.ontology_identity_hash = str(ontology_identity_hash)
        self.user_id = str(user_id)
        self.cache_prefix_hash = str(cache_prefix_hash)
        self.output_path = output_path
        self.spans: List[BenchmarkSpan] = []
        if output_path:
            os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)

    def _emit(self, span: BenchmarkSpan) -> None:
        self.spans.append(span)
        if self.output_path:
            with open(self.output_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(span.to_dict(), default=str) + "\n")

    def run_index(
        self,
        document: str,
        index_fn: Callable[[str], Dict[str, Any]],
    ) -> BenchmarkSpan:
        timings = StageTimings()
        t0 = time.time()
        try:
            result = index_fn(document)
        except Exception as exc:  # noqa: BLE001
            result = {"error": str(exc), "degraded": True, "fallback_from": "exception"}
        timings.total_seconds = time.time() - t0
        # Try to pick up substage timings if the caller stashed them.
        for key in ("extract_seconds", "validate_seconds", "write_seconds",
                    "compile_ontology_seconds"):
            v = result.get(key)
            if isinstance(v, (int, float)):
                setattr(timings, key, float(v))
        usage = result.get("usage", {}) or {}
        span = BenchmarkSpan(
            operation="index",
            config_label=self.config_label,
            workspace_id=self.workspace_id,
            ontology_identity_hash=self.ontology_identity_hash,
            user_id=self.user_id,
            input_preview=document,
            output_preview=str(result.get("source_id") or result.get("answer") or ""),
            stage_timings=timings,
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            degraded=bool(result.get("degraded", False)),
            fallback_from=str(result.get("fallback_from", "")),
            degraded_observability=bool(result.get("degraded_observability", False)),
            cache_prefix_hash=self.cache_prefix_hash,
            extra={"raw_result_keys": sorted(result.keys())[:10]},
        )
        self._emit(span)
        return span

    def run_query(
        self,
        question: str,
        query_fn: Callable[[str], str],
    ) -> BenchmarkSpan:
        timings = StageTimings()
        t0 = time.time()
        answer = ""
        degraded = False
        try:
            answer = query_fn(question) or ""
        except Exception as exc:  # noqa: BLE001
            answer = f"<error: {exc}>"
            degraded = True
        timings.query_seconds = time.time() - t0
        timings.total_seconds = timings.query_seconds
        span = BenchmarkSpan(
            operation="query",
            config_label=self.config_label,
            workspace_id=self.workspace_id,
            ontology_identity_hash=self.ontology_identity_hash,
            user_id=self.user_id,
            input_preview=question,
            output_preview=str(answer),
            stage_timings=timings,
            degraded=degraded,
            cache_prefix_hash=self.cache_prefix_hash,
        )
        self._emit(span)
        return span

    def run(
        self,
        corpus: BenchmarkCorpus,
        *,
        index_fn: Callable[[str], Dict[str, Any]],
        query_fn: Callable[[str], str],
    ) -> List[BenchmarkSpan]:
        for doc in corpus.documents:
            self.run_index(doc, index_fn)
        for q in corpus.queries:
            self.run_query(q, query_fn)
        return list(self.spans)


# ---------------------------------------------------------------------------
# Replay + summary
# ---------------------------------------------------------------------------


def load_jsonl_spans(path: str) -> List[Dict[str, Any]]:
    """Load BenchmarkSpan dicts from a JSONL file. Skips corrupt lines."""
    out: List[Dict[str, Any]] = []
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (len(sorted_values) - 1) * (pct / 100.0)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_values) - 1)
    weight = rank - lo
    return float(sorted_values[lo] * (1 - weight) + sorted_values[hi] * weight)


def compute_run_summary(spans: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate metrics from BenchmarkSpan dicts.

    Output groups by ``config_label`` so a single run can compare two
    configurations side-by-side (e.g. ``cache=on`` vs ``cache=off``).
    Per-group stats:

    - count
    - latency p50 / p95 / mean (seconds)
    - degraded_rate (fraction with degraded=True)
    - degraded_observability_rate
    - prompt_cache_hit_ratio (estimated from cache_prefix_hash repetition)
    - total tokens (prompt + completion)
    """
    by_label: Dict[str, List[Dict[str, Any]]] = {}
    for span in spans:
        by_label.setdefault(str(span.get("config_label", "")), []).append(span)

    summary: Dict[str, Dict[str, Any]] = {}
    for label, group in by_label.items():
        latencies = [
            float(s.get("stage_timings", {}).get("total_seconds", 0.0)) for s in group
        ]
        prompt_hashes = [str(s.get("cache_prefix_hash", "")) for s in group]
        # Cache-hit ratio estimate: a "hit" is a span whose cache_prefix_hash
        # matches a hash that appeared in an earlier span (within this group).
        seen: set = set()
        hits = 0
        for h in prompt_hashes:
            if not h:
                continue
            if h in seen:
                hits += 1
            else:
                seen.add(h)
        non_empty = [h for h in prompt_hashes if h]
        cache_ratio = (hits / len(non_empty)) if non_empty else 0.0
        summary[label] = {
            "count": len(group),
            "latency_p50": _percentile(latencies, 50),
            "latency_p95": _percentile(latencies, 95),
            "latency_mean": (statistics.fmean(latencies) if latencies else 0.0),
            "degraded_rate": (
                sum(1 for s in group if s.get("degraded")) / len(group)
            ),
            "degraded_observability_rate": (
                sum(1 for s in group if s.get("degraded_observability")) / len(group)
            ),
            "prompt_cache_hit_ratio": cache_ratio,
            "total_prompt_tokens": sum(int(s.get("prompt_tokens", 0)) for s in group),
            "total_completion_tokens": sum(int(s.get("completion_tokens", 0)) for s in group),
        }
    return summary
