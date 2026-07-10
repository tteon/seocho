"""Deterministic risk preflight and ontology-aligned disclosure filtering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Tuple

from ..tracing import log_span


_SEVERITY = {"low": 1, "medium": 2, "high": 3, "critical": 4}
_CLASSIFICATION = {"public": 0, "internal": 1, "restricted": 2, "secret": 3}


@dataclass(frozen=True, slots=True)
class RiskSignalEvidence:
    reason_code: str
    severity: str
    graph_hops: int
    provenance_id: str
    observed_at: str = ""

    def __post_init__(self) -> None:
        if self.severity not in _SEVERITY:
            raise ValueError(f"unsupported severity: {self.severity}")
        if self.graph_hops < 0:
            raise ValueError("graph_hops must be non-negative")
        if not self.provenance_id.strip():
            raise ValueError("provenance_id is required")


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    policy_id: str
    version: str
    max_graph_hops: int = 4
    critical_block_hops: int = 2
    high_review_hops: int = 4
    repeated_flagged_counterparty_threshold: int = 3


@dataclass(frozen=True, slots=True)
class RiskPreflightResult:
    disposition: str
    reason_codes: Tuple[str, ...]
    policy_id: str
    policy_version: str
    evaluated_signal_count: int
    max_observed_hops: int
    projection_current: bool
    authorizes_transaction: bool = False


def evaluate_risk_preflight(
    *,
    signals: Tuple[RiskSignalEvidence, ...],
    repeated_flagged_counterparties: int,
    policy: RiskPolicy,
    projection_current: bool,
) -> RiskPreflightResult:
    """Evaluate signals without an LLM and without authorizing a transaction."""

    considered = tuple(signal for signal in signals if signal.graph_hops <= policy.max_graph_hops)
    reasons: list[str] = []
    if not projection_current:
        disposition = "review_required"
        reasons.append("projection_not_current")
    elif any(
        signal.severity == "critical"
        and signal.graph_hops <= policy.critical_block_hops
        for signal in considered
    ):
        disposition = "policy_block"
        reasons.append("critical_wallet_proximity")
    elif any(
        _SEVERITY[signal.severity] >= _SEVERITY["high"]
        and signal.graph_hops <= policy.high_review_hops
        for signal in considered
    ):
        disposition = "review_required"
        reasons.append("high_risk_wallet_proximity")
    elif repeated_flagged_counterparties >= policy.repeated_flagged_counterparty_threshold:
        disposition = "review_required"
        reasons.append("repeated_flagged_counterparties")
    else:
        disposition = "continue_policy_evaluation"
        reasons.append("no_blocking_signal_in_bounded_graph")

    result = RiskPreflightResult(
        disposition=disposition,
        reason_codes=tuple(reasons),
        policy_id=policy.policy_id,
        policy_version=policy.version,
        evaluated_signal_count=len(considered),
        max_observed_hops=max((signal.graph_hops for signal in considered), default=0),
        projection_current=projection_current,
    )
    log_span(
        "risk.preflight",
        output_data={
            "seocho.risk.disposition": result.disposition,
            "seocho.risk.reason_codes": list(result.reason_codes),
            "seocho.risk.signal_count": result.evaluated_signal_count,
            "seocho.risk.max_observed_hops": result.max_observed_hops,
            "seocho.risk.projection_current": result.projection_current,
            "seocho.risk.authorizes_transaction": False,
        },
        metadata={
            "seocho.risk.policy_id": policy.policy_id,
            "seocho.risk.policy_version": policy.version,
        },
        tags=["risk", "preflight"],
    )
    return result


@dataclass(frozen=True, slots=True)
class DisclosureResult:
    visible: Mapping[str, Any]
    redacted_fields: Tuple[str, ...]
    policy_id: str
    policy_version: str


@dataclass(frozen=True, slots=True)
class SubjectDisclosureBinding:
    """Per-subject policy binding stored in durable memory, never etcd."""

    subject_ref_hash: str
    role: str
    policy_id: str
    policy_version: str
    denied_fields: Tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OntologyDisclosurePolicy:
    """Compiled ontology property classifications enforced before prompting."""

    policy_id: str
    version: str
    property_classification: Mapping[str, str]
    role_clearance: Mapping[str, str]

    def __post_init__(self) -> None:
        invalid_classes = set(self.property_classification.values()) - set(_CLASSIFICATION)
        invalid_clearances = set(self.role_clearance.values()) - set(_CLASSIFICATION)
        if invalid_classes or invalid_clearances:
            raise ValueError("unsupported disclosure classification")

    def filter_record(
        self,
        record: Mapping[str, Any],
        *,
        role: str,
        subject_denied_fields: Tuple[str, ...] = (),
    ) -> DisclosureResult:
        clearance_name = self.role_clearance.get(role, "public")
        clearance = _CLASSIFICATION[clearance_name]
        denied = set(subject_denied_fields)
        visible: dict[str, Any] = {}
        redacted: list[str] = []
        for field, value in record.items():
            classification = self.property_classification.get(field, "restricted")
            if field in denied or _CLASSIFICATION[classification] > clearance:
                redacted.append(field)
            else:
                visible[field] = value
        result = DisclosureResult(
            visible=visible,
            redacted_fields=tuple(sorted(redacted)),
            policy_id=self.policy_id,
            policy_version=self.version,
        )
        log_span(
            "guardrail.disclosure",
            output_data={
                "seocho.guardrail.visible_field_count": len(visible),
                "seocho.guardrail.redacted_field_count": len(redacted),
                "seocho.guardrail.redacted_fields": list(result.redacted_fields),
            },
            metadata={
                "seocho.guardrail.policy_id": self.policy_id,
                "seocho.guardrail.policy_version": self.version,
                "seocho.guardrail.role": role,
            },
            tags=["guardrail", "disclosure"],
        )
        return result

    def filter_for_subject(
        self,
        record: Mapping[str, Any],
        *,
        binding: SubjectDisclosureBinding,
    ) -> DisclosureResult:
        if (binding.policy_id, binding.policy_version) != (self.policy_id, self.version):
            raise ValueError("subject disclosure binding does not match active policy")
        return self.filter_record(
            record,
            role=binding.role,
            subject_denied_fields=binding.denied_fields,
        )


def default_disclosure_policy() -> OntologyDisclosurePolicy:
    return OntologyDisclosurePolicy(
        policy_id="okx-risk-disclosure",
        version="1.0.0",
        property_classification={
            "disposition": "public",
            "reason_codes": "public",
            "policy_version": "public",
            "graph_hops": "internal",
            "provenance_id": "internal",
            "wallet_hash": "restricted",
            "customer_id": "restricted",
            "watchlist_source": "restricted",
            "internal_risk_score": "secret",
            "policy_threshold": "secret",
            "raw_wallet_address": "secret",
        },
        role_clearance={
            "customer": "public",
            "support": "internal",
            "compliance": "restricted",
            "system": "secret",
        },
    )
