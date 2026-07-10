"""Typed, measurable query workloads for agent-memory evaluation.

Workloads describe what evidence a customer question requires before prompt
assembly or graph access.  They are deliberately provider- and database-free
so routing, tracing, and evaluation can share the same contract.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping, Tuple


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class PromptIdentity:
    """Stable identity for a versioned prompt template."""

    name: str
    version: str
    template: str

    @property
    def template_hash(self) -> str:
        return _digest(self.template)

    def telemetry_attributes(self) -> dict[str, str]:
        return {
            "seocho.prompt.name": self.name,
            "seocho.prompt.version": self.version,
            "seocho.prompt.template_hash": self.template_hash,
        }


@dataclass(frozen=True, slots=True)
class EvidenceRequirement:
    """One answer slot and the graph/API sources allowed to fill it."""

    slot: str
    sources: Tuple[str, ...]
    relation_path: Tuple[str, ...] = ()
    required: bool = True


@dataclass(frozen=True, slots=True)
class QuerySafetyPolicy:
    """Deterministic limits applied before any LLM or graph query runs."""

    max_graph_hops: int
    allowed_tools: Tuple[str, ...]
    forbidden_actions: Tuple[str, ...]
    require_workspace_scope: bool = True
    fail_closed_on_missing_required_evidence: bool = True


@dataclass(frozen=True, slots=True)
class QueryFamilySpec:
    """Stable contract for one recurring customer-query family."""

    intent_id: str
    description: str
    trigger_phrases: Tuple[str, ...]
    required_relations: Tuple[str, ...]
    required_entity_types: Tuple[str, ...]
    evidence: Tuple[EvidenceRequirement, ...]
    prompt: PromptIdentity
    safety: QuerySafetyPolicy

    @property
    def required_slots(self) -> Tuple[str, ...]:
        return tuple(item.slot for item in self.evidence if item.required)

    def missing_slots(self, supplied: Mapping[str, Any]) -> Tuple[str, ...]:
        return tuple(
            slot
            for slot in self.required_slots
            if supplied.get(slot) is None or supplied.get(slot) == ""
        )

    def telemetry_attributes(
        self,
        *,
        workspace_id: str,
        supplied: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Return bounded, privacy-safe attributes; never return raw values."""

        missing = self.missing_slots(supplied)
        return {
            "seocho.query.family": self.intent_id,
            "seocho.workspace_hash": _digest(workspace_id),
            "seocho.query.required_slot_count": len(self.required_slots),
            "seocho.query.supplied_slot_count": len(self.required_slots) - len(missing),
            "seocho.query.missing_slots": list(missing),
            "seocho.graph.hop_limit": self.safety.max_graph_hops,
            **self.prompt.telemetry_attributes(),
        }


WITHDRAWAL_EXPLANATION_PROMPT = """You explain an existing withdrawal state from supplied evidence.
Use only evidence slots marked as filled and cite their provenance identifiers.
Name every required slot that is missing. Do not infer customer identity, wallet
ownership, risk status, or policy applicability. Never authorize, submit,
cancel, retry, or promise completion of a withdrawal. If required evidence is
missing or contradictory, return require_review with the insufficiency."""


WITHDRAWAL_EXPLANATION = QueryFamilySpec(
    intent_id="withdrawal_explanation.v1",
    description="Explain why a crypto withdrawal is pending or blocked.",
    trigger_phrases=(
        "why is my withdrawal pending",
        "why can't i withdraw",
        "why cant i withdraw",
        "withdrawal blocked",
        "withdrawal failed",
        "출금이 왜",
        "출금할 수 없",
        "출금 실패",
    ),
    required_relations=(
        "INITIATED",
        "USES_ASSET",
        "USES_NETWORK",
        "HAS_STATUS",
        "BLOCKED_BY",
        "SUBJECT_TO",
        "SUPPORTED_BY",
    ),
    required_entity_types=(
        "Withdrawal",
        "Asset",
        "Network",
        "AccountState",
        "Restriction",
        "Policy",
        "EvidenceSource",
    ),
    evidence=(
        EvidenceRequirement("withdrawal_state", ("withdrawal_api",)),
        EvidenceRequirement("account_state", ("account_api",)),
        EvidenceRequirement("network_state", ("network_status_api",)),
        EvidenceRequirement(
            "restriction_state",
            ("risk_policy_api", "compliance_api"),
            ("Withdrawal", "BLOCKED_BY", "Restriction"),
        ),
        EvidenceRequirement(
            "applicable_policy",
            ("approved_policy_artifact",),
            ("Withdrawal", "SUBJECT_TO", "Policy"),
        ),
        EvidenceRequirement(
            "destination_compatibility",
            ("asset_network_registry",),
            ("Withdrawal", "USES_NETWORK", "Network"),
        ),
        EvidenceRequirement("customer_message", ("support_channel",), required=False),
    ),
    prompt=PromptIdentity(
        name="okx.withdrawal_explanation",
        version="1.0.0",
        template=WITHDRAWAL_EXPLANATION_PROMPT,
    ),
    safety=QuerySafetyPolicy(
        max_graph_hops=4,
        allowed_tools=(
            "withdrawal_status_read",
            "account_state_read",
            "network_status_read",
            "risk_policy_read",
            "graph_read",
        ),
        forbidden_actions=(
            "authorize_withdrawal",
            "submit_withdrawal",
            "cancel_withdrawal",
            "retry_withdrawal",
            "trade",
        ),
    ),
)


OKX_QUERY_FAMILIES: Tuple[QueryFamilySpec, ...] = (WITHDRAWAL_EXPLANATION,)


def classify_okx_query(question: str) -> QueryFamilySpec | None:
    """Return the first deterministic OKX workload match, if any."""

    normalized = " ".join(str(question).lower().split())
    for family in OKX_QUERY_FAMILIES:
        if any(trigger in normalized for trigger in family.trigger_phrases):
            return family
    return None

