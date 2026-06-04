"""Data-grounded retrieval-lane policy (F3, middleware perf).

Maps a `route_profile` (route_class + question_determinism, computed in
`intent._build_route_profile`) to a recommended retrieval lane and whether to
escalate to the expensive evidence-bundle/synthesis path. Versioned so a routing
change is attributable across runs (§20.7 reproducibility).

GROUNDING — this policy is built on MEASURED results, not the original
graph-favoring hypothesis. Across THREE datasets (FinDER financial, BC3 email,
AMI meetings) the relationship was consistently **vector ≈ hybrid ≫ graph**:
pure graph-as-context never beat vector (BC3 paired p=0.0; AMI tie), and hybrid
only tied vector. Therefore:

  - We NEVER recommend pure `graph` as the primary lane (it is dominated by
    vector on every measured slice). Relational/reasoning queries route to
    `hybrid` (which includes the vector content the graph alone drops).
  - The real cost lever is AVOIDING the expensive synthesis path for simple
    deterministic single-lookups (S6-style), where cheap vector retrieval
    sufficed at no measured quality loss — provider-agnostic token savings.

If a future extraction-recall improvement makes a graph lane competitive
(the recall-gate finding), bump the policy version and re-measure before
changing these mappings.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Tuple

ROUTE_POLICY_VERSION = "route_policy@v1"


@dataclass(frozen=True)
class LanePolicy:
    """Recommended retrieval lane + cost-tier for a query.

    retrieval: "vector" | "hybrid" — never pure "graph" (measured ≤ vector).
    escalate_synthesis: run the expensive evidence-bundle/multi-step synthesis
        path (True) vs the cheap lookup-first path (False).
    """

    retrieval: str
    escalate_synthesis: bool
    policy_version: str
    rationale: Tuple[str, ...]


def recommend_lane(
    route_class: str,
    question_determinism: str,
    *,
    source_types: Sequence[str] = (),
) -> LanePolicy:
    """Pick the cheapest sufficient lane for a query, grounded in measured data."""
    rc = str(route_class or "").strip() or "R1_LOOKUP"
    det = str(question_determinism or "").strip() or "hybrid"

    relational = rc in {"R4_GRAPH_JOIN", "R5_LONG_CONTEXT_REASONING"}

    if relational:
        # Relational/reasoning: include graph context, but as HYBRID (vector +
        # graph) — pure graph is dominated by vector in all measured datasets.
        retrieval = "hybrid"
        escalate = True
        rationale = (
            f"route_class={rc}: relational/reasoning",
            "hybrid (vector+graph) not pure-graph — graph alone measured <= vector",
            "escalate: multi-step synthesis warranted",
        )
    elif det == "deterministic":
        # Simple single-fact lookup (S6-style): cheap vector, skip the expensive
        # synthesis path — measured no quality loss, material token savings.
        retrieval = "vector"
        escalate = False
        rationale = (
            f"route_class={rc}, determinism=deterministic: single-lookup",
            "vector-only, skip expensive synthesis (cost lever, no measured quality loss)",
        )
    else:
        # Lookup but uncertain (hybrid/non-deterministic determinism): cheap
        # vector retrieval, but verify via synthesis.
        retrieval = "vector"
        escalate = True
        rationale = (
            f"route_class={rc}, determinism={det}: lookup but uncertain",
            "vector retrieval, escalate to verify-then-synthesize",
        )

    return LanePolicy(
        retrieval=retrieval,
        escalate_synthesis=escalate,
        policy_version=ROUTE_POLICY_VERSION,
        rationale=rationale,
    )
