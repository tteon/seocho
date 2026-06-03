from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence


def _clean_text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _clean_list(values: Any) -> List[str]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        return []
    out: List[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_text(value)
        key = text.casefold()
        if text and key not in seen:
            out.append(text)
            seen.add(key)
    return out


def _dict_list(values: Any) -> List[Dict[str, Any]]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    return [dict(item) for item in values if isinstance(item, Mapping)]


@dataclass(slots=True)
class EvidenceSwarmScout:
    """One deterministic scout result inside the evidence-swarm envelope."""

    scout_id: str
    status: str
    findings: List[str] = field(default_factory=list)
    confidence: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scout_id": self.scout_id,
            "status": self.status,
            "findings": list(self.findings),
            "confidence": round(float(self.confidence or 0.0), 4),
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class EvidenceSwarmReport:
    """Typed query-side swarm contract merged into evidence_bundle.v2."""

    enabled: bool
    hardness: str
    hardness_reasons: List[str] = field(default_factory=list)
    scouts: List[EvidenceSwarmScout] = field(default_factory=list)
    critical_path: List[str] = field(default_factory=list)
    recommended_next_step: str = "direct_answer"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": "evidence_swarm.v1",
            "enabled": self.enabled,
            "hardness": self.hardness,
            "hardness_reasons": list(self.hardness_reasons),
            "scouts": [scout.to_dict() for scout in self.scouts],
            "critical_path": list(self.critical_path),
            "recommended_next_step": self.recommended_next_step,
        }


def build_evidence_swarm_report(
    *,
    question: str,
    semantic_context: Mapping[str, Any],
    evidence_bundle: Mapping[str, Any],
    support_assessment: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build a deterministic evidence-swarm report for hard query inspection.

    This is the contract-first slice of the swarm design. It does not spawn
    runtime agents yet; it gives indexing/query evaluators the typed fields that
    a later parallel executor can fill without changing the evidence envelope.
    """

    support = dict(support_assessment or evidence_bundle.get("support_assessment", {}) or {})
    hardness, hardness_reasons = _classify_hardness(
        question=question,
        semantic_context=semantic_context,
        evidence_bundle=evidence_bundle,
        support_assessment=support,
    )
    enabled = hardness in {"medium", "hard"}
    scouts = [
        _ontology_signal_scout(semantic_context, evidence_bundle),
        _required_slot_scout(evidence_bundle, support),
        _relation_path_scout(evidence_bundle),
        _provenance_scout(evidence_bundle),
        _insufficiency_scout(evidence_bundle, support),
    ]
    critical_path = [
        scout.scout_id
        for scout in scouts
        if scout.status in {"missing", "partial", "blocked"}
    ]
    if not enabled:
        next_step = "direct_answer"
    elif any(scout.scout_id == "insufficiency_scout" and scout.status in {"missing", "blocked"} for scout in scouts):
        next_step = "abstain_or_expand_evidence"
    elif any(scout.scout_id == "relation_path_scout" and scout.status in {"missing", "partial"} for scout in scouts):
        next_step = "parallel_relation_path_search"
    else:
        next_step = "slot_bundle_then_synthesis"

    return EvidenceSwarmReport(
        enabled=enabled,
        hardness=hardness,
        hardness_reasons=hardness_reasons,
        scouts=scouts,
        critical_path=critical_path,
        recommended_next_step=next_step,
    ).to_dict()


def _classify_hardness(
    *,
    question: str,
    semantic_context: Mapping[str, Any],
    evidence_bundle: Mapping[str, Any],
    support_assessment: Mapping[str, Any],
) -> tuple[str, List[str]]:
    reasons: List[str] = []
    score = 0
    route_profile = evidence_bundle.get("route_profile", {})
    route_class = _clean_text(route_profile.get("route_class") if isinstance(route_profile, Mapping) else "")
    determinism = _clean_text(
        route_profile.get("question_determinism") if isinstance(route_profile, Mapping) else ""
    )
    missing_slots = _clean_list(evidence_bundle.get("missing_slots", []))
    unresolved_entities = _clean_list(semantic_context.get("unresolved_entities", []))
    query_diagnostics = _dict_list(semantic_context.get("query_diagnostics", []))
    graph_ids = _clean_list(evidence_bundle.get("graph_ids", []))
    support_status = _clean_text(
        support_assessment.get("status") or evidence_bundle.get("support_status")
    )
    normalized_question = question.casefold()

    if route_class in {"R4_GRAPH_JOIN", "R5_LONG_CONTEXT_REASONING"}:
        score += 2
        reasons.append(f"route_class={route_class}")
    if determinism == "hybrid":
        score += 1
        reasons.append("hybrid determinism")
    if missing_slots:
        score += min(3, len(missing_slots))
        reasons.append(f"missing_slots={','.join(missing_slots)}")
    if unresolved_entities:
        score += 2
        reasons.append(f"unresolved_entities={','.join(unresolved_entities)}")
    if query_diagnostics:
        score += 2
        reasons.append("query diagnostics present")
    if len(graph_ids) > 1:
        score += 1
        reasons.append("cross-graph evidence")
    if support_status in {"partial", "unsupported"}:
        score += 2
        reasons.append(f"support_status={support_status}")
    if any(term in normalized_question for term in ("compare", "across", "multi-hop", "why", "tradeoff", "rank")):
        score += 1
        reasons.append("complex question cue")

    if score >= 5:
        return "hard", reasons or ["hardness score threshold"]
    if score >= 2:
        return "medium", reasons or ["medium hardness score"]
    return "easy", reasons or ["direct evidence path"]


def _ontology_signal_scout(
    semantic_context: Mapping[str, Any],
    evidence_bundle: Mapping[str, Any],
) -> EvidenceSwarmScout:
    aliases = semantic_context.get("alias_resolved", {})
    vocabulary = semantic_context.get("vocabulary_resolved", {})
    label_hints = _clean_list(semantic_context.get("label_hints", []))
    route_profile = evidence_bundle.get("route_profile", {})
    route_class = _clean_text(route_profile.get("route_class") if isinstance(route_profile, Mapping) else "")
    findings: List[str] = []
    if isinstance(aliases, Mapping) and aliases:
        findings.append(f"aliases={len(aliases)}")
    if isinstance(vocabulary, Mapping) and vocabulary:
        findings.append(f"vocabulary_resolutions={len(vocabulary)}")
    if label_hints:
        findings.append(f"label_hints={','.join(label_hints[:5])}")
    if route_class:
        findings.append(f"route_class={route_class}")
    status = "complete" if findings else "empty"
    return EvidenceSwarmScout(
        scout_id="ontology_signal_scout",
        status=status,
        findings=findings,
        confidence=0.75 if findings else 0.25,
        metadata={"route_class": route_class},
    )


def _required_slot_scout(
    evidence_bundle: Mapping[str, Any],
    support_assessment: Mapping[str, Any],
) -> EvidenceSwarmScout:
    focus_slots = _clean_list(evidence_bundle.get("focus_slots", []))
    grounded_slots = _clean_list(evidence_bundle.get("grounded_slots", []))
    missing_slots = _clean_list(evidence_bundle.get("missing_slots", []))
    coverage = float(evidence_bundle.get("coverage", 0.0) or 0.0)
    status = "complete"
    if missing_slots and grounded_slots:
        status = "partial"
    elif missing_slots:
        status = "missing"
    findings = [
        f"grounded={','.join(grounded_slots) or 'none'}",
        f"missing={','.join(missing_slots) or 'none'}",
    ]
    return EvidenceSwarmScout(
        scout_id="required_slot_scout",
        status=status,
        findings=findings,
        confidence=coverage,
        metadata={
            "focus_slots": focus_slots,
            "coverage": coverage,
            "support_reason": _clean_text(support_assessment.get("reason")),
        },
    )


def _relation_path_scout(evidence_bundle: Mapping[str, Any]) -> EvidenceSwarmScout:
    required_relations = _clean_list(evidence_bundle.get("required_relations", []))
    triples = _dict_list(evidence_bundle.get("selected_triples", []))
    relations = _clean_list([triple.get("relation") for triple in triples])
    route_profile = evidence_bundle.get("route_profile", {})
    route_class = _clean_text(route_profile.get("route_class") if isinstance(route_profile, Mapping) else "")
    needs_graph_join = bool(required_relations) or route_class == "R4_GRAPH_JOIN"
    if relations:
        status = "complete"
    elif needs_graph_join:
        status = "missing"
    else:
        status = "empty"
    findings = [
        f"required={','.join(required_relations) or 'none'}",
        f"observed={','.join(relations) or 'none'}",
    ]
    confidence = 0.85 if relations else 0.15 if needs_graph_join else 0.5
    return EvidenceSwarmScout(
        scout_id="relation_path_scout",
        status=status,
        findings=findings,
        confidence=confidence,
        metadata={"selected_triple_count": len(triples)},
    )


def _provenance_scout(evidence_bundle: Mapping[str, Any]) -> EvidenceSwarmScout:
    provenance = _dict_list(evidence_bundle.get("provenance", []))
    selected_triples = _dict_list(evidence_bundle.get("selected_triples", []))
    databases = _clean_list(evidence_bundle.get("databases", []))
    status = "complete" if provenance else "partial" if selected_triples else "missing"
    findings = [
        f"provenance_items={len(provenance)}",
        f"databases={','.join(databases) or _clean_text(evidence_bundle.get('database')) or 'none'}",
    ]
    return EvidenceSwarmScout(
        scout_id="provenance_scout",
        status=status,
        findings=findings,
        confidence=0.9 if provenance else 0.45 if selected_triples else 0.1,
        metadata={"graph_id": _clean_text(evidence_bundle.get("graph_id"))},
    )


def _insufficiency_scout(
    evidence_bundle: Mapping[str, Any],
    support_assessment: Mapping[str, Any],
) -> EvidenceSwarmScout:
    support_status = _clean_text(
        support_assessment.get("status") or evidence_bundle.get("support_status")
    )
    missing_slots = _clean_list(
        support_assessment.get("missing_slots") or evidence_bundle.get("missing_slots", [])
    )
    reason = _clean_text(support_assessment.get("reason") or evidence_bundle.get("support_reason"))
    if bool(support_assessment.get("supported")) or support_status in {"supported", "derived_supported"}:
        status = "complete"
    elif support_status == "partial":
        status = "partial"
    elif support_status:
        status = "blocked"
    else:
        status = "missing"
    findings = [
        f"support_status={support_status or 'unknown'}",
        f"missing_slots={','.join(missing_slots) or 'none'}",
    ]
    if reason:
        findings.append(f"reason={reason}")
    confidence = 0.9 if support_status == "supported" else 0.55 if support_status == "partial" else 0.2
    return EvidenceSwarmScout(
        scout_id="insufficiency_scout",
        status=status,
        findings=findings,
        confidence=confidence,
        metadata={"support_reason": reason},
    )
