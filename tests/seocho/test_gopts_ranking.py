"""GOPTS G4 Layer-1 — ranking-quality harness contract tests (ADR-0097).

Pins three things:

1. The metric primitives (top1_accuracy, ndcg_at_k, kendall_tau) compute
   correct values on hand-checked inputs.
2. The fixture loader reads every YAML under tests/seocho/fixtures/gopts/.
3. The harness end-to-end produces a Layer1Report whose aggregates
   match expected values when paired with the
   ``make_expected_top1_oracle_fn`` (deterministic, in-process oracle).

Layer-2 (latency) and Layer-3 (answer F1) are explicit follow-ups; they
are not exercised here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple

from seocho.eval import gopts_ranking
from seocho.query.contracts import PatternSpec


# --- metric primitives -------------------------------------------------------


def test_top1_accuracy_returns_one_when_top_matches() -> None:
    assert gopts_ranking.top1_accuracy(["a", "b"], ["a", "b"]) == 1.0


def test_top1_accuracy_returns_zero_on_top_mismatch() -> None:
    assert gopts_ranking.top1_accuracy(["b", "a"], ["a", "b"]) == 0.0


def test_top1_accuracy_returns_zero_on_empty_input() -> None:
    assert gopts_ranking.top1_accuracy([], ["a"]) == 0.0
    assert gopts_ranking.top1_accuracy(["a"], []) == 0.0


def test_ndcg_at_k_perfect_ranking_is_one() -> None:
    predicted = ["a", "b", "c", "d"]
    assert gopts_ranking.ndcg_at_k(predicted, predicted, k=4) == 1.0


def test_ndcg_at_k_zero_on_disjoint_lists() -> None:
    assert gopts_ranking.ndcg_at_k(["x", "y"], ["a", "b"], k=2) == 0.0


def test_ndcg_at_k_between_zero_and_one_for_partial_overlap() -> None:
    predicted = ["b", "a", "c"]
    oracle = ["a", "b", "c"]
    score = gopts_ranking.ndcg_at_k(predicted, oracle, k=3)
    assert 0.0 < score < 1.0


def test_kendall_tau_full_agreement_is_one() -> None:
    assert gopts_ranking.kendall_tau(["a", "b", "c"], ["a", "b", "c"]) == 1.0


def test_kendall_tau_full_inversion_is_minus_one() -> None:
    assert gopts_ranking.kendall_tau(["a", "b", "c"], ["c", "b", "a"]) == -1.0


def test_kendall_tau_singleton_is_one_by_convention() -> None:
    """Degenerate fixtures (K=1 from G3's catalog) report tau=1.0 so
    they don't drag the suite average down."""
    assert gopts_ranking.kendall_tau(["a"], ["a"]) == 1.0


# --- fixture loader ----------------------------------------------------------


def _fixture_dir() -> Path:
    return Path(__file__).parent / "fixtures" / "gopts"


def test_load_fixtures_reads_every_yaml() -> None:
    fixtures = gopts_ranking.load_fixtures(_fixture_dir())
    # Seed suite shipped with G4 is at least 8 fixtures.
    assert len(fixtures) >= 8
    ids = {f.fixture_id for f in fixtures}
    # representative samples
    for expected_id in (
        "01_entity_lookup_by_name",
        "02_relationship_one_hop",
        "03_path_query",
        "05_finance_metric_value",
        "06_finance_metric_delta",
        "07_label_count",
    ):
        assert expected_id in ids, f"missing fixture {expected_id}"


def test_load_fixtures_skips_unknown_keys() -> None:
    """Unknown YAML keys (e.g. Layer-2 metadata) must not break Layer-1."""
    fixtures = gopts_ranking.load_fixtures(_fixture_dir())
    for f in fixtures:
        assert f.fixture_id
        assert f.question
        assert f.intent


# --- harness end-to-end ------------------------------------------------------


def test_run_layer1_with_expected_top1_oracle_reports_perfect_score() -> None:
    """When every fixture's expected_top1_pattern_id matches the cost
    model's predicted top-1 (which it does for G3's catalog because
    enumerate_for_shape returns one pattern per shape), Layer-1
    aggregates are all perfect."""
    fixtures = gopts_ranking.load_fixtures(_fixture_dir())
    oracle_fn = gopts_ranking.make_expected_top1_oracle_fn()
    report = gopts_ranking.run_layer1(fixtures, oracle_fn=oracle_fn, k=4)

    assert report.total_fixtures == len(fixtures)
    assert report.avg_top1_accuracy == 1.0
    assert report.avg_ndcg_at_k == 1.0
    assert report.avg_kendall_tau == 1.0


def test_run_layer1_drops_fixtures_with_no_registered_pattern() -> None:
    """A fixture whose intent has no registered pattern is silently
    dropped — surfaces in the missing count via total_fixtures."""
    bad = gopts_ranking.GoptsFixture(
        fixture_id="bad",
        question="?",
        intent="not_a_real_shape",
    )
    report = gopts_ranking.run_layer1(
        [bad],
        oracle_fn=gopts_ranking.make_expected_top1_oracle_fn(),
    )
    assert report.total_fixtures == 0


def test_run_layer1_uses_index_stats_for_cost() -> None:
    """When IndexStats are passed in, the cost ranker uses them.
    Smoke-test: passing stats with explicit label_counts changes the
    breakdown's estimated_row_count from defaults."""
    fixtures = [
        gopts_ranking.GoptsFixture(
            fixture_id="g4_smoke",
            question="What do we know about Apple?",
            intent="entity_lookup",
            expected_top1_pattern_id="pattern:entity_lookup_by_name",
        )
    ]
    stats = {
        "label_counts": {"Entity": 42},
        "indexes": [{"labels_or_types": ["Entity"]}],
        "rel_counts": {},
    }
    report = gopts_ranking.run_layer1(
        fixtures,
        oracle_fn=gopts_ranking.make_expected_top1_oracle_fn(),
        index_stats=stats,
    )
    # Single fixture, single candidate — perfect score.
    assert report.avg_top1_accuracy == 1.0


def test_run_layer1_reports_k_greater_than_one_for_alt_shapes() -> None:
    """F1 (seocho-suj2): pattern_catalog declares alternatives on
    pattern:shortest_path (for relationship_lookup) and
    pattern:neighbors_one_hop (for entity_lookup). The harness's
    per-fixture candidate_count must reflect K>1 on those intents.
    Pins the F1 contract: declaring an alternative is supposed to
    surface to the eval layer."""
    fixtures = gopts_ranking.load_fixtures(_fixture_dir())
    report = gopts_ranking.run_layer1(
        fixtures,
        oracle_fn=gopts_ranking.make_expected_top1_oracle_fn(),
    )
    by_id = {fr.fixture_id: fr for fr in report.fixture_results}

    # entity_lookup fixture must now see K>=2 (primary + neighbors alt)
    assert by_id["01_entity_lookup_by_name"].candidate_count >= 2
    # relationship_lookup fixture must now see K>=2 (primary + shortest_path alt)
    assert by_id["02_relationship_one_hop"].candidate_count >= 2
    # singleton shapes stay at K=1 (no alternatives registered)
    assert by_id["05_finance_metric_value"].candidate_count == 1
    assert by_id["07_label_count"].candidate_count == 1


def test_layer1_report_to_dict_serializable() -> None:
    """Layer1Report.to_dict() must be JSON-serializable so the harness
    output can land in JSONL traces."""
    import json

    fixtures = gopts_ranking.load_fixtures(_fixture_dir())
    report = gopts_ranking.run_layer1(
        fixtures,
        oracle_fn=gopts_ranking.make_expected_top1_oracle_fn(),
    )
    blob = json.dumps(report.to_dict())
    assert "fixtures" in blob
    assert "avg_top1_accuracy" in blob


# --- NLCypherExampleStore G4 fields ------------------------------------------


def test_example_store_accepts_g4_fields() -> None:
    """ADR-0097 G4: NLCypherExampleStore.add accepts cost + oracle fields
    so a Tier-2 NL→Cypher call can record both predicted and oracle
    rankings in a single write."""
    from seocho.store.vector import NLCypherExampleStore

    store = NLCypherExampleStore()
    store.add(
        workspace_id="ws-g4",
        question="What do we know about Apple?",
        cypher="MATCH (e:Entity {name:'Apple'}) RETURN e",
        plan_cost_estimate=12.5,
        k_rank_position=0,
        selected_pattern_id="pattern:entity_lookup_by_name",
        execution_row_count=1,
        total_latency_ms=18.4,
        enumeration_latency_ms=0.3,
        profile_db_hits=3,
        oracle_rank_position=0,
    )
    examples = store.search(workspace_id="ws-g4", question="x", k=1)
    assert len(examples) == 1
    ex = examples[0]
    assert ex.plan_cost_estimate == 12.5
    assert ex.k_rank_position == 0
    assert ex.selected_pattern_id == "pattern:entity_lookup_by_name"
    assert ex.profile_db_hits == 3
    assert ex.oracle_rank_position == 0


def test_example_store_existing_callers_unaffected_by_new_fields() -> None:
    """G4's schema extension is additive — pre-G4 callers that don't
    pass the new kwargs still work."""
    from seocho.store.vector import NLCypherExampleStore

    store = NLCypherExampleStore()
    store.add(workspace_id="ws", question="q", cypher="c")  # pre-G4 surface
    examples = store.search(workspace_id="ws", question="q", k=1)
    assert len(examples) == 1
    assert examples[0].plan_cost_estimate is None
    assert examples[0].profile_db_hits is None
