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

# A scan means cost grows with the data; a seek means it does not.
SCAN_OPERATORS = ("AllNodesScan", "NodeByLabelScan", "RelationshipTypeScan")
SEEK_OPERATORS = (
    "NodeIndexSeek", "NodeUniqueIndexSeek", "NodeIndexContainsScan",
    "NodeByIdSeek", "NodeIndexScan",
)


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

    def walk(node: Dict[str, Any]) -> None:
        nonlocal db_hits, rows
        operators.append(str(node.get("operatorType", "")))
        db_hits += int(node.get("dbHits", 0) or 0)
        rows += int(node.get("rows", 0) or 0)
        for child in node.get("children", []) or []:
            walk(child)

    walk(profile)
    scans = [op for op in operators if any(s in op for s in SCAN_OPERATORS)]
    seeks = [op for op in operators if any(s in op for s in SEEK_OPERATORS)]
    return {
        "available": True,
        "db_hits": db_hits,
        "rows": rows,
        "operator_count": len(operators),
        "scans": scans,
        "seeks": seeks,
        "sargable": bool(seeks) and not scans,
    }


def span_attributes(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Render a plan summary as span metadata.

    Kept low-cardinality: operator *names* are bounded by the planner's vocabulary,
    whereas the plan text is not, so only names and counts are emitted.
    """
    if not summary.get("available"):
        return {}
    attrs: Dict[str, Any] = {
        "db.plan.db_hits": summary["db_hits"],
        "db.plan.rows": summary["rows"],
        "db.plan.operator_count": summary["operator_count"],
        "db.plan.sargable": summary["sargable"],
    }
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
    from ..tracing import record_metric

    if summary.get("available"):
        record_metric(
            "seocho_query_plan",
            1,
            attributes={"sargable": str(bool(summary.get("sargable"))).lower()},
        )
        # A counter carrying db hits lets a rate() show cost per query over time
        # without needing a histogram on every backend.
        record_metric("seocho_query_db_hits", float(summary.get("db_hits") or 0))
        for operator in sorted(set(summary.get("scans") or []))[:3]:
            record_metric("seocho_query_scan", 1, attributes={"operator": operator})
    if route:
        record_metric("seocho_query_plan_route", 1, attributes={"route": route})
    if declined:
        # Only the exception class, so the label set stays small.
        reason = declined.split(":", 1)[0].strip()[:60] or "unknown"
        record_metric("seocho_query_generation_declined", 1, attributes={"reason": reason})


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
