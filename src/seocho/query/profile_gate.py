"""Metric-threshold profile gate for text2cypher (seocho-ia4).

text2cypher is the LAST place to optimize a query before it hits the GDBMS, and its
cost varies by orders of magnitude. The AIsummit26 harness already gates on a single
signal — a 2-second wall-clock probe — and merely *unlocks* a hint tool. This
generalizes that into the loop hadry wants: a **metric-threshold detector** over the
real plan signals (db_hits, operators, estimated rows, elapsed) that, on a breach,
(a) DETECTS which threshold broke, (b) PROFILES the plan (the offending operators), and
(c) emits an actionable IMPROVE directive the agent hands back to itself — auto-driven,
not just unlocked. Deterministic and DB-free: it consumes EXPLAIN/PROFILE metrics the
executor already collects, so it is testable without a live graph.

Flow: generate Cypher -> EXPLAIN/PROFILE -> evaluate_plan(metrics, thresholds) ->
if breached, feed decision.improve_directive back into the repair turn -> regenerate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# operators that signal an unindexed / blow-up plan (Neo4j/DozerDB planner vocabulary)
_FULL_SCAN_OPS = {"AllNodesScan", "NodeByLabelScan"}
_BLOWUP_OPS = {"CartesianProduct"}
_EXPAND_ALL = {"Expand(All)", "VarLengthExpand(All)"}


@dataclass
class PlanMetrics:
    """What EXPLAIN/PROFILE gives us for one candidate Cypher."""
    db_hits: int = 0
    estimated_rows: float = 0.0
    elapsed_ms: float = 0.0
    operators: List[str] = field(default_factory=list)   # operator-type histogram (flat list ok)
    rows_returned: int = 0
    used_index: bool = False


@dataclass
class ProfileThresholds:
    """The 'optimization needed' signal. Any breach triggers detect->profile->improve."""
    max_db_hits: int = 200_000
    max_estimated_rows: float = 100_000.0
    slo_ms: float = 1_000.0
    max_rows_returned: int = 1_000
    forbid_full_scan: bool = True
    forbid_cartesian: bool = True


@dataclass
class GateDecision:
    breached: bool
    reasons: List[str] = field(default_factory=list)
    profile: Dict[str, object] = field(default_factory=dict)
    improve_directive: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {"breached": self.breached, "reasons": self.reasons,
                "profile": self.profile, "improve_directive": self.improve_directive}


def evaluate_plan(metrics: PlanMetrics, thresholds: Optional[ProfileThresholds] = None) -> GateDecision:
    """Detect threshold breaches, profile the plan, and emit an improve directive."""
    t = thresholds or ProfileThresholds()
    reasons: List[str] = []
    fixes: List[str] = []
    ops = set(metrics.operators or [])

    full_scans = sorted(ops & _FULL_SCAN_OPS)
    if t.forbid_full_scan and full_scans and not metrics.used_index:
        reasons.append(f"full_scan:{','.join(full_scans)}")
        fixes.append("Start the match from an INDEXED property lookup (an anchor node "
                     "bound by a unique/indexed key) instead of a full label/node scan.")
    if t.forbid_cartesian and (ops & _BLOWUP_OPS):
        reasons.append("cartesian_product")
        fixes.append("Remove the cartesian product: connect the disjoint MATCH patterns "
                     "with an explicit relationship or a shared bound variable.")
    if metrics.db_hits > t.max_db_hits:
        reasons.append(f"db_hits {metrics.db_hits} > {t.max_db_hits}")
        fixes.append("The plan touches too many records; narrow the anchor (add a more "
                     "selective WHERE on an indexed property) before expanding.")
    if metrics.estimated_rows > t.max_estimated_rows:
        reasons.append(f"estimated_rows {metrics.estimated_rows:.0f} > {t.max_estimated_rows:.0f}")
        fixes.append("Estimated cardinality is huge; add a LIMIT and/or a more selective "
                     "filter, or aggregate instead of returning raw rows.")
    if metrics.elapsed_ms > t.slo_ms:
        reasons.append(f"elapsed_ms {metrics.elapsed_ms:.0f} > SLO {t.slo_ms:.0f}")
        fixes.append("Exceeds the latency SLO; prefer an indexed anchor and bounded "
                     "expansion (cap variable-length paths).")
    if metrics.rows_returned > t.max_rows_returned:
        reasons.append(f"rows_returned {metrics.rows_returned} > {t.max_rows_returned}")
        fixes.append("Returns too many rows for a context window; add LIMIT or aggregate.")

    breached = bool(reasons)
    profile = {
        "db_hits": metrics.db_hits,
        "estimated_rows": metrics.estimated_rows,
        "elapsed_ms": metrics.elapsed_ms,
        "rows_returned": metrics.rows_returned,
        "used_index": metrics.used_index,
        "full_scan_ops": full_scans,
        "expand_all_ops": sorted(ops & _EXPAND_ALL),
    }
    directive = ""
    if breached:
        directive = ("Query plan is over budget — revise the Cypher. Issues: "
                     + "; ".join(reasons) + ". Fixes: " + " ".join(fixes))
    return GateDecision(breached=breached, reasons=reasons, profile=profile,
                        improve_directive=directive)


def parse_explain_metrics(rows: object, *, elapsed_ms: float = 0.0) -> PlanMetrics:
    """Best-effort extraction of PlanMetrics from a DozerDB/neo4j EXPLAIN/PROFILE
    summary. Tolerant of shapes; the executor passes what it has (operators list,
    db-hits, estimated rows). Kept dependency-free / duck-typed."""
    db_hits = 0
    est = 0.0
    ops: List[str] = []
    used_index = False

    def _walk(node):
        nonlocal db_hits, est, used_index
        if isinstance(node, dict):
            op = node.get("operatorType") or node.get("operator") or node.get("name")
            if op:
                ops.append(str(op))
                if "index" in str(op).lower():
                    used_index = True
            args = node.get("arguments", node)
            if isinstance(args, dict):
                db_hits += int(args.get("DbHits", args.get("dbHits", 0)) or 0)
                est = max(est, float(args.get("EstimatedRows", args.get("estimatedRows", 0)) or 0))
            for ch in (node.get("children") or []):
                _walk(ch)

    _walk(rows)
    return PlanMetrics(db_hits=db_hits, estimated_rows=est, elapsed_ms=elapsed_ms,
                       operators=ops, used_index=used_index)
