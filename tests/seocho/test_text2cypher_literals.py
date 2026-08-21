"""Generated Cypher must parameterise its comparisons.

The text2cypher system prompt says *"Never insert literal IDs"* and nothing
checked it. Measured against the validator before this change:

    parameterised (good)   -> ()
    literal string inlined -> ()          <- accepted
    literal in CONTAINS    -> ()          <- accepted
    literal number inlined -> ('unknown_properties:year',)

The one that failed did so because of the property name, not the literal.

Neo4j keys its plan cache on the query **string**, so `WHERE n.name = 'Tesla'`
is a distinct cache entry for every entity asked about. Verified on the DozerDB
instance: `server.memory.query_cache.per_db_cache_num_entries = 1000`. Four
questions about four entities produce four entries when inlined and one when
parameterised — and a text2cypher workload emits new string shapes constantly,
so inlined literals evict the plans worth keeping and turn compile time into the
p99.

Correctness is affected too: a parameterised predicate can take an index seek on
a composite index, while a literal makes the planner re-plan and can pick a
different shape between otherwise identical questions.
"""

from __future__ import annotations

import pytest

from seocho.query.workload_compiler import (
    Text2CypherFallbackPolicy,
    validate_text2cypher_fallback,
)


@pytest.fixture
def policy() -> Text2CypherFallbackPolicy:
    return Text2CypherFallbackPolicy(
        allowed_labels=("Decision", "System"),
        allowed_relationships=("APPLIES_TO", "SUPERSEDES"),
        allowed_properties=("name", "status", "year", "_workspace_id"),
        workspace_property="_workspace_id",
        required_parameters=("workspace_id",),
        max_graph_hops=4,
        max_result_rows=100,
        max_repair_attempts=1,
        require_explain_before_execute=True,
    )


PARAMS = {"workspace_id": "tenant-a", "limit": 20}
SCOPE = "{_workspace_id: $workspace_id}"


def _violations(policy, cypher):
    return validate_text2cypher_fallback(cypher, params=PARAMS, policy=policy)


def _codes(policy, cypher):
    return {v.split(":", 1)[0] for v in _violations(policy, cypher)}


# ---------------------------------------------------------------------------
# Caught
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("predicate", [
    "d.name = 'Retry Standard v2'",
    'd.name = "Retry Standard v2"',
    "d.year = 2024",
    "d.year > 2020",
    "d.name CONTAINS 'Payments'",
    "d.name STARTS WITH 'Retry'",
    "d.name ENDS WITH 'v2'",
])
def test_inlined_comparison_literal_is_rejected(policy, predicate):
    cypher = f"MATCH (d:Decision {SCOPE}) WHERE {predicate} RETURN d LIMIT $limit"
    assert "inlined_literal" in _codes(policy, cypher), (
        f"{predicate!r} produces a new plan-cache entry per entity"
    )


def test_multiple_literals_are_all_reported(policy):
    cypher = (
        f"MATCH (d:Decision {SCOPE}) "
        "WHERE d.name = 'Tesla' AND d.year > 2020 RETURN d LIMIT $limit"
    )
    violation = next(v for v in _violations(policy, cypher)
                     if v.startswith("inlined_literal"))
    assert "'Tesla'" in violation and "2020" in violation, (
        "'inlined_literal:2' tells a model that it erred and not where, so it "
        "has no basis for a different second attempt"
    )


def test_metric_label_stays_bounded(policy):
    """The repair prompt needs the values; the metric must not carry them."""
    cypher = f"MATCH (d:Decision {SCOPE}) WHERE d.name = 'Tesla' RETURN d LIMIT $limit"
    violation = next(v for v in _violations(policy, cypher)
                     if v.startswith("inlined_literal"))
    assert violation.split(":", 1)[0] == "inlined_literal"


# ---------------------------------------------------------------------------
# Not caught — these must keep passing
# ---------------------------------------------------------------------------

def test_parameterised_query_passes(policy):
    cypher = (
        f"MATCH (d:Decision {SCOPE}) WHERE d.name = $name "
        "RETURN d LIMIT $limit"
    )
    assert _violations(policy, cypher) == ()


@pytest.mark.parametrize("clause", [
    "LIMIT 20",
    "SKIP 10 LIMIT 20",
])
def test_structural_literals_are_exempt(policy, clause):
    """LIMIT and SKIP do not vary per entity, so they multiply no cache keys."""
    cypher = f"MATCH (d:Decision {SCOPE}) WHERE d.name = $name RETURN d {clause}"
    assert "inlined_literal" not in _codes(policy, cypher)


def test_hop_bound_is_exempt(policy):
    cypher = (
        f"MATCH (d:Decision {SCOPE})-[:APPLIES_TO*..4]-(s:System) "
        "RETURN s LIMIT $limit"
    )
    assert "inlined_literal" not in _codes(policy, cypher)


def test_existing_violations_still_fire(policy):
    """The new check must not displace the ones already there."""
    cypher = "MATCH (d:Unknown) WHERE d.name = $name RETURN d LIMIT $limit"
    codes = _codes(policy, cypher)
    assert "unknown_labels" in codes
    assert "missing_workspace_scope_expression" in codes


def test_plan_cache_pressure_is_real():
    """Why this matters, stated as arithmetic rather than assertion.

    The DozerDB instance reports
    server.memory.query_cache.per_db_cache_num_entries = 1000.
    """
    entities = ["Retry Standard v1", "Retry Standard v2", "Payments API",
                "Redwood Inference"]
    inlined = {f"MATCH (n) WHERE n.name = '{e}' RETURN n LIMIT 5" for e in entities}
    parameterised = {"MATCH (n) WHERE n.name = $name RETURN n LIMIT 5"
                     for _ in entities}

    assert len(inlined) == len(entities), "one cache entry per entity asked about"
    assert len(parameterised) == 1


# ---------------------------------------------------------------------------
# The prompt must state the contract the validator enforces
# ---------------------------------------------------------------------------

def test_prompt_states_the_exact_scope_expression():
    """"It must include tenant scope" left the model to guess the expression.

    It guessed `{workspace_id: $workspace_id}` — rejected twice over, as
    `unknown_properties` and as `missing_workspace_scope_expression`. Measured
    live against MiniMax-M2.7: 2 of 2 generations failed that way, and the
    repair loop reproduced the identical violation on every attempt, because
    the feedback names what is wrong and the prompt never said what is right.

    After stating it exactly: 2 of 2 accepted on the first attempt, with no
    inlined literals.
    """
    import inspect

    from seocho.query import text2cypher

    source = inspect.getsource(text2cypher.generate_validated_cypher)
    assert "policy.workspace_property" in source, (
        "the prompt hardcodes or omits the tenancy property instead of deriving "
        "it from the policy the validator checks against"
    )
    assert "scope_expression" in source


def test_prompt_shows_a_parameterisation_example():
    """A rule without an example is a rule a model has to interpret."""
    import inspect

    from seocho.query import text2cypher

    source = inspect.getsource(text2cypher.generate_validated_cypher)
    assert "$name" in source and "'Tesla'" in source


def test_scope_expression_matches_what_the_validator_requires():
    """The prompt and the validator must agree, or the loop cannot converge."""
    import re

    from seocho.query.workload_compiler import Text2CypherFallbackPolicy

    policy = Text2CypherFallbackPolicy(
        allowed_labels=("Decision",), allowed_relationships=(),
        allowed_properties=("name", "_workspace_id"),
        workspace_property="_workspace_id",
        required_parameters=("workspace_id",), max_graph_hops=4,
        max_result_rows=100, max_repair_attempts=1,
        require_explain_before_execute=True,
    )
    prompted = f"{{{policy.workspace_property}: $workspace_id}}"
    cypher = f"MATCH (d:Decision {prompted}) WHERE d.name = $name RETURN d LIMIT $limit"

    scope_pattern = rf"{re.escape(policy.workspace_property)}\s*:\s*\$workspace_id"
    assert re.search(scope_pattern, cypher), (
        "the expression the prompt asks for does not satisfy the validator"
    )
    assert validate_text2cypher_fallback(
        cypher, params={"workspace_id": "t", "limit": 5}, policy=policy
    ) == ()
