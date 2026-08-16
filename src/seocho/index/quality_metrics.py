"""Emit ontology-quality and extraction-quality signals.

Two things were measurable and unmeasured.

`score_ontology` already names what an ontology is missing — run against a
hand-authored enterprise ontology it reported grade B with "6 classes but no
'broader' hierarchy", "No corpus profile supplied", "No competency questions
supplied" — and nothing emitted any of it. A corpus could be, and was, indexed
against an unscored ontology, with the deficiency only surfacing later as wrong
answers.

Extraction quality was likewise invisible. A 322-document run produced eight
distinct values for one `P(str)` property (`CURRENT`, `current`, `SUPERSEDED`,
`superseded`, `proposed`, `applied`, `pending`, `mitigation`), a node whose
label was `EntityType` — the prompt's own output example, leaked into the data —
and 13 documents that yielded nothing. All three were found by reading the JSONL
afterwards. None of them raised.

The emitters live here rather than inline at the call sites so the metric names
have one home, and so a caller that does not want telemetry simply does not call
them. Every function is a no-op when metrics are disabled, which is the default.

Attribute values must stay bounded — `ProductionMetrics` rejects unbounded ones
by contract — so labels and property names are passed through, and free-text
values never are.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

__all__ = [
    "record_scorecard",
    "record_contract_gaps",
    "record_extraction",
    "record_off_vocabulary",
]


def _metrics():
    from ..metrics import get_metrics

    return get_metrics()


def record_scorecard(scorecard: Any, *, ontology: str, profile: str = "default") -> None:
    """Emit the overall grade, each dimension, and every weak point.

    Weak points carry `severity` so an alert can fire on `major` without
    drowning in the `minor` findings that every young ontology has.
    """
    metrics = _metrics()
    data: Dict[str, Any] = (
        scorecard.to_dict() if hasattr(scorecard, "to_dict") else dict(scorecard or {})
    )

    score = data.get("score")
    if isinstance(score, (int, float)):
        metrics.set("seocho.ontology.scorecard.score",
                    float(score), {"ontology": ontology, "profile": profile})

    for dimension in data.get("dimensions") or []:
        name = str(dimension.get("name") or "")
        value = dimension.get("score")
        if name and isinstance(value, (int, float)):
            metrics.set("seocho.ontology.scorecard.dimension",
                        float(value), {"ontology": ontology, "dimension": name})

    for weak in data.get("weak_points") or []:
        metrics.add("seocho.ontology.weak_point.count", 1, {
            "ontology": ontology,
            "dimension": str(weak.get("dimension") or "unknown"),
            "severity": str(weak.get("severity") or "unknown"),
        })


def record_contract_gaps(ontology_obj: Any, *, ontology: str) -> None:
    """Count the OS-contract elements this ontology does not declare (ADR-0181).

    Distinct from the scorecard: these are the things an ontology FILE cannot
    carry, so their absence is expected on first import and is a to-do rather
    than a defect. Counting them is what turns "you should add competency
    questions" from advice nobody reads into a number that can be tracked to
    zero.
    """
    metrics = _metrics()
    annotations = getattr(ontology_obj, "annotations", None) or {}
    nodes = getattr(ontology_obj, "nodes", None) or {}

    missing = []
    if not (getattr(ontology_obj, "description", "") or "").strip():
        missing.append("purpose")
    if not annotations.get("competency_questions"):
        missing.append("competency_questions")
    if not annotations.get("modelling_decisions"):
        missing.append("modelling_decisions")
    if not any(getattr(nd, "identity_keys", None) for nd in nodes.values()):
        missing.append("identity")
    if not annotations.get("vocabularies"):
        missing.append("vocabularies")

    for element in missing:
        metrics.add("seocho.ontology.contract.missing", 1,
                    {"ontology": ontology, "element": element})


def record_extraction(
    *,
    ontology: str,
    source_type: str,
    nodes: Sequence[Mapping[str, Any]],
    relationships: Sequence[Mapping[str, Any]],
    allowed_labels: Optional[Iterable[str]] = None,
    retries: int = 0,
    retry_reason: str = "",
) -> None:
    """Emit per-document extraction volume and the two silent-failure signals.

    `empty` and `off_ontology_label` are separated on purpose. An empty document
    is a transport or parsing failure — on the measured run, a reasoning model
    returning prose where JSON belonged. An off-ontology label is the opposite:
    extraction succeeded and produced something the schema does not admit, which
    is how the prompt's own `EntityType` example ended up stored as data.
    """
    metrics = _metrics()
    attributes = {"ontology": ontology, "source_type": source_type}

    metrics.record("seocho.index.extraction.nodes", len(nodes), attributes)
    metrics.record("seocho.index.extraction.relationships", len(relationships), attributes)

    if not nodes:
        metrics.add("seocho.index.extraction.empty.count", 1, attributes)

    if retries:
        metrics.add("seocho.index.extraction.retry.count", retries, {
            "ontology": ontology,
            "reason": retry_reason or "unknown",
        })

    if allowed_labels is not None:
        allowed = set(allowed_labels)
        for node in nodes:
            label = str(node.get("label") or "")
            if label and label not in allowed:
                metrics.add("seocho.index.off_ontology_label.count", 1,
                            {"ontology": ontology, "label": label})


def record_off_vocabulary(
    *,
    ontology: str,
    nodes: Sequence[Mapping[str, Any]],
    vocabularies: Mapping[str, Sequence[str]],
) -> None:
    """Count property values outside a declared vocabulary.

    The declaration has no home in `P` yet (`seocho-8v5`), so vocabularies
    arrive from the OS contract's sidecar. Keys are `Label.property`.

    Comparison is case-folded because the measured failure was as much a case
    split as an invented vocabulary: `CURRENT` 88 against `current` 8, and
    `SUPERSEDED` 2 against `superseded` 2. A filter that is literal about case
    loses half its rows, so a value that is only wrong in case is still counted.
    """
    if not vocabularies:
        return
    metrics = _metrics()
    folded = {
        key: {str(v).strip().lower() for v in values}
        for key, values in vocabularies.items()
    }
    for node in nodes:
        label = str(node.get("label") or "")
        for prop, value in (node.get("properties") or {}).items():
            key = f"{label}.{prop}"
            allowed = folded.get(key)
            if allowed is None or value in (None, ""):
                continue
            if str(value).strip().lower() not in allowed:
                metrics.add("seocho.index.off_vocabulary_value.count", 1,
                            {"ontology": ontology, "property": key})
