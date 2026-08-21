"""The PROFILE path collected nothing while paying for a second execution.

A graph-engineer review found the sampling path did this:

    profiled = executor.execute(QueryPlan(cypher=f"PROFILE {cypher}", ...))
    for row in profiled.records or []:
        if isinstance(row, dict) and row.get("profile"):
            profile = row["profile"]

The PROFILE tree lives on the **ResultSummary**, not in the result rows, and
`Neo4jGraphStore.query` returns `[record.data() for record in result]` — it
discards the summary entirely. So `profile` was always None,
`summarize_profile(None)` returned `{"available": False}`, and nothing was ever
recorded. What it did cost was a full second execution of every sampled query.

Verified against a live DozerDB 5.26.3:

    OLD path — profile found in result rows: False
    NEW path — profile on the summary:       True

The test that "pinned" this behaviour faked `records = [{"profile": {...}}]`, a
shape the real store cannot produce, so five tests passed against a fiction.
"""

from __future__ import annotations

import pytest

from seocho.query.plan_quality import (
    span_attributes,
    summarize_plan,
    summarize_profile,
)


def _profile_tree() -> dict:
    """Driver-shaped profile: counters on the node, children nested."""
    return {
        "operatorType": "ProduceResults@neo4j",
        "dbHits": 0, "rows": 10,
        "pageCacheHits": 8, "pageCacheMisses": 2,
        "args": {"EstimatedRows": 10},
        "children": [{
            "operatorType": "NodeByLabelScan@neo4j",
            "dbHits": 500, "rows": 10,
            "pageCacheHits": 40, "pageCacheMisses": 60,
            "args": {"EstimatedRows": 1000},
            "children": [],
        }],
    }


# ---------------------------------------------------------------------------
# The store grows a way to harvest a profile at all
# ---------------------------------------------------------------------------

def test_store_exposes_profile_plan():
    """`query()` returns rows and drops the summary, so PROFILE needs its own
    accessor — the same reason `explain_plan` exists."""
    from seocho.store.graph import Neo4jGraphStore

    assert hasattr(Neo4jGraphStore, "profile_plan")


def test_profile_plan_does_not_route_through_query():
    import inspect

    from seocho.store.graph import Neo4jGraphStore

    source = inspect.getsource(Neo4jGraphStore.profile_plan)
    assert "session.run" in source, "must read the summary directly"
    assert "self.query(" not in source, (
        "query() discards the ResultSummary, which is where the profile lives"
    )


# ---------------------------------------------------------------------------
# The summary carries the signals that separate root causes
# ---------------------------------------------------------------------------

def test_page_cache_ratio_is_reported():
    """Separates 'the graph does not fit in the page cache' from 'the query is
    bad'. Without it an infra problem attributes to retrieval quality."""
    summary = summarize_profile(_profile_tree())
    assert summary["page_cache_hit_ratio"] == pytest.approx(48 / 110)


def test_estimate_error_is_reported():
    """Estimated vs actual per operator is the best signal for 'the planner was
    wrong', which is a different root cause from 'the query was wrong'."""
    summary = summarize_profile(_profile_tree())
    assert summary["worst_estimate_ratio"] == pytest.approx(100.0)


def test_db_hits_per_row_is_scale_free():
    """Raw db_hits is not comparable across questions."""
    summary = summarize_profile(_profile_tree())
    assert summary["db_hits"] == 500
    assert summary["db_hits_per_row"] == pytest.approx(500 / 20)


def test_summed_rows_are_named_for_what_they_are():
    """Summing rows across the tree double-counts every pipeline stage, so it is
    neither result size nor total work — and it sat next to db_hits where it
    read as result size."""
    summary = summarize_profile(_profile_tree())
    assert summary["intermediate_rows"] == 20


def test_no_profile_is_reported_as_unavailable():
    assert summarize_profile(None) == {"available": False}


# ---------------------------------------------------------------------------
# An EXPLAIN summary must not raise, and must not fabricate counters
# ---------------------------------------------------------------------------

def _explain_summary() -> dict:
    return summarize_plan({
        "operatorType": "NodeByLabelScan@neo4j",
        "args": {"EstimatedRows": 1000},
        "children": [],
    })


def test_span_attributes_accepts_an_explain_summary():
    """It indexed summary["db_hits"], which summarize_plan never sets — a
    KeyError for the exact caller the function exists to serve."""
    attrs = span_attributes(_explain_summary())
    assert attrs, "explain summaries produced no attributes"
    assert "db.plan.db_hits" not in attrs


def test_span_attributes_says_which_instrument_produced_it():
    assert span_attributes(_explain_summary())["db.plan.source"] == "explain"
    assert span_attributes(summarize_profile(_profile_tree()))["db.plan.source"] == "profile"


def test_explain_summary_does_not_record_zero_db_hits():
    """`.get("db_hits") or 0` recorded a real zero for every explained plan —
    indistinguishable from a query that genuinely touched nothing, and wrong in
    the same direction every time."""
    import inspect

    from seocho.query import plan_quality

    source = inspect.getsource(plan_quality.record_metrics)
    assert 'get("db_hits") or 0' not in source
    assert 'summary.get("db_hits") is not None' in source
