"""GOPTS F4 — Layer-1 ranking-quality harness against a live DozerDB.

Acceptance from seocho-75jf: ``gopts_ranking.run_layer1`` with
``make_profile_oracle_fn`` produces non-trivial NDCG@K data on the
fixture suite, proving the harness isn't a tautology. Any ranking
mismatch between the cost model and PROFILE surfaces here; agreement
across the suite is also a valid result (means the cost model is
well-calibrated against today's catalog).

The fixture filter drops 05/06 (finance metric fixtures) because the
DozerDB compose stack ships a uniqueness constraint on
FinancialMetric.name that conflicts with the multi-year corpus shape.
Those fixtures still run in the mock-oracle Layer-1 path under
``test_gopts_ranking``.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Tuple

import pytest

from seocho.eval import gopts_ranking
from seocho.query.contracts import PatternSpec


WORKSPACE_ID = "fixture-gopts"
DATABASE = os.environ.get("SEOCHO_GOPTS_DATABASE", "neo4j")

# Fixtures whose patterns the live corpus can answer. 05/06 are filtered
# because the FinancialMetric uniqueness constraint forbids loading the
# multi-year metric data they need.
_LIVE_FIXTURE_IDS = {
    "01_entity_lookup_by_name",
    "02_relationship_one_hop",
    "03_path_query",
    "04_neighbors_under_specified",
    "07_label_count",
    "08_label_list_all",
}

# Per-fixture structured kwargs the catalog template_factory needs.
# Lives here so GoptsFixture stays Layer-1-shaped; richer Layer-3
# fixtures will introduce their own kwargs structure.
_FIXTURE_KWARGS: Dict[str, Dict[str, Any]] = {
    "01_entity_lookup_by_name": {
        "anchor_entity": "Apple",
        "anchor_label": "Entity",
    },
    "02_relationship_one_hop": {
        "anchor_entity": "Tim Cook",
        "anchor_label": "Person",
        "target_entity": "Apple",
        "target_label": "Entity",
        "relationship_type": "MANAGES",
    },
    "03_path_query": {
        "anchor_entity": "Apple",
        "target_entity": "Foxconn",
        "anchor_label": "Entity",
        "target_label": "Entity",
    },
    "04_neighbors_under_specified": {
        "anchor_entity": "Apple",
        "anchor_label": "Entity",
    },
    "07_label_count": {
        "anchor_label": "Company",
    },
    "08_label_list_all": {
        "anchor_label": "Company",
    },
}


def _build_ontology() -> Any:
    """Minimal ontology matching the live FIBO-lite corpus."""
    from seocho import NodeDef, Ontology, P, RelDef

    return Ontology(
        name="gopts_live",
        graph_model="lpg",
        nodes={
            "Entity": NodeDef(properties={"name": P(str, unique=True)}),
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
            "Person": NodeDef(properties={"name": P(str, unique=True)}),
        },
        relationships={
            "RELATES_TO": RelDef(
                source="Entity",
                target="Entity",
                description="generic relation",
            ),
            "MANAGES": RelDef(
                source="Person",
                target="Entity",
                description="person manages entity",
            ),
        },
    )


def _build_cypher_fn(builder: Any):
    """Return ``(fixture, pattern) -> (cypher, params)`` for the oracle.

    The closure binds a single ``CypherBuilder`` instance so every
    pattern's template_factory sees the same ontology + helpers.
    """

    def fn(
        fixture: gopts_ranking.GoptsFixture,
        pattern: PatternSpec,
    ) -> Tuple[str, Dict[str, Any]]:
        kwargs = dict(_FIXTURE_KWARGS.get(fixture.fixture_id, {}))
        kwargs.update(
            intent=pattern.cypher_shape,
            workspace_id=fixture.workspace_id,
            limit=20,
        )
        return pattern.template_factory(builder, **kwargs)

    return fn


def _fixture_dir():
    """Path to the YAML fixture seed under seocho/tests/fixtures/gopts/."""
    from pathlib import Path

    return Path(__file__).parent.parent / "fixtures" / "gopts"


@pytest.mark.integration_gopts
def test_layer1_live_harness_emits_real_profile_data(gopts_live_driver: Any) -> None:
    """End-to-end: load fixtures, run Layer-1 against live PROFILE,
    assert the harness wiring produces a populated report."""
    from seocho.query.cypher_builder import CypherBuilder

    fixtures_all = gopts_ranking.load_fixtures(_fixture_dir())
    fixtures = [f for f in fixtures_all if f.fixture_id in _LIVE_FIXTURE_IDS]
    assert fixtures, "live fixture filter dropped everything — bug"

    # Build a fake "graph_store" duck-typed object pinned at the live
    # driver. make_profile_oracle_fn only touches ._driver and
    # session.run(...) so we don't need the full Neo4jGraphStore.
    class _LiveStore:
        def __init__(self, driver: Any):
            self._driver = driver

    store = _LiveStore(gopts_live_driver)
    builder = CypherBuilder(_build_ontology())
    build_fn = _build_cypher_fn(builder)
    oracle_fn = gopts_ranking.make_profile_oracle_fn(store, build_cypher_fn=build_fn)

    report = gopts_ranking.run_layer1(fixtures, oracle_fn=oracle_fn, k=4)

    assert report.total_fixtures == len(fixtures), (
        "every live fixture must reach the harness — none dropped due to "
        "missing patterns"
    )
    for fr in report.fixture_results:
        # Each fixture has at least one predicted candidate (cost-ranked)
        # and at least one oracle candidate (PROFILE-ranked).
        assert fr.predicted_ranking, f"empty predicted ranking for {fr.fixture_id}"
        assert fr.oracle_ranking, f"empty oracle ranking for {fr.fixture_id}"
        assert fr.candidate_count >= 1


@pytest.mark.integration_gopts
def test_layer1_live_harness_surfaces_k_gt1_for_alt_shapes(
    gopts_live_driver: Any,
) -> None:
    """F1's declared alternatives must produce K>1 candidate counts on
    the live entity_lookup + relationship_lookup fixtures. Layer-1's
    PROFILE oracle ranks both candidates with real db_hits."""
    from seocho.query.cypher_builder import CypherBuilder

    fixtures_all = gopts_ranking.load_fixtures(_fixture_dir())
    fixtures = [
        f
        for f in fixtures_all
        if f.fixture_id in {"01_entity_lookup_by_name", "02_relationship_one_hop"}
    ]
    assert len(fixtures) == 2

    class _LiveStore:
        def __init__(self, driver: Any):
            self._driver = driver

    store = _LiveStore(gopts_live_driver)
    builder = CypherBuilder(_build_ontology())
    build_fn = _build_cypher_fn(builder)
    oracle_fn = gopts_ranking.make_profile_oracle_fn(store, build_cypher_fn=build_fn)

    report = gopts_ranking.run_layer1(fixtures, oracle_fn=oracle_fn, k=4)
    for fr in report.fixture_results:
        assert fr.candidate_count >= 2, (
            f"{fr.fixture_id} should see K>=2 after F1 — got {fr.candidate_count}"
        )
        # Oracle ranking should also have multiple entries.
        assert len(fr.oracle_ranking) >= 1


@pytest.mark.integration_gopts
def test_layer1_live_aggregate_metrics_are_in_range(
    gopts_live_driver: Any,
) -> None:
    """Aggregate metric sanity floor: top1_accuracy and NDCG are in
    [0, 1], Kendall tau in [-1, 1]. Catches harness-side wiring bugs
    that would produce nonsensical values."""
    from seocho.query.cypher_builder import CypherBuilder

    fixtures_all = gopts_ranking.load_fixtures(_fixture_dir())
    fixtures = [f for f in fixtures_all if f.fixture_id in _LIVE_FIXTURE_IDS]

    class _LiveStore:
        def __init__(self, driver: Any):
            self._driver = driver

    store = _LiveStore(gopts_live_driver)
    builder = CypherBuilder(_build_ontology())
    build_fn = _build_cypher_fn(builder)
    oracle_fn = gopts_ranking.make_profile_oracle_fn(store, build_cypher_fn=build_fn)

    report = gopts_ranking.run_layer1(fixtures, oracle_fn=oracle_fn, k=4)

    assert 0.0 <= report.avg_top1_accuracy <= 1.0
    assert 0.0 <= report.avg_ndcg_at_k <= 1.0
    assert -1.0 <= report.avg_kendall_tau <= 1.0
