"""
seocho.eval — evaluation harness for ontology delivery.

Closes seocho-foq7 (the umbrella ticket). Provides the primitives for
measuring where time and tokens go in the indexing → query path:

- :class:`BenchmarkCorpus` — fixed test corpus (documents + queries)
  with deterministic seeds.
- :class:`BenchmarkRunner` — runs the corpus against a configurable
  Seocho instance, captures per-stage timings, dumps JSONL spans.
- :func:`load_jsonl_spans` / :func:`compute_run_summary` — replay the
  trace artifact and produce per-policy / per-config aggregates.

Pairs with the CLAUDE.md §18 KV-cache hit-ratio target (≥85%) and the
beads catalogue of enhancement candidates: seocho-x0t5 (KV-cache-aware
ontology), seocho-cvys (slice extraction), seocho-tfql (response
cache), seocho-6c9v (pre-warmed factories), seocho-a9ay (delta
streaming), seocho-oilg (token budget). Each enhancement's payoff is
measured against this harness.
"""

from .benchmark import (
    BenchmarkCorpus,
    BenchmarkRunner,
    BenchmarkSpan,
    StageTimings,
    compute_run_summary,
    load_jsonl_spans,
)

__all__ = [
    "BenchmarkCorpus",
    "BenchmarkRunner",
    "BenchmarkSpan",
    "StageTimings",
    "compute_run_summary",
    "load_jsonl_spans",
]
