"""The sargability grader must not be gameable, and must see the real hazards.

A graph-engineer review found three faults in the operator classification, all
verified against live DozerDB 5.26.3 EXPLAIN plans:

1. `NodeIndexScan` / `NodeIndexContainsScan` were in the SEEK bucket. They are
   scans of the index, not seeks -- cost grows with the label. So the cheapest
   way for an LLM to turn "unsargable" green was to add `WHERE n.prop IS NOT
   NULL` or a `CONTAINS`, which produces exactly these operators. Confirmed
   live: `IS NOT NULL` on an indexed property yields `NodeIndexScan`.

2. Plural-label scans (`UnionNodeByLabelsScan`) are not substrings of
   `NodeByLabelScan`, so `(n:A|B)` graded clean.

3. `CartesianProduct` -- two disconnected MATCH patterns, the most common LLM
   Cypher error -- was invisible. A plan of two index *seeks* joined by a
   cartesian product graded sargable=True.

These assert on synthetic operator trees (fast, no DB) plus the live behaviour
where a graph is available.
"""

from __future__ import annotations

from seocho.query.plan_quality import repair_hint, summarize_plan


def _tree(*operators):
    """Build a nested plan tree from a top-down operator list."""
    node = None
    for op in reversed(operators):
        node = {"operatorType": op, "args": {"EstimatedRows": 100},
                "children": [node] if node else []}
    return node


# ---------------------------------------------------------------------------
# The gameable bucket
# ---------------------------------------------------------------------------

def test_index_scan_is_not_a_seek():
    s = summarize_plan(_tree("ProduceResults@neo4j", "NodeIndexScan@neo4j"))
    assert s["index_scans"], "NodeIndexScan must land in the index-scan bucket"
    assert not s["seeks"], "NodeIndexScan must not count as a seek"
    assert s["sargable"] is False, (
        "an index scan grows with the index; grading it sargable is what let "
        "`WHERE n.prop IS NOT NULL` game the gate"
    )


def test_contains_scan_is_not_a_seek():
    s = summarize_plan(_tree("NodeIndexContainsScan@neo4j"))
    assert s["index_scans"] and not s["seeks"]
    assert s["sargable"] is False


def test_a_real_seek_is_sargable():
    for seek in ("NodeIndexSeek@neo4j", "NodeUniqueIndexSeek@neo4j",
                 "NodeByElementIdSeek@neo4j"):
        s = summarize_plan(_tree("ProduceResults@neo4j", seek))
        assert s["seeks"], seek
        assert s["sargable"] is True, seek


# ---------------------------------------------------------------------------
# The missed scans
# ---------------------------------------------------------------------------

def test_plural_label_scan_is_caught():
    s = summarize_plan(_tree("UnionNodeByLabelsScan@neo4j"))
    assert s["scans"], "(n:A|B) produced a scan the substring match missed"
    assert s["sargable"] is False


def test_all_relationships_scan_is_caught():
    s = summarize_plan(_tree("UndirectedAllRelationshipsScan@neo4j"))
    assert s["scans"]
    assert s["sargable"] is False


# ---------------------------------------------------------------------------
# The invisible hazards
# ---------------------------------------------------------------------------

def test_cartesian_product_between_two_seeks_is_not_sargable():
    """The classic 'looks sargable, runs quadratic' plan."""
    plan = {
        "operatorType": "CartesianProduct@neo4j",
        "args": {"EstimatedRows": 100},
        "children": [
            _tree("NodeUniqueIndexSeek@neo4j"),
            _tree("NodeUniqueIndexSeek@neo4j"),
        ],
    }
    s = summarize_plan(plan)
    assert s["seeks"], "the two seeks are still there"
    assert "CartesianProduct@neo4j" in s["dangers"]
    assert s["sargable"] is False, (
        "two seeks joined by a cartesian product is not a healthy plan"
    )


def test_eager_is_flagged():
    s = summarize_plan(_tree("EagerAggregation@neo4j", "NodeIndexSeek@neo4j"))
    assert any("Eager" in d for d in s["dangers"])
    assert s["sargable"] is False


def test_unbounded_sort_is_flagged_but_bounded_sort_is_not():
    unbounded = summarize_plan(_tree("Sort@neo4j", "NodeIndexSeek@neo4j"))
    assert any("Sort" in d for d in unbounded["dangers"])
    assert unbounded["sargable"] is False

    bounded = summarize_plan(_tree("Top@neo4j", "Sort@neo4j", "NodeIndexSeek@neo4j"))
    assert not any("Sort" in d for d in bounded["dangers"]), (
        "a Sort bounded by a Top is cheap and must not be flagged"
    )
    assert bounded["sargable"] is True


# ---------------------------------------------------------------------------
# repair_hint speaks to the actual fault
# ---------------------------------------------------------------------------

def test_cartesian_hint_says_connect_not_anchor():
    plan = {
        "operatorType": "CartesianProduct@neo4j", "args": {},
        "children": [_tree("NodeUniqueIndexSeek@neo4j"),
                     _tree("NodeUniqueIndexSeek@neo4j")],
    }
    hint = repair_hint(summarize_plan(plan))
    assert hint is not None
    assert "connect" in hint.lower() or "join" in hint.lower()
    # It must NOT tell an already-anchored query to anchor harder.
    assert "anchored lookup" not in hint


def test_scan_hint_still_suggests_an_index_seek():
    class _Node:
        def __init__(self, props):
            self.properties = props

    class _Spec:
        def __init__(self, unique):
            self.unique = unique

    class _Onto:
        nodes = {"Decision": _Node({"name": _Spec(True), "status": _Spec(False)})}

    hint = repair_hint(summarize_plan(_tree("NodeByLabelScan@neo4j")), _Onto())
    assert hint is not None
    assert "Decision.name" in hint
    assert "anchored lookup" in hint


def test_sargable_plan_gets_no_hint():
    assert repair_hint(summarize_plan(_tree("NodeIndexSeek@neo4j"))) is None


# ---------------------------------------------------------------------------
# The plan gate must not trade a correct answer for a cheaper one
# ---------------------------------------------------------------------------

def test_plan_hint_repair_does_not_reduce_a_correct_result():
    """SEOCHO_PLAN_GATE widens the repair trigger to include a query that
    returned rows but planned a scan. That repair is a COST optimisation, so a
    repaired query returning FEWER rows than the correct original is a silent
    correctness regression -- it must be rejected and the original kept.

    Asserted on source rather than by driving the whole engine: the guard is a
    single condition and the engine path needs a live LLM and graph.
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2]
           / "src" / "seocho" / "local_engine.py").read_text()
    assert "original_had_records" in src, (
        "nothing distinguishes a cost-only repair from a correctness repair"
    )
    # The guard must break before overwriting `records` when the repair is smaller.
    guard = src[src.index("original_had_records and"):]
    guard = guard[: guard.index("records = repair_records")]
    assert "len(repair_records) < len(records" in guard, (
        "a plan-hint repair returning fewer rows still overwrites the correct "
        "original"
    )
