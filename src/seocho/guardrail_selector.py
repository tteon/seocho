"""Domain-adaptive guardrail selector (ADR-0122 follow-up).

ADR-0122 measured, across all FinDER case types, that a rich ontology guardrail
*helps answers in entity/qualitative domains* (Governance +0.67, Legal/Company
+0.42, Risk +0.33) but is *neutral-to-harmful in numeric domains* (Financials
−0.08, Shareholder return −0.17 — even as conformance rose to 1.0). So there is no
single best guardrail: it is domain-conditional. This module encodes that learned
rule as an automatic selector.

Given candidate ontologies + a target-corpus profile (from an OPEN extraction,
ADR-0116 `build_corpus_profile`), it:

1. scores each candidate against the corpus with the corpus-aware scorecard
   (`profile="guardrail"`), getting `corpus_coverage`;
2. estimates the corpus's **numeric intensity** (how much of the entity mass is
   metric/quantity-like);
3. selects: for an **entity** corpus → the candidate with the best coverage
   (richest adequate); for a **numeric** corpus → the **leanest** candidate whose
   coverage is within ε of the best (avoid over-enrichment noise), and advises
   applying numeric validation (P3/ADR-0119) rather than vocabulary enrichment.

Pure and offline — consumes a precomputed corpus profile; no LLM, no hot path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Union

from .ontology import Ontology
from .ontology_scorecard import CorpusProfile, score_ontology

# Labels denoting a quantity/metric (the domains where vocabulary enrichment did
# not help answering — ADR-0122). Matched against the label and its spaced form.
_NUMERIC_LABEL_RE = re.compile(
    r"metric|value|amount|ratio|price|rate|revenue|income|ebitda|margin|return|yield|"
    r"monetary|measure|kpi|share\s*price|earnings|cash\s*flow|expense|cost|tax|debt|"
    r"asset|liabilit|equity|dividend|percentage|number|count|quantity",
    re.I,
)


def numeric_intensity(corpus_profile: CorpusProfile) -> float:
    """Frequency-weighted fraction of the corpus's entity mentions whose type is
    metric/quantity-like. ~0 = purely entity/qualitative corpus, ~1 = numeric."""
    freqs = corpus_profile.label_frequencies
    total = sum(freqs.values())
    if total == 0:
        return 0.0
    numeric_mass = 0
    for label, count in freqs.items():
        spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", label).replace("_", " ")
        if _NUMERIC_LABEL_RE.search(label) or _NUMERIC_LABEL_RE.search(spaced):
            numeric_mass += count
    return round(numeric_mass / total, 4)


@dataclass(slots=True)
class GuardrailRecommendation:
    chosen: str
    domain_kind: str                 # "entity" | "numeric" | "mixed"
    numeric_intensity: float
    rationale: str
    advisories: List[str] = field(default_factory=list)
    candidate_scores: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chosen": self.chosen, "domain_kind": self.domain_kind,
            "numeric_intensity": self.numeric_intensity, "rationale": self.rationale,
            "advisories": list(self.advisories), "candidate_scores": dict(self.candidate_scores),
        }


def select_guardrail(
    candidates: Dict[str, Ontology],
    corpus_profile: CorpusProfile,
    *,
    competency_questions: Optional[Sequence[Union[str, Dict[str, Any]]]] = None,
    numeric_threshold: float = 0.5,
    coverage_epsilon: float = 0.05,
) -> GuardrailRecommendation:
    """Pick the best guardrail ontology for a corpus, domain-adaptively.

    Parameters
    ----------
    candidates: name -> Ontology (e.g. {"lean": fibo_minus, "rich": fibo_plus}).
    corpus_profile: target-corpus profile from an open extraction.
    numeric_threshold: numeric_intensity at/above which the corpus is "numeric".
    coverage_epsilon: for numeric corpora, candidates within this of the best
        coverage are considered equivalent, so the leanest is chosen.
    """
    if not candidates:
        raise ValueError("no candidate ontologies provided")

    scores: Dict[str, Dict[str, Any]] = {}
    for name, onto in candidates.items():
        card = score_ontology(onto, corpus_profile=corpus_profile,
                              competency_questions=competency_questions, profile="guardrail")
        cc = card.dimension("corpus_coverage")
        scores[name] = {
            "corpus_coverage": round(cc.score, 4) if cc else 0.0,
            "overall": round(card.overall_score, 4),
            "grade": card.grade,
            "n_classes": len(onto.nodes),
        }

    ni = numeric_intensity(corpus_profile)
    best_cov = max(s["corpus_coverage"] for s in scores.values())
    advisories: List[str] = []

    if ni >= numeric_threshold:
        domain_kind = "numeric"
        # leanest candidate within ε of the best coverage — avoid over-enrichment noise
        eligible = {n: s for n, s in scores.items() if s["corpus_coverage"] >= best_cov - coverage_epsilon}
        chosen = min(eligible, key=lambda n: (eligible[n]["n_classes"], -eligible[n]["corpus_coverage"]))
        rationale = (f"numeric-intensive corpus (numeric_intensity={ni}); per ADR-0122 vocabulary "
                     f"enrichment does not improve numeric answers, so chose the leanest adequate "
                     f"guardrail '{chosen}' ({scores[chosen]['n_classes']} classes, "
                     f"coverage {scores[chosen]['corpus_coverage']}).")
        advisories.append("Apply numeric-fact VALIDATION (P3/ADR-0119): reconciliation, unit/scale, "
                          "fiscal-period checks — the lever for numeric domains, not more entity types.")
        advisories.append("Do NOT over-enrich the guardrail here; conformance gains did not translate "
                          "to answer accuracy in numeric domains (ADR-0122).")
    else:
        domain_kind = "entity" if ni <= (1.0 - numeric_threshold) else "mixed"
        # richest adequate: maximize coverage (ties → richer/more classes)
        chosen = max(scores, key=lambda n: (scores[n]["corpus_coverage"], scores[n]["n_classes"]))
        rationale = (f"entity/qualitative corpus (numeric_intensity={ni}); per ADR-0122 a richer "
                     f"guardrail materially improves answers here, so chose the highest-coverage "
                     f"guardrail '{chosen}' (coverage {scores[chosen]['corpus_coverage']}, "
                     f"{scores[chosen]['n_classes']} classes).")
        if domain_kind == "mixed":
            advisories.append("Mixed numeric/entity corpus — consider splitting by sub-domain and "
                              "selecting a guardrail per split.")

    recommendation = GuardrailRecommendation(
        chosen=chosen, domain_kind=domain_kind, numeric_intensity=ni,
        rationale=rationale, advisories=advisories, candidate_scores=scores,
    )

    # ADR-0144 §6: the selection was previously returned but logged nowhere.
    # Emit an audit span so the decision (and its inputs) is observable.
    try:
        from .tracing import is_tracing_enabled, log_span

        if is_tracing_enabled():
            log_span(
                "ontology.guardrail_select",
                input_data={"candidates": sorted(candidates)},
                output_data={
                    "chosen": chosen,
                    "domain_kind": domain_kind,
                    "numeric_intensity": ni,
                },
                metadata={"rationale": rationale, "candidate_scores": scores},
                tags=["ontology", "guardrail"],
            )
    except Exception:
        pass

    return recommendation


def select_per_domain(
    domain_profiles: Dict[str, CorpusProfile],
    candidates: Dict[str, Ontology],
    **kwargs: Any,
) -> Dict[str, GuardrailRecommendation]:
    """Run :func:`select_guardrail` for each domain's corpus profile — the
    operational form of ADR-0122's per-category finding."""
    return {domain: select_guardrail(candidates, profile, **kwargs)
            for domain, profile in domain_profiles.items()}


def load_corpus_profile(data: Union[str, Dict[str, Any]]) -> CorpusProfile:
    """Build a CorpusProfile from a label->frequency dict, a CorpusProfile dict
    (``label_frequencies``), or an experiment record carrying ``corpus_profile``."""
    import json
    from pathlib import Path

    if isinstance(data, (str, Path)):
        with open(data, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    if "corpus_profile" in data and isinstance(data["corpus_profile"], dict):
        data = data["corpus_profile"]
    if "label_frequencies" in data:
        return CorpusProfile(
            label_frequencies={str(k): int(v) for k, v in data["label_frequencies"].items()},
            doc_count=int(data.get("doc_count", 0)), source=str(data.get("source", "")),
        )
    # assume a bare {label: freq} mapping
    return CorpusProfile(label_frequencies={str(k): int(v) for k, v in data.items()})


# ---------------------------------------------------------------------------
# Learning the numeric-intensity threshold from measured answer-accuracy deltas
# (ADR-0126). ADR-0123 hard-coded 0.5; ADR-0122/0124 give per-domain deltas
# (rich-minus-sparse answer accuracy) we can use to calibrate the boundary.
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DomainObservation:
    """A measured per-domain point: how numeric the domain's corpus is, and
    whether the richer guardrail actually helped its answers."""

    domain: str
    numeric_intensity: float
    rich_minus_sparse_delta: float  # measured answer-accuracy delta; >0 = rich helped


def calibrate_numeric_threshold(
    observations: Sequence[DomainObservation],
    *,
    default: float = 0.5,
) -> Dict[str, Any]:
    """Find the numeric_intensity threshold T that best separates domains where
    the rich guardrail helped (delta>0 → should be treated as entity, ni<T) from
    those where it did not (delta<=0 → numeric, ni>=T).

    Scans candidate thresholds (midpoints between sorted unique intensities, plus
    the 0/1 bounds), scores each by agreement with the measured outcomes, and
    returns the best (ties broken toward ``default``). Pure/deterministic."""
    obs = list(observations)
    if not obs:
        return {"threshold": default, "accuracy": None, "n": 0, "default": default}

    intensities = sorted({o.numeric_intensity for o in obs})
    candidates = [0.0]
    for a, b in zip(intensities, intensities[1:]):
        candidates.append((a + b) / 2.0)
    candidates.append(1.0)
    # also allow thresholds just above each observed intensity so a point can be
    # classified numeric (ni >= T) exactly at a boundary
    candidates = sorted(set(candidates))

    def agreement(t: float) -> int:
        correct = 0
        for o in obs:
            predict_rich_helps = o.numeric_intensity < t
            actual_rich_helps = o.rich_minus_sparse_delta > 0
            if predict_rich_helps == actual_rich_helps:
                correct += 1
        return correct

    best_t = default
    best_score = -1
    for t in candidates:
        s = agreement(t)
        if s > best_score or (s == best_score and abs(t - default) < abs(best_t - default)):
            best_score, best_t = s, t

    return {
        "threshold": round(best_t, 4),
        "accuracy": round(best_score / len(obs), 4),
        "n": len(obs),
        "default": default,
    }
