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
from typing import Optional, Sequence, Tuple

ROUTE_POLICY_VERSION = "route_policy@v1"
ANSWERABILITY_VERSION = "answerability@v1"


@dataclass(frozen=True)
class Answerability:
    """Ontology-as-predicate answerability verdict (the Answerability Gate).

    Panel-converged design (systems architect + ontologist): the ontology earns
    its keep as a DETERMINISTIC PREDICATE at the routing boundary — read the
    DECLARED relation set as the SOLE admission authority for the graph LLM-free
    lane, never the materialized graph (the store can hold prompt-smuggled,
    ungoverned edges) and never the unstable LLM judge.

    verdict: "CERTIFIED" — the required answer-relation is declared → the graph
        lane may serve this class deterministically (LLM-free, provenance), subject
        to a per-case serving certificate downstream (does this workspace's graph
        actually hold the grounded declared tuples).
      "PARTIAL" — only a related relation is declared (e.g. wrong endpoint type)
        → hybrid, not LLM-free.
      "UNCOVERED" — no declared relation serves this → route to vector; the graph
        lane is REFUSED (firewall: never serve LLM-free from an undeclared edge).
    """

    verdict: str
    declared_match: Tuple[str, ...]
    rationale: str
    version: str = ANSWERABILITY_VERSION


def answerability_gate(
    required_relations: Sequence[str],
    declared_relations: Sequence[str],
    *,
    partial_relations: Sequence[str] = (),
) -> Answerability:
    """$0, no-LLM, no-graph ontology-coverage predicate.

    required_relations: declared relation name(s) that would FULLY serve the
        query's answer (derived from the question's required (subj, role, obj)).
    declared_relations: the DECLARED relation set of the active composed ontology.
    partial_relations: related relations that only partially serve (e.g. correct
        relation but wrong endpoint type) → PARTIAL.

    Reads the DECLARED schema only. Validated $0: COVERED classes carry the
    graph's non-zero stage-local tuple-F1 (E3); UNCOVERED classes are non-viable
    (E4 — answers came from ungoverned prompt-smuggled edges). See
    examples/contextgraph/answerability_gate.py.
    """
    req = {str(r).strip() for r in required_relations if str(r).strip()}
    dec = {str(r).strip() for r in declared_relations if str(r).strip()}
    par = {str(r).strip() for r in partial_relations if str(r).strip()}
    full_hit = sorted(req & dec)
    if full_hit:
        return Answerability("CERTIFIED", tuple(full_hit),
                             "required relation declared → graph LLM-free serving admissible "
                             "(subject to per-case serving certificate)")
    part_hit = sorted(par & dec)
    if part_hit:
        return Answerability("PARTIAL", tuple(part_hit),
                             "only a related relation declared (wrong endpoint/scope) → hybrid, not LLM-free")
    return Answerability("UNCOVERED", (),
                         "no declared relation serves this class → route to vector; "
                         "refuse graph LLM-free lane (firewall — never serve from an undeclared edge)")


@dataclass(frozen=True)
class LanePolicy:
    """Recommended retrieval lane + cost-tier for a query.

    retrieval: "vector" | "hybrid" | "graph_certified".
        "graph_certified" (LLM-free deterministic graph serving) is recommended
        ONLY when the Answerability Gate returns CERTIFIED — i.e. the ontology
        DECLARES the required relation. Otherwise never pure graph (measured ≤
        vector on prose).
    escalate_synthesis: run the expensive evidence-bundle/multi-step synthesis
        path (True) vs the cheap lookup-first path (False).
    answerability: the gate verdict when ontology relations were supplied (else None).
    """

    retrieval: str
    escalate_synthesis: bool
    policy_version: str
    rationale: Tuple[str, ...]
    answerability: Optional[Answerability] = None


def recommend_lane(
    route_class: str,
    question_determinism: str,
    *,
    source_types: Sequence[str] = (),
    required_relations: Sequence[str] = (),
    declared_relations: Sequence[str] = (),
    partial_relations: Sequence[str] = (),
) -> LanePolicy:
    """Pick the cheapest sufficient lane for a query, grounded in measured data.

    Answerability Gate (opt-in): when BOTH required_relations and
    declared_relations are supplied, the gate runs as a deterministic predicate
    over the DECLARED ontology. CERTIFIED upgrades a deterministic lookup to the
    LLM-free `graph_certified` lane; UNCOVERED enforces the firewall (never the
    graph lane — refuse to serve from an undeclared edge). With no relations
    supplied the gate is OFF and behavior is identical to route_policy@v1.
    """
    rc = str(route_class or "").strip() or "R1_LOOKUP"
    det = str(question_determinism or "").strip() or "hybrid"

    gate: Optional[Answerability] = None
    if required_relations and declared_relations:
        gate = answerability_gate(required_relations, declared_relations,
                                  partial_relations=partial_relations)

    # CERTIFIED + a deterministic single-lookup → graph LLM-free deterministic lane
    # (the only place a graph lane is recommended; subject to the per-case serving
    # certificate in the answerer). Reasoning/relational classes stay hybrid.
    if gate is not None and gate.verdict == "CERTIFIED" and det == "deterministic" \
            and rc not in {"R5_LONG_CONTEXT_REASONING"}:
        return LanePolicy(
            retrieval="graph_certified",
            escalate_synthesis=False,
            policy_version=ROUTE_POLICY_VERSION,
            rationale=(
                f"route_class={rc}, determinism=deterministic",
                f"answerability=CERTIFIED via {list(gate.declared_match)} — graph LLM-free lane",
                "subject to per-case serving certificate (declared grounded tuples present)",
            ),
            answerability=gate,
        )

    relational = rc in {"R4_GRAPH_JOIN", "R5_LONG_CONTEXT_REASONING"}
    # firewall: an UNCOVERED relational query must NOT pull graph context it can't
    # serve from a governed edge — drop to vector (hybrid's graph half is refused).
    if gate is not None and gate.verdict == "UNCOVERED" and relational:
        return LanePolicy(
            retrieval="vector",
            escalate_synthesis=True,
            policy_version=ROUTE_POLICY_VERSION,
            rationale=(
                f"route_class={rc}: relational but answerability=UNCOVERED",
                "firewall: ontology declares no relation for this class → vector, "
                "refuse graph lane (no governed edge to serve/cite)",
            ),
            answerability=gate,
        )

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
        answerability=gate,
    )
