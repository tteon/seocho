"""RouteProfile — route-conditional tool_policy + planner selection.

Empirically motivated by the opik kdd2026/icml2026 traces:

- kdd `semantic.route_profile` spans map each question to a
  (route_class, tool_policy, planner, determinism) tuple before execution
  (routes R1_LOOKUP … R6_DOC_ONLY_REFERENCE).
- icml `exp5_policy_compare` measured single_call vs planner per bucket:
  the multi-step ``planner`` only beats ``single_call`` on **multi-hop**
  questions (multi-hop +0.048 f1; ambiguous/factual/compliance lose).

The lesson: the planner is route-conditional — escalate to the expensive
multi-step path ONLY for multi-hop/compositional questions; keep simple
lookups on the cheap single-pass template. This module is seocho's port:
classify a route_class, look up its RouteProfile, and map the profile's
planner to the lane's execution levers (reasoning_mode / repair_budget).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Tuple


class ToolPolicy(str, Enum):
    CYPHER_FIRST = "cypher_first"                    # schema→text2cypher→validate→execute
    RETRIEVE_VERIFY_CYPHER = "retrieve_verify_cypher"  # enrich fan-out→text2cypher→verify
    RULE_CONTRACT_FIRST = "rule_contract_first"      # ontology/rule contract gate first


class Planner(str, Enum):
    TEMPLATE = "template"      # single-pass deterministic build (cheap)
    COST_RANKED = "cost_ranked"  # GOPTS K-candidate cost ranking
    MULTI_STEP = "multi_step"    # iterative repair / decomposition (expensive)
    # Validated generation: the model writes the read query, then it is checked
    # against the ontology (declared labels/relationships only, bounded paths,
    # tenant scope, row budget) and EXPLAINed before execution. Reserved for the
    # routes whose shapes the pattern catalog cannot express — see ROUTE_CATALOG.
    VALIDATED_GENERATION = "validated_generation"


@dataclass(frozen=True)
class RouteProfile:
    route_class: str
    tool_policy: ToolPolicy
    planner: Planner
    question_determinism: str            # "deterministic" | "hybrid"
    recommended_tools: Tuple[str, ...] = ()
    rationale: Tuple[str, ...] = ()


# Empirically-seeded catalog. The multi_hop row is the only one that
# escalates to MULTI_STEP — directly from exp5 (planner wins only on
# multi-hop). Everything else stays on the cheap single-pass planner.
ROUTE_CATALOG: Dict[str, RouteProfile] = {
    "lookup": RouteProfile(
        route_class="lookup",
        tool_policy=ToolPolicy.CYPHER_FIRST,
        planner=Planner.TEMPLATE,
        question_determinism="deterministic",
        recommended_tools=("schema_with_stats", "text2cypher", "validate_cypher", "execute_cypher"),
        rationale=("single_call ≥ planner on factual/lookup (exp5)",),
    ),
    "entity_summary": RouteProfile(
        route_class="entity_summary",
        tool_policy=ToolPolicy.RETRIEVE_VERIFY_CYPHER,
        planner=Planner.TEMPLATE,
        question_determinism="deterministic",
        recommended_tools=("schema_with_stats", "text2cypher", "execute_cypher"),
        rationale=("single_call ≥ planner on ambiguous (exp5)",),
    ),
    "graph_join": RouteProfile(
        route_class="graph_join",
        tool_policy=ToolPolicy.RETRIEVE_VERIFY_CYPHER,
        planner=Planner.COST_RANKED,
        question_determinism="hybrid",
        recommended_tools=("schema_with_stats", "text2cypher", "validate_cypher", "execute_cypher"),
        rationale=("R4_GRAPH_JOIN workhorse; GOPTS cost-ranked applies",),
    ),
    "multi_hop": RouteProfile(
        route_class="multi_hop",
        tool_policy=ToolPolicy.RETRIEVE_VERIFY_CYPHER,
        planner=Planner.VALIDATED_GENERATION,
        question_determinism="hybrid",
        recommended_tools=("schema_with_stats", "text2cypher", "validate_cypher", "execute_cypher", "similar_query_search"),
        rationale=(
            "exp5: multi-step planner beats single_call ONLY on multi-hop (+0.048 f1)",
            # FinBench SF1000, same ontology/questions/scoring: the pattern catalog
            # cannot express a variable-length hop, so it answered nhop_neighborhood
            # with rows=2 and laundering_cycle with rows=0 — plausible counts, wrong
            # content. Validated generation answered both, and its plans were cheaper
            # throughout (265 vs 43,235,371 total db hits, sargable 100% vs 88%).
            "finbench sf1000: catalog lacks variable-length hops; generation covers "
            "them at 100% sargable and far fewer db hits",
            # Escalation stays confined to this route: on lookup/entity_summary the
            # catalog already answers, and generation there would add a model round
            # trip plus a rejection risk for no accuracy gain.
            "escalate only here — cheap routes keep the deterministic single pass",
        ),
    ),
}

_DEFAULT_ROUTE = "entity_summary"


# Rule signals for route_class. Compositional / multi-hop wording escalates.
_MULTI_HOP_RE = re.compile(
    # Compositional wording (the original exp5 signals).
    r"\b(compositional|and then|after|both .* and|compared to|versus|"
    r"relative to|as a (?:fraction|percentage|share) of|combined|"
    r"across .* and|sum of|difference between"
    # Traversal-depth wording. These name the shapes the pattern catalog cannot
    # express — a variable-length hop, reachability, or a cycle — so they must
    # escalate rather than be answered by a one-hop template with a plausible row
    # count. Measured on FinBench SF1000: the catalog answered "within 3 transfer
    # hops" with rows=2 and a cycle question with rows=0.
    # Hop counts arrive as digits *or* as words, and a qualifier can sit between the
    # count and the noun — "within two transfer hops downstream" is how the question is
    # actually asked. Matching only `\d+\s*hops?` missed that entirely: measured on the
    # realistic question set, every two-hop question fell through to the one-hop count
    # template and was answered with the anchor's out-degree (gold 69,303, answered 68).
    # That failure is worse than an expensive one because it is *cheaper* — db hits per
    # answer fell to 154 from 38,584, so a cost metric reads it as an improvement.
    r"|(?:\d+|two|three|four|five|several|multiple)\s*[- ]?(?:\w+\s+){0,2}hops?"
    r"|multi[- ]?hop|hops? (?:away|from)"
    r"|reachable|reach(?:es|ed)? .* (?:from|within)|traverse|traversal"
    r"|cycle|loop(?:s)? back|returns? to (?:it|itself|the same)"
    r"|shortest path|path between|connected to .* through"
    r"|indirect(?:ly)?|chain of)\b",
    re.IGNORECASE,
)
_LOOKUP_RE = re.compile(
    r"\bwhere\b|\bwho (?:is|are)\b|\bhow much\b|"
    # "what was [up to 4 words incl. possessives] <metric noun>"
    r"\bwhat (?:was|is|were)\s+(?:[\w'’.&,-]+\s+){0,4}"
    r"(revenue|income|dividend|value|amount|ratio|sales|profit|loss|"
    r"ceo|chair|headquarter|settlement|capital)",
    re.IGNORECASE,
)


def classify_route_class(question: str, *, reasoning_type: str = "") -> str:
    """Pick a route_class for the question.

    ``reasoning_type`` (when a dataset provides it, e.g. FinDER
    'single_hop' / 'compositional' / 'numeric_lookup') takes precedence
    over text rules — it's a curated label. Otherwise fall back to
    keyword rules, then the safe default.
    """
    rt = (reasoning_type or "").strip().lower()
    if rt in ("compositional", "multi_hop", "multi-hop"):
        return "multi_hop"
    if rt in ("single_hop", "numeric_lookup", "lookup"):
        return "lookup"

    q = (question or "").strip()
    if not q:
        return _DEFAULT_ROUTE
    if _MULTI_HOP_RE.search(q):
        return "multi_hop"
    if _LOOKUP_RE.search(q):
        return "lookup"
    return _DEFAULT_ROUTE


def select_route_profile(question: str, *, reasoning_type: str = "") -> RouteProfile:
    return ROUTE_CATALOG[classify_route_class(question, reasoning_type=reasoning_type)]


def planner_exec_params(planner: Planner) -> Dict[str, int]:
    """Map a planner to the lane's execution levers.

    - TEMPLATE: single pass, no repair (cheap; the exp5 'single_call').
    - COST_RANKED: single pass but cost-ranked plan emission (GOPTS).
    - MULTI_STEP: reasoning on with a repair budget (the exp5 'planner',
      reserved for multi-hop).
    - VALIDATED_GENERATION: the model writes the query and validation gates it, so
      one repair is enough — the generator already retries internally once against
      its own violation feedback. Reasoning stays on because these are the
      compositional questions.
    """
    if planner == Planner.VALIDATED_GENERATION:
        return {"reasoning_mode": True, "repair_budget": 1}
    if planner == Planner.MULTI_STEP:
        return {"reasoning_mode": True, "repair_budget": 2}
    if planner == Planner.COST_RANKED:
        return {"reasoning_mode": False, "repair_budget": 1}
    return {"reasoning_mode": False, "repair_budget": 0}


def prefers_validated_generation(question: str, *, reasoning_type: str = "") -> bool:
    """Whether the route's policy calls for validated generation.

    This is the policy-driven counterpart to the global
    ``SEOCHO_QUERY_PRECEDENCE`` switch: the catalog already encodes that planner
    escalation is route-conditional, so which arm builds the query belongs in the
    same table rather than in a process-wide flag.
    """
    return select_route_profile(question, reasoning_type=reasoning_type).planner is Planner.VALIDATED_GENERATION
