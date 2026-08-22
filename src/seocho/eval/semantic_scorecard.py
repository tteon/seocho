"""Outcome scorecard for testing semantic lift from governed RDF ingestion.

Constraint conformance is not agent usefulness.  This module exposes the
operational proxies that an E2E run can observe and keeps gold-answer judging
as a separate, explicit measurement.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from statistics import fmean
from typing import Any, Mapping, Sequence


SCORECARD_SCHEMA_VERSION = "seocho.agent_semantic_scorecard.v1"


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    return round(float(numerator) / float(denominator), 6) if denominator > 0 else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _normalise(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").casefold()).strip()


@dataclass(frozen=True, slots=True)
class SemanticUtilityScorecard:
    """Deterministic outcome evidence from one indexed corpus and query set."""

    schema_version: str
    indexing: dict[str, Any]
    agent: dict[str, Any]
    governance: dict[str, Any]
    limitations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["limitations"] = list(self.limitations)
        return result


def score_semantic_utility(
    indexing: Mapping[str, Any] | None,
    queries: Sequence[Mapping[str, Any]] | None,
    *,
    governance: Mapping[str, Any] | None = None,
) -> SemanticUtilityScorecard:
    """Summarise observable semantic utility without fabricating gold labels.

    ``relation_density`` is not relation recall, and ``supported_rate`` is an
    evidence-routing proxy rather than grounded answer accuracy.
    """
    indexing, records = indexing or {}, list(queries or ())
    found = int(indexing.get("files_found", len(indexing.get("results", []) or [])) or 0)
    indexed, failed = int(indexing.get("files_indexed", 0) or 0), int(indexing.get("files_failed", 0) or 0)
    nodes, relationships = int(indexing.get("total_nodes", 0) or 0), int(indexing.get("total_relationships", 0) or 0)
    attempted = max(found, indexed + failed)
    answered = [r for r in records if not r.get("error") and not r.get("empty")]
    assessed = [r for r in records if str(r.get("support_status", "")).strip()]
    supported = [r for r in assessed if str(r.get("support_status", "")).casefold() == "supported"]
    evidence = [r for r in records if int(r.get("selected_triple_count", 0) or 0) > 0]
    coverage = [v for r in records if (v := _number(r.get("coverage"))) is not None]
    latency = [v for r in records if (v := _number(r.get("latency_s"))) is not None]
    expected = [r for r in records if _normalise(r.get("expect"))]
    reference_contains = [r for r in expected if _normalise(r.get("expect")) in _normalise(r.get("answer"))]
    receipt = dict(governance or {})
    limitations = [
        "SHACL/RDF promotion establishes constraint conformance, not semantic usefulness.",
        "relation_density is not relation recall; recall requires a labelled gold graph.",
        "supported_rate is an evidence proxy; use blinded judging or gold answers for grounded correctness.",
    ]
    if not expected:
        limitations.append("No expected answers supplied; reference answer containment is unavailable.")
    if not records:
        limitations.append("No query records supplied; agent-usefulness metrics are unavailable.")
    return SemanticUtilityScorecard(
        schema_version=SCORECARD_SCHEMA_VERSION,
        indexing={
            "documents_attempted": attempted, "documents_indexed": indexed, "documents_failed": failed,
            "admission_rate": _ratio(indexed, attempted),
            "validation_errors": int(indexing.get("validation_errors_count", 0) or 0),
            "validation_errors_per_indexed_document": _ratio(int(indexing.get("validation_errors_count", 0) or 0), indexed),
            "nodes": nodes, "relationships": relationships, "relation_density": _ratio(relationships, nodes),
        },
        agent={
            "questions": len(records), "answered_rate": _ratio(len(answered), len(records)),
            "evidence_available_rate": _ratio(len(evidence), len(records)),
            "mean_evidence_coverage": round(fmean(coverage), 6) if coverage else None,
            "assessed_questions": len(assessed), "supported_rate": _ratio(len(supported), len(assessed)),
            "missing_slots_per_question": _ratio(sum(len(r.get("missing_slots", []) or []) for r in records), len(records)),
            "reference_answer_questions": len(expected), "reference_contains_rate": _ratio(len(reference_contains), len(expected)),
            "mean_latency_s": round(fmean(latency), 6) if latency else None,
        },
        governance={
            "receipt_present": bool(receipt), "promotable": receipt.get("promotable"),
            "rdf_bundle_sha256": receipt.get("rdf_bundle_sha256") or receipt.get("bundle_sha256"),
            "projection_receipt_sha256": receipt.get("projection_receipt_sha256"),
        },
        limitations=tuple(limitations),
    )


def compare_semantic_utility(
    baseline: Mapping[str, Any], governed: Mapping[str, Any], *, minimum_questions: int = 10
) -> dict[str, Any]:
    """Apply pre-registered, conservative lift gates to matched scorecards."""
    base_agent, gov_agent = dict(baseline.get("agent", {})), dict(governed.get("agent", {}))
    base_index, gov_index = dict(baseline.get("indexing", {})), dict(governed.get("indexing", {}))
    paths = {
        "admission_rate": (base_index, gov_index), "mean_evidence_coverage": (base_agent, gov_agent),
        "supported_rate": (base_agent, gov_agent), "missing_slots_per_question": (base_agent, gov_agent),
        "reference_contains_rate": (base_agent, gov_agent), "mean_latency_s": (base_agent, gov_agent),
    }
    deltas = {name: (round(right - left, 6) if (left := _number(before.get(name))) is not None and (right := _number(after.get(name))) is not None else None) for name, (before, after) in paths.items()}
    questions = min(int(base_agent.get("questions", 0) or 0), int(gov_agent.get("questions", 0) or 0))
    if questions < minimum_questions:
        verdict, reason = "insufficient_sample", f"need at least {minimum_questions} matched questions; observed {questions}"
    elif deltas["mean_evidence_coverage"] is None or deltas["missing_slots_per_question"] is None:
        verdict, reason = "inconclusive", "matched coverage and missing-slot measurements are required"
    elif deltas["admission_rate"] is not None and deltas["admission_rate"] < -0.02:
        verdict, reason = "does_not_support_hypothesis", "governed admission fell by more than 2 percentage points"
    elif deltas["mean_evidence_coverage"] >= 0.05 and deltas["missing_slots_per_question"] <= -0.05 and (deltas["reference_contains_rate"] is None or deltas["reference_contains_rate"] >= -0.02):
        verdict, reason = "supports_hypothesis", "coverage improved by ≥5 points, missing slots fell, and reference proxy did not materially regress"
    else:
        verdict, reason = "inconclusive", "semantic-lift thresholds were not met"
    return {"schema_version": "seocho.rdf_governance_lift_comparison.v1", "minimum_questions": minimum_questions, "matched_questions": questions, "deltas": deltas, "verdict": verdict, "reason": reason, "limitations": ["Use matched corpus/model/ontology-revision conditions.", "Replace reference containment with blinded judging before a product claim."]}
