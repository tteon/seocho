"""Query-plan quality signals for tracing (ADR-0144, seocho-d6x.5).

Latency and row counts say how long a query took; they do not say whether it
will keep working as the graph grows. Two queries returning identical answers can
differ by five orders of magnitude in work done — measured on a synthetic AML
graph, an anchored traversal cost 25 db hits at every scale factor while the same
question expressed without a label qualifier cost 6.6M at SF1000. Accuracy tests
cannot separate them, and neither can wall-clock at small scale: at SF1 the two
shapes are 4 ms apart while already differing 269x in db hits.

So db hits and the seek/scan distinction are the leading indicators, and they
belong on the span rather than in a benchmark script.

``summarize_profile`` flattens a Neo4j/DozerDB PROFILE tree; ``span_attributes``
renders it as OTel-style keys for ``rag.execute``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# A seek touches O(matching) rows; a scan touches O(label or graph). The
# distinction is the whole point of the gate, so the buckets have to be right —
# and two operators were in the wrong one.
#
# `NodeIndexScan` and `NodeIndexContainsScan` were classified as SEEKS. They are
# not: both read every entry of the index (a range scan and a substring scan
# respectively), so cost grows with the label, not with the match. Keeping them
# in the seek bucket made the grader gameable — the cheapest way for an LLM to
# turn an "unsargable" verdict green was to add `WHERE n.prop IS NOT NULL` or a
# `CONTAINS`, which produces exactly these operators and a full index scan. They
# now sit in their own bucket, cheaper than a label scan but still O(index).
SEEK_OPERATORS = (
    "NodeIndexSeek", "NodeUniqueIndexSeek", "NodeByIdSeek",
    "NodeByElementIdSeek",            # 5.x operator for elementId(n) = $x
    "DirectedRelationshipIndexSeek", "UndirectedRelationshipIndexSeek",
)
INDEX_SCAN_OPERATORS = (
    "NodeIndexScan", "NodeIndexContainsScan", "NodeIndexEndsWithScan",
)
# Full scans. The plural-label operators (`UnionNodeByLabelsScan`, note the `s`)
# are NOT substrings of `NodeByLabelScan`, so substring matching missed them and
# graded `(n:A|B)` as clean.
SCAN_OPERATORS = (
    "AllNodesScan", "NodeByLabelScan",
    "UnionNodeByLabelsScan", "IntersectionNodeByLabelsScan",
    "SubtractionNodeByLabelsScan",
    "RelationshipTypeScan",
    "DirectedAllRelationshipsScan", "UndirectedAllRelationshipsScan",
)
# Operators that are almost always an accident in generated Cypher and where the
# cost is not a scan the buckets above would catch. A CartesianProduct is two
# disconnected MATCH patterns — the single most common LLM Cypher error, and a
# plan of two index *seeks* joined by one still graded sargable=True. Eager
# (and EagerAggregation, caught by the same substring) fully materialises an
# intermediate result, which is the usual cause of a transaction-memory abort.
DANGER_OPERATORS = ("CartesianProduct", "Eager")


def summarize_plan(plan: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Flatten an EXPLAIN tree — the same signals, before the query runs.

    `PROFILE` executes the query to get real `dbHits`; `EXPLAIN` only compiles
    it and reports the optimiser's `EstimatedRows`. That difference is what
    makes a pre-execution gate possible at all: EXPLAIN costs nothing, so it can
    run on every query rather than on a sample, and the seek/scan distinction —
    the signal that actually predicts how a query behaves as the graph grows —
    is already decided at plan time.

    Verified against DozerDB 5.26.3: `EXPLAIN MATCH (n:X) WHERE n.name='x'`
    yields NodeByLabelScan, and dropping the label yields AllNodesScan, both
    without touching the data. Operator names arrive suffixed (`NodeByLabelScan
    @neo4j`), which the substring matching below already tolerates.

    `estimated_rows` is the optimiser's guess and is often wrong in absolute
    terms; it is carried for ordering candidates, never as a cost claim.
    """
    if not plan:
        return {"available": False, "source": "explain"}

    operators: List[str] = []
    estimated = 0.0

    def walk(node: Dict[str, Any]) -> None:
        nonlocal estimated
        operators.append(str(node.get("operatorType", "")))
        args = node.get("args") or {}
        try:
            estimated = max(estimated, float(args.get("EstimatedRows") or 0))
        except (TypeError, ValueError):
            pass
        for child in node.get("children", []) or []:
            walk(child)

    walk(plan)
    return _classify(operators, source="explain", estimated_rows=estimated)


def _classify(operators: List[str], *, source: str,
              estimated_rows: float = 0.0, db_hits: int = 0,
              rows: int = 0) -> Dict[str, Any]:
    """Shared operator classification for both EXPLAIN and PROFILE summaries."""
    scans = [op for op in operators if any(s in op for s in SCAN_OPERATORS)]
    seeks = [op for op in operators if any(s in op for s in SEEK_OPERATORS)]
    index_scans = [op for op in operators
                   if any(s in op for s in INDEX_SCAN_OPERATORS)]
    dangers = [op for op in operators if any(d in op for d in DANGER_OPERATORS)]
    # ORDER BY with no bounding LIMIT sorts the whole intermediate result; with
    # a Top the sort is bounded and cheap. Only the unbounded form is a danger.
    if any("Sort" in op for op in operators) and not any(
        "Top" in op for op in operators
    ):
        dangers = dangers + ["Sort(unbounded)"]

    summary = {
        "available": True,
        "source": source,
        "estimated_rows": estimated_rows,
        "operator_count": len(operators),
        "scans": scans,
        "seeks": seeks,
        "index_scans": index_scans,
        "dangers": dangers,
        # A real seek, no full scan, no index scan, and nothing on the danger
        # list. An index scan counts against it because it grows with the index;
        # a CartesianProduct counts because two seeks joined by one is the
        # classic "looks sargable, runs quadratic" plan.
        "sargable": bool(seeks) and not scans and not index_scans and not dangers,
    }
    if source == "profile":
        summary["db_hits"] = db_hits
        summary["rows"] = rows
    return summary


def repair_hint(summary: Dict[str, Any], ontology: Any = None) -> Optional[str]:
    """Turn a bad plan into an instruction the repair prompt can act on.

    The repair loop today sees only errors, so a query that runs and burns 6.6M
    db hits is treated as a success. This gives it the other half.

    The last line is the part worth having. Because the ontology declares which
    properties are unique, the hint can state that an index seek EXISTS rather
    than suggesting the model guess — the schema is the evidence, not the
    model's prior.
    """
    if not summary.get("available") or summary.get("sargable"):
        return None

    dangers = summary.get("dangers") or []
    scans = (summary.get("scans") or []) + (summary.get("index_scans") or [])
    if not dangers and not scans:
        return None

    lines = ["The previous query was valid but will not scale."]

    # A CartesianProduct is a different fault from a scan and needs a different
    # fix — connecting the patterns, not anchoring them — so it is named first
    # and explicitly, rather than folded into the generic scan advice.
    if dangers:
        lines.append(f"  costly operators: {', '.join(sorted(set(dangers))[:3])}")
        if any("Cartesian" in d for d in dangers):
            lines.append(
                "  A CartesianProduct means two MATCH patterns are not "
                "connected; join them with a relationship or a shared variable."
            )
        if any("Eager" in d for d in dangers):
            lines.append(
                "  An Eager operator materialises the whole intermediate "
                "result; avoid interleaving reads with writes or aggregations."
            )
        if any("Sort" in d for d in dangers):
            lines.append(
                "  An unbounded Sort orders the whole result; add a LIMIT so "
                "the sort is bounded."
            )
    if scans:
        lines.append(f"  scan operators: {', '.join(sorted(set(scans))[:3])}")
        lines.append(f"  estimated rows: {summary.get('estimated_rows', 0):.0f}")
        lines.append("  A scan grows with the graph; a seek does not.")

    # The index advice only helps a scan. If the sole problem is a
    # CartesianProduct, telling the model to anchor on a unique property is a
    # non-sequitur — the patterns are already anchored, they are just not joined.
    if not scans:
        return "\n".join(lines)

    indexed: List[str] = []
    for label, node in (getattr(ontology, "nodes", None) or {}).items():
        for prop, spec in (getattr(node, "properties", None) or {}).items():
            if getattr(spec, "unique", False):
                indexed.append(f"{label}.{prop}")
    if indexed:
        lines.append(
            "  The ontology declares these as unique, so an index seek is "
            f"available on: {', '.join(sorted(indexed)[:6])}."
        )
        lines.append("  Rewrite as an anchored lookup on one of them.")
    else:
        lines.append(
            "  The ontology declares no unique property, so no seek is "
            "available; narrow the match instead of anchoring it."
        )
    return "\n".join(lines)


def summarize_profile(profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Flatten a PROFILE tree into plan-quality signals.

    ``sargable`` is deliberately strict — a single scan anywhere in the plan means
    the query has a component that grows with the graph, which is the property we
    want to alert on even when the rest of the plan is indexed.
    """
    if not profile:
        return {"available": False}

    operators: List[str] = []
    db_hits = 0
    rows = 0
    cache_hits = 0
    cache_misses = 0
    worst_estimate_ratio = 0.0

    def walk(node: Dict[str, Any]) -> None:
        nonlocal db_hits, rows, cache_hits, cache_misses, worst_estimate_ratio
        operators.append(str(node.get("operatorType", "")))
        db_hits += int(node.get("dbHits", 0) or 0)
        rows += int(node.get("rows", 0) or 0)
        cache_hits += int(node.get("pageCacheHits", 0) or 0)
        cache_misses += int(node.get("pageCacheMisses", 0) or 0)

        # Estimated vs actual, per operator. This is the single best signal for
        # "the planner was wrong", which is a DIFFERENT root cause from "the
        # query was wrong" — and the two were indistinguishable before.
        estimated = (node.get("args") or {}).get("EstimatedRows")
        actual = node.get("rows")
        if isinstance(estimated, (int, float)) and isinstance(actual, (int, float)):
            hi, lo = max(float(estimated), float(actual)), min(float(estimated), float(actual))
            if lo >= 1.0:
                worst_estimate_ratio = max(worst_estimate_ratio, hi / lo)

        for child in node.get("children", []) or []:
            walk(child)

    walk(profile)
    cache_total = cache_hits + cache_misses
    summary = _classify(operators, source="profile", db_hits=db_hits, rows=rows)
    summary.update({
        # Summed across the whole tree, so this double-counts every pipeline
        # stage and is neither result size nor total work. Named for what it is.
        "intermediate_rows": rows,
        # Scale-free: raw db_hits is not comparable across questions.
        "db_hits_per_row": (db_hits / rows) if rows else float(db_hits),
        # Separates "the graph does not fit in the page cache" from "the query
        # is bad". Without it an infra problem attributes to retrieval quality.
        "page_cache_hit_ratio": (cache_hits / cache_total) if cache_total else None,
        "worst_estimate_ratio": round(worst_estimate_ratio, 2) or None,
    })
    return summary


def span_attributes(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Render a plan summary as span metadata.

    Kept low-cardinality: operator *names* are bounded by the planner's vocabulary,
    whereas the plan text is not, so only names and counts are emitted.
    """
    if not summary.get("available"):
        return {}
    # .get(), not [] — summarize_plan (EXPLAIN) has no db_hits or rows at all,
    # so indexing them raised KeyError for the caller this function exists to
    # serve. Worse, record_metrics used `.get("db_hits") or 0` and silently
    # recorded ZERO db hits for every explained plan, which is data corruption
    # rather than a missing signal. EXPLAIN summaries now omit the field.
    attrs: Dict[str, Any] = {
        "db.plan.operator_count": summary.get("operator_count", 0),
        "db.plan.sargable": summary.get("sargable", False),
        "db.plan.source": "profile" if "db_hits" in summary else "explain",
    }
    for key, attr in (("db_hits", "db.plan.db_hits"),
                      ("intermediate_rows", "db.plan.intermediate_rows"),
                      ("db_hits_per_row", "db.plan.db_hits_per_row"),
                      ("page_cache_hit_ratio", "db.plan.page_cache_hit_ratio"),
                      ("worst_estimate_ratio", "db.plan.worst_estimate_ratio"),
                      ("estimated_rows", "db.plan.estimated_rows")):
        value = summary.get(key)
        if value is not None:
            attrs[attr] = value
    if summary.get("scans"):
        attrs["db.plan.scan_operators"] = ",".join(sorted(set(summary["scans"])))
    if summary.get("seeks"):
        attrs["db.plan.seek_operators"] = ",".join(sorted(set(summary["seeks"])))
    return attrs


def record_metrics(summary: Dict[str, Any], *, route: Optional[str] = None,
                   declined: Optional[str] = None) -> None:
    """Emit the aggregate counterparts of the span attributes.

    Spans answer "why was this request slow"; they cannot answer "is the sargable
    rate falling this hour", "what share of questions does each arm handle", or "is
    the db-hit distribution moving with scale". Those are the questions that matter
    when the graph grows, so the same signals are recorded as metrics — traces and
    metrics being complementary rather than substitutes (ADR-0144 §6).

    Label cardinality is kept bounded: booleans, a route name, and a coarse
    rejection reason rather than the message text.
    """
    from ..metrics import get_metrics

    metrics = get_metrics()

    if summary.get("available"):
        metrics.add(
            "seocho.query.plan.count",
            1,
            attributes={"sargable": str(bool(summary.get("sargable"))).lower()},
        )
        # A counter carrying db hits lets a rate() show cost per query over time
        # without needing a histogram on every backend.
        # Only from a PROFILE. An EXPLAIN summary has no db_hits, and
        # `or 0` recorded a real zero for it — indistinguishable from a
        # query that genuinely touched nothing, and wrong in the same
        # direction every time.
        if summary.get("db_hits") is not None:
            metrics.add("seocho.query.db_hits.count", float(summary["db_hits"]))
        for operator in sorted(set(summary.get("scans") or []))[:3]:
            metrics.add("seocho.query.scan.count", 1, attributes={"operator": operator})
    if route:
        metrics.add("seocho.query.plan_route.count", 1, attributes={"route": route})
    if declined:
        # Only the exception class, so the label set stays small.
        reason = declined.split(":", 1)[0].strip()[:60] or "unknown"
        metrics.add("seocho.query.generation_declined.count", 1, attributes={"reason": reason})


def slot_attributes(intent_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Render the resolved intent slots for ``rag.compile_cypher``.

    Which relationship and endpoint labels a question resolved to is the thing that
    degrades as a schema gains types, and it is invisible in an answer. Recording it
    separates "the model picked the wrong relationship" from "the template dropped
    the right one" — a distinction that outcome-only signals collapse.
    """
    data = intent_data or {}
    attrs: Dict[str, Any] = {}
    for key, attr in (
        ("intent", "rag.intent"),
        ("anchor_label", "rag.slot.anchor_label"),
        ("target_label", "rag.slot.target_label"),
        ("relationship_type", "rag.slot.relationship_type"),
    ):
        value = str(data.get(key, "") or "")
        if value:
            attrs[attr] = value
    # The value itself can be user text; presence is the low-cardinality signal.
    attrs["rag.slot.anchor_entity_present"] = bool(str(data.get("anchor_entity", "") or "").strip())
    return attrs
