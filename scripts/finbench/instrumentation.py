#!/usr/bin/env python3
"""Paradigm-agnostic pipeline instrumentation for agent query experiments.

Outcome-only scoring (accuracy, latency) cannot say *where* an agent failed, and
in this experiment it actively misled us: three models "failed" a scenario whose
real cause was a middleware template discarding correct slots, while a different
failure was a genuine model slot-fill error. Both looked like "0%".

This module decomposes one question into the stages where agents actually differ:

    S1 intent     did it classify the question shape correctly?
    S2 slots      did it pick the right relationship/table out of many?
                  (plus: did the guardrail have to repair the direction?)
    S3 validity   did the generated query execute and return rows?
    S4 plan       is the query sargable — index seek vs full scan, db hits?
    S5 answer     does it match gold — and does it match *only* gold (precision)?

S2 is the metric that matters for schema cardinality: a model can produce the
right answer with a terrible query (scan everything, let the LLM filter), so
slot-fill isolates "did it know which relationship to traverse".

The same decomposition applies to a relational arm (text2sql): S2 becomes table
and join selection, S4 uses EXPLAIN ANALYZE instead of PROFILE. Keeping the
stages paradigm-neutral is what makes the graph-vs-relational comparison fair.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional, Sequence

# Items an answer may enumerate: channel-style CODES, or 4+ digit identifiers.
# Used to measure precision — an answer listing all twelve channels "contains"
# the three gold ones but is not the right answer.
_ITEM_RE = re.compile(r"\b(?:[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+|\d{4,})\b")


def gold_matches(answer: str, gold_token: str) -> bool:
    """A gold token may offer '|'-separated alternatives (code or human label)."""
    low = (answer or "").lower()
    return any(alt.strip().lower() in low for alt in str(gold_token).split("|") if alt.strip())


def score_answer(answer: str, gold: Sequence[str]) -> Dict[str, Any]:
    """Recall against gold plus precision over enumerated items.

    Recall alone rewards over-answering: a model that dumps every channel passes a
    containment check. Precision counts how many of the items it actually
    enumerated were expected, which is what separates "knew the answer" from
    "returned a superset and let the reader filter".
    """
    answer = answer or ""
    recall_hits = [g for g in gold if gold_matches(answer, g)]
    recall = len(recall_hits) / len(gold) if gold else 0.0

    found = set(_ITEM_RE.findall(answer.upper()))
    expected_items = set()
    for token in gold:
        for alt in str(token).split("|"):
            expected_items.update(_ITEM_RE.findall(alt.strip().upper()))
    # Only meaningful when the answer enumerates items and gold names some.
    if found and expected_items:
        precision: Optional[float] = len(found & expected_items) / len(found)
        superset = bool(found - expected_items) and expected_items <= found
    else:
        precision, superset = None, False

    return {
        "recall": recall,
        "correct": recall == 1.0,
        "precision": precision,
        "superset": superset,
        "exact": recall == 1.0 and (precision is None or precision == 1.0),
        "items_found": sorted(found)[:20],
    }


# Operators that mean "we scanned instead of seeking" — the sargability signal.
_SCAN_OPERATORS = ("AllNodesScan", "NodeByLabelScan", "SEQ_SCAN", "TableScan")
_SEEK_OPERATORS = ("NodeIndexSeek", "NodeUniqueIndexSeek", "NodeByIdSeek", "INDEX_SCAN")


def summarize_plan(profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Flatten a Neo4j/DozerDB PROFILE tree into plan-quality signals."""
    if not profile:
        return {"available": False}

    operators: list[str] = []
    total_hits = 0
    total_rows = 0

    def walk(node: Dict[str, Any]) -> None:
        nonlocal total_hits, total_rows
        operators.append(str(node.get("operatorType", "")))
        total_hits += int(node.get("dbHits", 0) or 0)
        total_rows += int(node.get("rows", 0) or 0)
        for child in node.get("children", []) or []:
            walk(child)

    walk(profile)
    scans = [op for op in operators if any(s in op for s in _SCAN_OPERATORS)]
    seeks = [op for op in operators if any(s in op for s in _SEEK_OPERATORS)]
    return {
        "available": True,
        "db_hits": total_hits,
        "rows": total_rows,
        "operators": operators,
        "scans": scans,
        "seeks": seeks,
        # Sargable = the identifying predicate resolved through an index rather
        # than a scan. This is what decides whether cost grows with graph size.
        "sargable": bool(seeks) and not scans,
    }


def profile_cypher(driver: Any, cypher: str, params: Dict[str, Any], database: str) -> Dict[str, Any]:
    """Re-run a generated query under PROFILE to capture its plan quality."""
    if not cypher:
        return {"available": False, "reason": "no_cypher"}
    try:
        with driver.session(database=database) as session:
            result = session.run("PROFILE " + cypher, **(params or {}))
            result.consume  # noqa: B018 - force lazy fetch below
            rows = [r.data() for r in result]
            summary = result.consume()
        plan = summarize_plan(summary.profile)
        plan["result_rows"] = len(rows)
        return plan
    except Exception as exc:  # a plan probe must never fail the measurement
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"[:200]}


def slot_fingerprint(intent_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The S1/S2 slots, normalized across paradigms."""
    data = intent_data or {}
    return {
        "intent": str(data.get("intent", "") or ""),
        "anchor_label": str(data.get("anchor_label", "") or ""),
        "target_label": str(data.get("target_label", "") or ""),
        "relationship_type": str(data.get("relationship_type", "") or ""),
        "anchor_entity": str(data.get("anchor_entity", "") or ""),
        "anchor_entity_present": bool(str(data.get("anchor_entity", "") or "").strip()),
    }


def detect_orientation_repair(ontology: Any, intent_data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Would the direction guardrail have to repair these slots?

    Reconstructed from the recorded slots rather than read out of engine internals,
    so measuring the guardrail's contribution needs no plumbing through the core.
    Faithful because it calls the same ``_orient_relationship`` the builder uses.
    """
    from seocho.query.cypher_builder import CypherBuilder

    slots = slot_fingerprint(intent_data)
    rel = slots["relationship_type"]
    if not rel:
        return None
    builder = CypherBuilder(ontology)
    builder._orient_relationship(rel, slots["anchor_label"], slots["target_label"])
    return getattr(builder, "last_orientation_repair", None)


def expected_slots(case: Dict[str, Any]) -> Dict[str, str]:
    """Per-case expected slots, when the case declares them (``expect_slots``)."""
    return {k: str(v) for k, v in (case.get("expect_slots") or {}).items()}


def score_slots(actual: Dict[str, Any], expected: Dict[str, str]) -> Dict[str, Any]:
    """S2: did the agent fill the slots the question implies?

    Only the slots a case explicitly declares are graded, so cases that do not
    pin an expectation contribute nothing rather than a false signal.
    """
    if not expected:
        return {"graded": False}
    hits = {k: (actual.get(k, "") == v) for k, v in expected.items()}
    return {
        "graded": True,
        "per_slot": hits,
        "slot_fill_rate": sum(hits.values()) / len(hits),
        "all_correct": all(hits.values()),
    }


def fingerprint(
    *,
    case: Dict[str, Any],
    answer: str,
    metadata: Dict[str, Any],
    orientation_repair: Optional[Dict[str, Any]] = None,
    plan: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
    latency_ms: float = 0.0,
) -> Dict[str, Any]:
    """Assemble the per-case stage-wise record."""
    metadata = metadata or {}
    slots = slot_fingerprint(metadata.get("intent_data"))
    support = dict(metadata.get("support_assessment") or {})
    answer_score = score_answer(answer, case.get("gold") or [])
    latency_breakdown = dict(metadata.get("latency_breakdown_ms") or {})

    return {
        "id": case.get("id"),
        "scenario": case.get("scenario", ""),
        # Complexity tier travels with the record so results can be sliced by how
        # much schema the question forced the agent to resolve.
        "tier": case.get("tier", ""),
        "latency_ms": latency_ms,
        "error": error,
        # S1 / S2
        "s1_intent": slots["intent"],
        "s2_slots": slots,
        "s2_score": score_slots(slots, expected_slots(case)),
        # The guardrail's contribution, counted rather than asserted.
        "guardrail_repaired_direction": bool(orientation_repair),
        "guardrail_repair": orientation_repair,
        # S3
        "s3_executed": error is None,
        "s3_result_count": metadata.get("result_count"),
        "s3_support_status": str(support.get("status", "") or ""),
        # S4
        "s4_plan": plan or {"available": False},
        # S5
        "s5_answer": answer_score,
        "correct": answer_score["correct"],
        # cost / effort
        "reasoning_attempts": metadata.get("reasoning_attempts"),
        "token_usage": dict(metadata.get("token_usage") or {}),
        "latency_breakdown_ms": latency_breakdown,
        # Where the time actually went: LLM planning vs graph execution.
        "engine_ms": latency_breakdown.get("execute_ms"),
        "llm_ms": (latency_breakdown.get("plan_ms") or 0.0)
        + (latency_breakdown.get("generation_ms") or 0.0),
        "cypher": (metadata.get("cypher") or "")[:1200],
        "answer": (answer or "")[:400],
    }


def aggregate(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Stage-wise pass rates over a set of per-case records."""
    rows = list(records)
    if not rows:
        return {}
    n = len(rows)

    def rate(pred) -> float:
        return sum(1 for r in rows if pred(r)) / n

    graded = [r for r in rows if r["s2_score"].get("graded")]
    planned = [r for r in rows if r["s4_plan"].get("available")]
    precise = [r for r in rows if r["s5_answer"].get("precision") is not None]

    return {
        "cases": n,
        "s2_slot_fill_rate": (
            sum(r["s2_score"]["slot_fill_rate"] for r in graded) / len(graded) if graded else None
        ),
        "s3_executed_rate": rate(lambda r: r["s3_executed"]),
        "s3_supported_rate": rate(lambda r: r["s3_support_status"] == "supported"),
        "s4_sargable_rate": (
            sum(1 for r in planned if r["s4_plan"].get("sargable")) / len(planned) if planned else None
        ),
        "s4_db_hits_total": sum(int(r["s4_plan"].get("db_hits", 0) or 0) for r in planned),
        "s5_accuracy": rate(lambda r: r["correct"]),
        "s5_exact_rate": rate(lambda r: r["s5_answer"].get("exact")),
        "s5_superset_rate": (
            sum(1 for r in precise if r["s5_answer"].get("superset")) / len(precise) if precise else None
        ),
        "guardrail_repair_rate": rate(lambda r: r["guardrail_repaired_direction"]),
        "avg_reasoning_attempts": (
            sum(int(r.get("reasoning_attempts") or 0) for r in rows) / n
        ),
        "engine_ms_total": sum(float(r.get("engine_ms") or 0.0) for r in rows),
        "llm_ms_total": sum(float(r.get("llm_ms") or 0.0) for r in rows),
    }
