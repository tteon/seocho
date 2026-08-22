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
    compare_ontology_evaluation_runs,
    compute_run_summary,
    load_jsonl_spans,
)
from .plan_audit import (
    PlanAudit,
    PlanComparison,
    PlanOperator,
    audit_profile,
    compare_plans,
    emit_plan_comparison_metrics,
)
from .semantic_scorecard import (
    SCORECARD_SCHEMA_VERSION,
    SemanticUtilityScorecard,
    compare_semantic_utility,
    score_semantic_utility,
)
from .case_envelope import (
    CASE_ENVELOPE_SCHEMA_VERSION,
    annotation_coverage,
    case_receipt,
    validate_case_envelope,
)

__all__ = [
    "BenchmarkCorpus",
    "BenchmarkRunner",
    "BenchmarkSpan",
    "StageTimings",
    "compare_ontology_evaluation_runs",
    "compute_run_summary",
    "load_jsonl_spans",
    "PlanAudit",
    "PlanComparison",
    "PlanOperator",
    "audit_profile",
    "compare_plans",
    "emit_plan_comparison_metrics",
    "SCORECARD_SCHEMA_VERSION",
    "SemanticUtilityScorecard",
    "compare_semantic_utility",
    "score_semantic_utility",
    "CASE_ENVELOPE_SCHEMA_VERSION",
    "annotation_coverage",
    "case_receipt",
    "validate_case_envelope",
]
