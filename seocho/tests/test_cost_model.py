"""GOPTS G2 (ADR-0097) — cost_model linear scoring contract.

The cost model is the input ranker for K-candidate enumeration. These
tests pin its behavior so a coefficient or feature change leaves a
trace in the regression sweep before it ships.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from seocho.query import cost_model, pattern_catalog
from seocho.query.contracts import PatternSpec


def _factory_noop(builder: Any, **_: Any) -> Tuple[str, Dict[str, Any]]:
    return "", {}


def _spec(
    pattern_id: str,
    *,
    cypher_shape: str,
    required_labels: Tuple[str, ...] = (),
    cost_hints: Dict[str, Any] = None,
) -> PatternSpec:
    return PatternSpec(
        pattern_id=pattern_id,
        intent_id="entity_summary",
        cypher_shape=cypher_shape,
        required_labels=required_labels,
        required_relations=(),
        schema_preconditions=(),
        cost_hints=cost_hints or {},
        template_factory=_factory_noop,
    )


# --- plan_depth coefficient ---------------------------------------------------


def test_count_shape_has_zero_plan_depth() -> None:
    breakdown = cost_model.cost(_spec("p:count", cypher_shape="count"))
    assert breakdown.plan_depth == 0


def test_path_shape_has_largest_plan_depth() -> None:
    path = cost_model.cost(_spec("p:path", cypher_shape="path"))
    relationship = cost_model.cost(
        _spec("p:rel", cypher_shape="relationship_lookup")
    )
    entity = cost_model.cost(_spec("p:ent", cypher_shape="entity_lookup"))
    assert path.plan_depth > relationship.plan_depth
    assert relationship.plan_depth > entity.plan_depth


# --- estimated_row_count from IndexStats label_counts -------------------------


def test_label_count_drives_row_estimate() -> None:
    stats = {
        "label_counts": {"Entity": 100, "Company": 5},
        "indexes": [],
    }
    entity = cost_model.cost(
        _spec("p:e", cypher_shape="entity_lookup", required_labels=("Entity",)),
        index_stats=stats,
    )
    company = cost_model.cost(
        _spec("p:c", cypher_shape="entity_lookup", required_labels=("Company",)),
        index_stats=stats,
    )
    assert entity.estimated_row_count == 100
    assert company.estimated_row_count == 5


def test_missing_label_count_falls_back_to_zero() -> None:
    stats = {"label_counts": {"Entity": 100}, "indexes": []}
    breakdown = cost_model.cost(
        _spec("p:m", cypher_shape="entity_lookup", required_labels=("Missing",)),
        index_stats=stats,
    )
    assert breakdown.estimated_row_count == 0


# --- index_miss_penalty -------------------------------------------------------


def test_index_miss_when_required_label_has_no_index() -> None:
    stats = {
        "label_counts": {"Entity": 100, "Bond": 50},
        "indexes": [{"labels_or_types": ["Entity"]}],
    }
    matched = cost_model.cost(
        _spec("p:m", cypher_shape="entity_lookup", required_labels=("Entity",)),
        index_stats=stats,
    )
    missed = cost_model.cost(
        _spec("p:x", cypher_shape="entity_lookup", required_labels=("Bond",)),
        index_stats=stats,
    )
    assert matched.index_miss_penalty == 0
    assert missed.index_miss_penalty == 1


# --- cartesian_risk -----------------------------------------------------------


def test_unbounded_path_carries_cartesian_risk() -> None:
    breakdown = cost_model.cost(
        _spec("p:p", cypher_shape="path", cost_hints={"unbounded_path": True}),
    )
    assert breakdown.cartesian_risk == 1


def test_finance_delta_carries_cartesian_risk() -> None:
    breakdown = cost_model.cost(
        _spec(
            "p:d",
            cypher_shape="financial_metric_delta",
            cost_hints={"scans_multiple_years": True},
        ),
    )
    assert breakdown.cartesian_risk == 1


# --- rank_candidates ----------------------------------------------------------


def test_rank_candidates_returns_ascending_cost() -> None:
    cheap = _spec(
        "p:cheap",
        cypher_shape="entity_lookup",
        required_labels=("Small",),
    )
    expensive = _spec(
        "p:expensive",
        cypher_shape="path",
        cost_hints={"unbounded_path": True},
    )
    stats = {
        "label_counts": {"Small": 5, "Big": 10_000_000},
        "indexes": [{"labels_or_types": ["Small"]}],
    }

    ranked = cost_model.rank_candidates([expensive, cheap], index_stats=stats)
    pattern_ids = [spec.pattern_id for spec, _ in ranked]
    assert pattern_ids == ["p:cheap", "p:expensive"]


def test_rank_candidates_breaks_ties_by_pattern_id() -> None:
    """Equal cost → deterministic order by pattern_id so the regression
    harness doesn't flake."""
    a = _spec("p:a", cypher_shape="entity_lookup")
    b = _spec("p:b", cypher_shape="entity_lookup")
    ranked = cost_model.rank_candidates([b, a])
    pattern_ids = [spec.pattern_id for spec, _ in ranked]
    assert pattern_ids == ["p:a", "p:b"]


# --- enumerate_for_shape (pattern_catalog G2 API) ----------------------------


def test_enumerate_for_shape_returns_primary_only_when_no_alternatives() -> None:
    """A truly singleton shape (financial_metric_lookup has no
    registered alternatives) returns just the primary pattern."""
    candidates = pattern_catalog.enumerate_for_shape("financial_metric_lookup")
    assert len(candidates) == 1
    assert candidates[0].pattern_id == "pattern:finance_metric_value"


def test_enumerate_for_shape_unknown_returns_empty() -> None:
    assert pattern_catalog.enumerate_for_shape("does_not_exist") == []


# --- F1 (seocho-suj2): K>1 alternatives ---------------------------------------


def test_entity_lookup_enumerates_with_neighbors_alternative() -> None:
    """F1: pattern:neighbors_one_hop is registered as an alternative for
    the entity_lookup shape so G2's enumerator gets two real candidates."""
    candidates = pattern_catalog.enumerate_for_shape("entity_lookup")
    pattern_ids = [c.pattern_id for c in candidates]
    assert pattern_ids[0] == "pattern:entity_lookup_by_name"  # primary first
    assert "pattern:neighbors_one_hop" in pattern_ids


def test_relationship_lookup_enumerates_with_shortest_path_alternative() -> None:
    """F1: pattern:shortest_path declares relationship_lookup as an
    alternative — under-specified relationship questions get the
    general path option ranked alongside the hop-1 specialist."""
    candidates = pattern_catalog.enumerate_for_shape("relationship_lookup")
    pattern_ids = [c.pattern_id for c in candidates]
    assert pattern_ids[0] == "pattern:relationship_lookup_hop1"  # primary first
    assert "pattern:shortest_path" in pattern_ids


def test_cost_ranker_picks_relationship_hop1_over_shortest_path() -> None:
    """F1: the cost model must prefer the cheap specialist
    (relationship_lookup_hop1, plan_depth=2) over the expensive general
    (shortest_path, plan_depth=5 + cartesian_risk=1) when both are
    candidates for the same question. Pins ranker tie-break against
    regression."""
    candidates = pattern_catalog.enumerate_for_shape("relationship_lookup")
    ranked = cost_model.rank_candidates(candidates)
    assert ranked[0][0].pattern_id == "pattern:relationship_lookup_hop1"
    assert ranked[-1][0].pattern_id == "pattern:shortest_path"


def test_cost_ranker_picks_entity_lookup_over_neighbors_one_hop() -> None:
    """F1: entity_lookup_by_name (plan_depth=1) beats neighbors_one_hop
    (plan_depth=2) on cost when both are candidates for entity_lookup.
    Pins the ranker so adding cheaper alternatives later doesn't
    silently flip the existing behavior."""
    candidates = pattern_catalog.enumerate_for_shape("entity_lookup")
    ranked = cost_model.rank_candidates(candidates)
    assert ranked[0][0].pattern_id == "pattern:entity_lookup_by_name"


def test_enumerate_for_shape_picks_up_alternatives() -> None:
    """When a pattern declares ``alternatives``, enumerate_for_shape
    returns it alongside the primary."""
    # Register an ad-hoc pattern that declares "entity_lookup" as an
    # alternative; the primary entity_lookup pattern stays primary.

    @pattern_catalog.register_pattern(
        pattern_id="pattern:test_entity_lookup_by_id",
        intent_id="entity_summary",
        cypher_shape="entity_lookup_by_id",
        required_labels=("Entity",),
        cost_hints={"prefers_indexed": ["id"]},
        alternatives=("entity_lookup",),
    )
    def _factory(builder: Any, **_: Any) -> Tuple[str, Dict[str, Any]]:
        return "", {}

    try:
        candidates = pattern_catalog.enumerate_for_shape("entity_lookup")
        ids = [c.pattern_id for c in candidates]
        assert ids[0] == "pattern:entity_lookup_by_name"  # primary first
        assert "pattern:test_entity_lookup_by_id" in ids
    finally:
        # Best-effort cleanup so we don't leak state into other tests.
        pattern_catalog._REGISTRY.pop("pattern:test_entity_lookup_by_id", None)
        pattern_catalog._SHAPE_INDEX.pop("entity_lookup_by_id", None)
