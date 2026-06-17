"""Agent contracts for the multi-provider data-federation scenario (hq-42k).

Pure dataclasses — no I/O — importable by both agents and the runner. The
provenance fields mirror lib/federation.py's record shape verbatim so a
ProviderResponse composes from instances_read rows without re-derivation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

# Provider abstention marker — identical string to the 09 benchmark so the
# number-aware metric and abstain detection stay shared (§20.3).
ABSTAIN_MARK = "not in the provided context"


@dataclass(frozen=True)
class Provenance:
    """Every fact a provider returns is attributable to exactly this tuple."""

    provider_id: str         # e.g. "deepseek" (== federation Instance.dept)
    src_instance: str        # bolt URI of the provider's physical store
    model: str               # MARA model id that indexed the store
    workspace_id: str        # fedcat-<provider>-<case_id> (§6.1 propagation)
    retrieved_node_count: int


@dataclass(frozen=True)
class ProviderFact:
    """A single (metric, period, basis) figure — the bridge to SourceFact."""

    metric_raw: str
    period: str
    basis: str
    value_raw: str           # figure exactly as extracted (survivorship raw)
    eid: str                 # elementId provenance (§8)


@dataclass(frozen=True)
class ProviderResponse:
    """One provider-agent's answer to one sub-query — the federation contract.

    ``abstain=True`` is the §20.2 anti-fabrication signal: the provider lacked
    the data and SAID SO (empty subgraph, or the model emitted ABSTAIN_MARK).
    """

    provider_id: str
    query: str
    case_id: str
    abstain: bool
    context: str                              # serialized subgraph (narrative path)
    facts: Tuple[ProviderFact, ...]           # structured figures (reference path)
    answer: Optional[str]                     # narrative answer, None if retrieve-only
    confidence: float                         # 0.0 on abstain
    provenance: Provenance
    retrieval_ms: float
    answer_ms: float
    error: str = ""                           # recorded, never imputed (§20.2)

    def to_dict(self) -> dict:
        return {
            "provider_id": self.provider_id, "case_id": self.case_id,
            "abstain": self.abstain, "answer": self.answer,
            "confidence": self.confidence, "context_chars": len(self.context),
            "n_facts": len(self.facts), "retrieval_ms": round(self.retrieval_ms, 1),
            "answer_ms": round(self.answer_ms, 1), "error": self.error,
            "provenance": {
                "provider_id": self.provenance.provider_id,
                "src_instance": self.provenance.src_instance,
                "model": self.provenance.model,
                "workspace_id": self.provenance.workspace_id,
                "retrieved_node_count": self.provenance.retrieved_node_count,
            },
            "facts": [{"metric_raw": f.metric_raw, "period": f.period,
                       "basis": f.basis, "value_raw": f.value_raw, "eid": f.eid}
                      for f in self.facts],
        }


@dataclass(frozen=True)
class FederationRequest:
    query: str
    case_id: str
    slice_tag: str
    category: str
    mode: str = "auto"        # "auto" | "narrative" | "reference"


@dataclass(frozen=True)
class FederationResponse:
    query: str
    case_id: str
    route: str                                  # "reference" | "narrative"
    selected_providers: Tuple[str, ...]
    provider_responses: Tuple[ProviderResponse, ...]
    answer: str
    abstain: bool
    survived: Optional[dict]                    # reference path survivorship verdict
    providers_attempted: int
    providers_answered: int
    degraded: bool                              # True if any selected provider failed
    unavailable: Tuple[dict, ...]               # [{provider, reason}] — never silent
    fanout_latency_ms: dict
    answer_ms: float

    def to_dict(self) -> dict:
        return {
            "query": self.query, "case_id": self.case_id, "route": self.route,
            "selected_providers": list(self.selected_providers),
            "answer": self.answer, "abstain": self.abstain,
            "survived": self.survived,
            "providers_attempted": self.providers_attempted,
            "providers_answered": self.providers_answered,
            "degraded": self.degraded, "unavailable": list(self.unavailable),
            "fanout_latency_ms": self.fanout_latency_ms,
            "answer_ms": round(self.answer_ms, 1),
            "provider_responses": [r.to_dict() for r in self.provider_responses],
        }
