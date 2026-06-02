from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Literal, Tuple


@dataclass(frozen=True, slots=True)
class PayloadContract:
    """Small helper for serializing nested Graph-CoT contract payloads."""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class GraphCoTQuestionFrame(PayloadContract):
    """Semantic-layer handoff payload for Graph-CoT query mode."""

    question: str
    workspace_id: str
    databases: Tuple[str, ...]
    intent_id: str = ""
    query_mode: Literal["graph_cot"] = "graph_cot"
    entity_candidates: Tuple[str, ...] = field(default_factory=tuple)
    unresolved_entities: Tuple[str, ...] = field(default_factory=tuple)
    support_status: str = ""
    support_reason: str = ""
    ontology_context_mismatch: Dict[str, Any] = field(default_factory=dict)
    semantic_context: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SupervisorDirective(PayloadContract):
    """Planner-side contract emitted by QuerySupervisorAgent."""

    objective: str
    route: Literal["lpg", "rdf", "hybrid", "abstain"] = "lpg"
    answer_style: Literal["concise", "evidence", "partial"] = "evidence"
    must_ground_slots: Tuple[str, ...] = field(default_factory=tuple)
    must_not_infer: Tuple[str, ...] = field(default_factory=tuple)
    max_repair_attempts: int = 1
    require_guardrail: bool = True


@dataclass(frozen=True, slots=True)
class QueryEvidencePacket(PayloadContract):
    """Structured retrieval evidence returned by Text2CypherAgent."""

    database: str
    cypher: str
    params: Dict[str, Any] = field(default_factory=dict)
    records: Tuple[Dict[str, Any], ...] = field(default_factory=tuple)
    selected_triples: Tuple[Dict[str, Any], ...] = field(default_factory=tuple)
    slot_fills: Dict[str, Any] = field(default_factory=dict)
    grounded_slots: Tuple[str, ...] = field(default_factory=tuple)
    missing_slots: Tuple[str, ...] = field(default_factory=tuple)
    support_status: str = ""
    support_reason: str = ""
    ontology_context_mismatch: Dict[str, Any] = field(default_factory=dict)
    query_diagnostics: Tuple[Dict[str, Any], ...] = field(default_factory=tuple)
    repair_trace: Tuple[Dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def has_grounded_support(self) -> bool:
        return bool(
            self.records
            or self.selected_triples
            or self.slot_fills
            or self.grounded_slots
        ) and self.support_status in {"supported", "partial"}


@dataclass(frozen=True, slots=True)
class AnswerDraft(PayloadContract):
    """Answer-only payload synthesized from a QueryEvidencePacket."""

    answer_text: str
    cited_facts: Tuple[str, ...] = field(default_factory=tuple)
    grounded_slots: Tuple[str, ...] = field(default_factory=tuple)
    missing_slots: Tuple[str, ...] = field(default_factory=tuple)
    unresolved_entities: Tuple[str, ...] = field(default_factory=tuple)
    abstain: bool = False
    confidence_note: str = ""

    @property
    def is_partial(self) -> bool:
        return bool(self.missing_slots) and not self.abstain


@dataclass(frozen=True, slots=True)
class GuardrailFinding(PayloadContract):
    """One ontology/evidence review finding for the answer guardrail."""

    code: str
    severity: Literal["hard", "soft"]
    message: str
    evidence_ref: str = ""
    repair_hint: str = ""


@dataclass(frozen=True, slots=True)
class GuardrailVerdict(PayloadContract):
    """Structured answer review produced by AnswerGuardrailAgent."""

    decision: Literal["pass", "revise", "refuse"]
    summary: str
    supported_claims: Tuple[str, ...] = field(default_factory=tuple)
    unsupported_claims: Tuple[str, ...] = field(default_factory=tuple)
    hard_findings: Tuple[GuardrailFinding, ...] = field(default_factory=tuple)
    soft_findings: Tuple[GuardrailFinding, ...] = field(default_factory=tuple)
    required_repairs: Tuple[str, ...] = field(default_factory=tuple)
    ontology_consistent: bool = True
    suspicious: bool = False

    @property
    def allows_answer(self) -> bool:
        return self.decision == "pass"


@dataclass(frozen=True, slots=True)
class GraphCoTFinalAnswer(PayloadContract):
    """Final supervisor envelope for the Graph-CoT lane."""

    answer_text: str
    status: Literal["answered", "partial", "abstained"]
    draft: AnswerDraft
    verdict: GuardrailVerdict
    evidence: QueryEvidencePacket


__all__ = [
    "AnswerDraft",
    "GraphCoTFinalAnswer",
    "GraphCoTQuestionFrame",
    "GuardrailFinding",
    "GuardrailVerdict",
    "PayloadContract",
    "QueryEvidencePacket",
    "SupervisorDirective",
]
