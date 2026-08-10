#!/usr/bin/env python3
"""Decide, before executing, whether a question can be answered within budget.

The experiment's thesis is that a transaction graph's *distribution* — not its size — is
what reaches an agent's workload. The evidence is blunt: raising volume a thousandfold with
the structure held fixed changed nothing, while raising the degree tail turned a 45 ms
aggregate into a timeout on the same question and the same code.

What decides the outcome, measured on one hub anchor of degree 158,315:

    DISTINCT ... LIMIT     163 db hits      2.8 ms
    count(DISTINCT ...)    —                > 30 s, no answer

Same node, same data, same hop count. **The operation class decides, not the data.** So an
agent that reasons only about *what* is being asked, and never about *what shape of work*
the answer requires, meets that wall at the baseline. This module is the missing step: a
descriptor computed before execution, and a verdict derived from it.

Four inputs, each of them something an earlier measurement showed to be load-bearing:

``predicted_rows``
    Estimated intermediate result size for the intended plan. Uses L2 — the sum of
    out-degrees over the anchor's out-edges — which predicted measured db hits at ~2x
    across five orders of magnitude (5 → 51, 3,545 → 7,317, 190,788 → 382,596), computed
    offline so it costs no round trip and can be known before the query is planned.

``terminable``
    Whether the answer permits stopping early. On a hub this is the single strongest
    determinant, and it is a property of the *question*, not of the data.

``bound_changes_meaning``
    Whether truncating would produce an approximation or a wrong answer. "List up to ten
    counterparties" tolerates a bound; "how many counterparties" does not — a bounded count
    is not an approximate count, it is incorrect. Conflating these is how a guardrail that
    looks protective produces confident nonsense.

``engine_supports``
    What the target can do cheaply. FinBench's own mitigation — per-hop truncation ordered
    by timestamp — cost **70,000x more** than no mitigation when expressed as user-level
    Cypher (11,502,593 db hits against 163), because ORDER BY is a pipeline breaker that
    destroys the laziness doing the actual protecting. A bound the engine cannot serve is
    an amplifier, so "which engine offers this operation" is a first-class input rather
    than an afterthought.

Deliberately **not** a query rewriter. The 70,000x result is the reason: emitting cleverer
Cypher is the failure mode, not the fix. The verdict is one of four actions, and two of
them decline to answer exhaustively.

Anchor degree is deliberately *not* an input. It is non-monotonic in cost — a degree-6
anchor measured 158,487 db hits while a degree-73 anchor measured 3,876 — because
preferential attachment makes a low-degree node's few neighbours disproportionately likely
to be hubs. Cost follows the neighbourhood, not the node, which is the same reason LDBC
curates parameters by intermediate result size.

Usage:
    python scripts/finbench/workload_gate.py --src outputs/finbench/sf1000-real \
        --cases examples/finbench/cases_hub.json --budget-rows 200000
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Measured relationship between the offline estimate and engine work: db hits landed at
# about twice L2 consistently from 5 to 190,788, so the estimate converts rather than
# merely correlating.
DB_HITS_PER_L2 = 2.0

# Question shapes. `terminable` says an answer can stop early; `bound_safe` says a bound
# yields an approximation rather than a wrong answer. They are not the same flag: a ranked
# top-K is terminable *and* bound-safe, while an exhaustive count is neither.
@dataclass(frozen=True)
class OperationClass:
    name: str
    terminable: bool
    bound_safe: bool
    # Which engine family serves this shape cheaply. Left as a set of capability names
    # rather than product names so the descriptor stays about operations.
    needs: tuple = ()


OPERATIONS = {
    "lookup": OperationClass("lookup", terminable=True, bound_safe=True,
                             needs=("index_seek",)),
    "bounded_list": OperationClass("bounded_list", terminable=True, bound_safe=True,
                                   needs=("index_seek", "lazy_limit")),
    "exhaustive_aggregate": OperationClass("exhaustive_aggregate", terminable=False,
                                           bound_safe=False,
                                           needs=("index_seek", "full_expansion")),
    "ranked_topk": OperationClass("ranked_topk", terminable=True, bound_safe=True,
                                  needs=("ordered_topk_expansion",)),
    "unanchored_motif": OperationClass("unanchored_motif", terminable=False,
                                       bound_safe=False,
                                       needs=("set_oriented_join",)),
}

# What the engines actually offer, as measured in this experiment rather than as
# advertised. `ordered_topk_expansion` is absent from the Cypher arm precisely because the
# ORDER BY rewrite measured 70,000x worse than no bound at all.
ENGINE_CAPABILITIES = {
    "graph_oltp": {"index_seek", "lazy_limit", "full_expansion"},
    "columnar_olap": {"set_oriented_join", "full_expansion"},
}

_AGG_RE = re.compile(r"\bhow many\b|\bcount\b|\btotal number\b|\bsum of\b", re.I)
_LIST_RE = re.compile(r"\blist\b|\bwhich accounts\b|\bname the\b|\bup to \d+\b", re.I)
_TOPK_RE = re.compile(r"\btop \d+\b|\blargest\b|\bmost recent\b|\bhighest\b", re.I)
_MOTIF_RE = re.compile(r"\bcycle\b|\bring\b|\bloop\b|\bmotif\b|\bpattern\b", re.I)
# Anchor extraction. The named form comes first because a bare digit-run heuristic gets
# this wrong in both directions: requiring 4+ digits missed "account number 0" — the single
# most expensive anchor in the dataset, L2 51,447,907 — and classified an anchored question
# as unanchored, while accepting any digit-run would capture "within 2 hops" and "up to 10".
_NAMED_ANCHOR_RE = re.compile(r"account\s+number\s+(\d+)", re.I)
_ANCHOR_RE = re.compile(r"\b(\d{4,})\b")


@dataclass
class Descriptor:
    question: str
    operation: str
    anchor: Optional[int]
    hops: int
    predicted_l2: Optional[int]
    predicted_db_hits: Optional[float]
    terminable: bool
    bound_safe: bool
    engine: str
    unsupported_capabilities: List[str] = field(default_factory=list)


@dataclass
class Verdict:
    action: str          # execute | execute_bounded | approximate | decline
    reason: str
    descriptor: Descriptor


def classify(question: str, hops: int) -> str:
    """Name the shape of work the answer requires.

    Ordering matters: an unanchored motif question is a motif question even when it is
    phrased as a count, because the absence of an anchor is what makes it expensive.
    """
    if _MOTIF_RE.search(question) and not _ANCHOR_RE.search(question):
        return "unanchored_motif"
    if _TOPK_RE.search(question):
        return "ranked_topk"
    if _AGG_RE.search(question):
        return "exhaustive_aggregate"
    if _LIST_RE.search(question):
        return "bounded_list"
    return "lookup"


def extract_anchor(question: str) -> Optional[int]:
    m = _NAMED_ANCHOR_RE.search(question) or _ANCHOR_RE.search(question)
    return int(m.group(1)) if m else None


class CostModel:
    """Offline neighbourhood-size estimates, read from the snapshot.

    Built once per dataset. The point of computing this from Parquet rather than from the
    serving database is that a gate has to run *before* the query, and must not depend on
    the engine whose cost it is deciding about.
    """

    def __init__(self, src: Path) -> None:
        import duckdb

        self.src = src
        self.con = duckdb.connect()
        self.con.execute("SET memory_limit='8GB'")
        transfer = str(src / "edges" / "transfer.parquet")
        self.con.execute(f"CREATE VIEW t AS SELECT * FROM '{transfer}'")
        self.con.execute(
            "CREATE TABLE outdeg AS SELECT src AS id, count(*) AS deg FROM t GROUP BY src")
        self.con.execute(
            """CREATE TABLE l2 AS
               SELECT t.src AS id, count(*) AS l1, sum(coalesce(o.deg,0)) AS l2
               FROM t LEFT JOIN outdeg o ON o.id = t.dst GROUP BY t.src""")
        row = self.con.execute("SELECT max(l2), quantile_disc(l2, 0.5) FROM l2").fetchone()
        self.max_l2, self.median_l2 = int(row[0]), int(row[1])

    def estimate(self, anchor: Optional[int], hops: int) -> Optional[int]:
        if anchor is None:
            # No anchor means no seek and no local estimate: the cost is a property of the
            # whole graph, which is exactly why unanchored motif questions are the hard
            # case rather than a harder version of an easy one.
            return None
        row = self.con.execute("SELECT l1, l2 FROM l2 WHERE id = ?", [anchor]).fetchone()
        if row is None:
            return 0
        l1, l2 = int(row[0]), int(row[1])
        if hops <= 1:
            return l1
        if hops == 2:
            return l2
        # Beyond two hops, extrapolate with the observed branching factor. Marked as an
        # extrapolation rather than a measurement because it is one.
        branch = (l2 / l1) if l1 else 1.0
        return int(l2 * (branch ** (hops - 2)))


def evaluate(question: str, *, hops: int, model: Optional[CostModel],
             budget_rows: int, engine: str = "graph_oltp") -> Verdict:
    op_name = classify(question, hops)
    op = OPERATIONS[op_name]
    anchor = extract_anchor(question)
    l2 = model.estimate(anchor, hops) if model else None
    predicted = None if l2 is None else l2 * DB_HITS_PER_L2

    caps = ENGINE_CAPABILITIES.get(engine, set())
    missing = [c for c in op.needs if c not in caps]

    d = Descriptor(question=question, operation=op_name, anchor=anchor, hops=hops,
                   predicted_l2=l2, predicted_db_hits=predicted,
                   terminable=op.terminable, bound_safe=op.bound_safe,
                   engine=engine, unsupported_capabilities=missing)

    # An operation the engine cannot serve cheaply is the 70,000x case: expressing it
    # anyway is worse than not trying. Routing elsewhere is the only sound answer.
    if missing:
        return Verdict("decline",
                       f"{engine} lacks {', '.join(missing)}; expressing this shape anyway "
                       f"is the truncation-as-Cypher case that measured 70,000x worse than "
                       f"no bound. Route to an engine that offers it.", d)

    # No anchor and a non-terminable shape: cost is global, and a bound would silently
    # change the answer from "the suspicious rings" to "ten arbitrary rings".
    if l2 is None and not op.terminable:
        return Verdict("approximate",
                       "unanchored and not early-terminable, so exhaustive evaluation is "
                       "unbounded; answer from a sample or a precomputed summary and say "
                       "so — a bounded answer here is not an approximation of the question "
                       "asked", d)

    if predicted is not None and predicted <= budget_rows:
        return Verdict("execute",
                       f"predicted ~{predicted:,.0f} db hits within budget "
                       f"{budget_rows:,}", d)

    if op.terminable and op.bound_safe:
        return Verdict("execute_bounded",
                       f"predicted ~{predicted:,.0f} db hits exceeds budget "
                       f"{budget_rows:,}, but the answer can stop early — execute with an "
                       f"explicit row cap and abort the stream once satisfied", d)

    return Verdict("approximate",
                   f"predicted ~{predicted:,.0f} db hits exceeds budget {budget_rows:,} "
                   f"and the shape cannot stop early; a bound would make the answer wrong "
                   f"rather than approximate, so answer approximately and label it", d)


# Canonical shape per question type. The gate reasons about shapes, so validation has to
# execute the shape it reasoned about — scoring against whatever an LLM happened to emit
# would measure the model, not the gate.
SHAPES = {
    "one_hop_aggregate":
        "MATCH (a:Account {acct_no:$n})-[:TRANSFER]->(b:Account) "
        "RETURN count(DISTINCT b) AS v",
    "two_hop_aggregate":
        "MATCH (a:Account {acct_no:$n})-[:TRANSFER]->(:Account)-[:TRANSFER]->(c:Account) "
        "RETURN count(DISTINCT c) AS v",
    "one_hop_list":
        "MATCH (a:Account {acct_no:$n})-[:TRANSFER]->(b:Account) "
        "RETURN DISTINCT b.acct_no AS v LIMIT 10",
}


def _db_hits(plan: Dict[str, Any]) -> int:
    return int(plan.get("dbHits", 0) or 0) + sum(
        _db_hits(c) for c in plan.get("children", []) or [])


def validate(results: List[Dict[str, Any]], cases: List[Dict[str, Any]], *,
             uri: str, user: str, password: str, database: str,
             timeout_s: float, budget_rows: int) -> Dict[str, Any]:
    """Execute each case and compare the outcome against the gate's verdict.

    The gate is only worth having if its verdicts track reality, and the two error
    directions are not symmetric. A **false clear** — cleared, then timed out — is the
    failure the gate exists to prevent. A **false flag** — declined or approximated when
    the query would have returned cheaply — costs answer quality for safety, which is a
    tuning question rather than a broken premise.
    """
    from neo4j import GraphDatabase
    from neo4j.exceptions import Neo4jError

    by_id = {c.get("id"): c for c in cases}
    driver = GraphDatabase.driver(uri, auth=(user, password))
    matrix = {"true_clear": 0, "false_clear": 0, "true_flag": 0, "false_flag": 0}
    try:
        for r in results:
            case = by_id.get(r["id"], {})
            shape = SHAPES.get(case.get("reasoning_type"))
            anchor = case.get("anchor_account")
            if shape is None or anchor is None:
                r["actual"] = "no_shape"
                continue
            with driver.session(database=database) as session:
                tx = session.begin_transaction(timeout=timeout_s)
                try:
                    res = tx.run("PROFILE " + shape, n=anchor)
                    list(res)
                    summary = res.consume()
                    tx.commit()
                    hits = _db_hits(summary.profile or {})
                    r["actual_db_hits"] = hits
                    r["actual"] = "returned"
                    r["within_budget"] = hits <= budget_rows
                    pd = r.get("predicted_db_hits")
                    r["prediction_ratio"] = round(hits / pd, 3) if pd else None
                except Neo4jError as exc:
                    tx.close()
                    r["actual_db_hits"] = None
                    r["actual"] = "timeout"
                    r["error"] = exc.code
                    r["within_budget"] = False

            cleared = r["action"] in ("execute", "execute_bounded")
            ok = r.get("within_budget", False)
            key = ("true_clear" if ok else "false_clear") if cleared else (
                "true_flag" if not ok else "false_flag")
            matrix[key] += 1
            print(f"[gate] {str(r['id']):26s} verdict={r['action']:16s} "
                  f"actual={r['actual']:9s} "
                  f"hits={('—' if r.get('actual_db_hits') is None else format(r['actual_db_hits'], ',')):>14} "
                  f"ratio={r.get('prediction_ratio') or '—'}", flush=True)
    finally:
        driver.close()
    return matrix


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, default=None,
                        help="snapshot for the cost model; omit to gate on shape alone")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--budget-rows", type=int, default=200_000,
                        help="predicted db-hit ceiling before the gate intervenes")
    parser.add_argument("--engine", default="graph_oltp",
                        choices=sorted(ENGINE_CAPABILITIES))
    parser.add_argument("--hops-default", type=int, default=1)
    parser.add_argument("--validate", action="store_true",
                        help="execute every case and score the gate's verdicts against "
                             "what actually happened")
    parser.add_argument("--database")
    parser.add_argument("--uri", default="bolt://localhost:7687")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", default="")
    parser.add_argument("--query-timeout", type=float, default=45.0)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.validate and not args.database:
        raise SystemExit("--validate requires --database")

    with args.cases.open('r', encoding='utf-8') as f:

        doc = json.load(f)
    cases = doc["cases"] if isinstance(doc, dict) else doc

    # Anchors are snapshot-specific ids. Estimating their cost against another snapshot
    # produces plausible numbers about nothing, so a mismatch is an error rather than a
    # warning — the first run of this gate did exactly that and reported every case as
    # comfortably within budget.
    curated_from = doc.get("curated_from") if isinstance(doc, dict) else None
    if args.src and curated_from and Path(curated_from) != args.src:
        raise SystemExit(
            f"case anchors were curated from {curated_from} but the cost model is being "
            f"built from {args.src}. Anchor ids do not carry across snapshots; regenerate "
            f"the cases for this snapshot or point --src at {curated_from}.")
    if args.src and not curated_from:
        print(f"[gate] warning: {args.cases} records no curated_from, so anchor/cost-model "
              f"agreement cannot be checked", flush=True)

    model = CostModel(args.src) if args.src else None
    if model:
        print(f"[gate] cost model from {args.src}: median L2 {model.median_l2:,}, "
              f"max L2 {model.max_l2:,}", flush=True)

    results = []
    for c in cases:
        q = c.get("question", "")
        hops = 2 if "2 transfer hops" in q or "two hops" in q else args.hops_default
        v = evaluate(q, hops=hops, model=model, budget_rows=args.budget_rows,
                     engine=args.engine)
        results.append({"id": c.get("id"), "action": v.action, "reason": v.reason,
                        **asdict(v.descriptor)})
        pd = v.descriptor.predicted_db_hits
        print(f"[gate] {str(c.get('id')):24s} {v.action:16s} "
              f"op={v.descriptor.operation:20s} "
              f"pred={'—' if pd is None else format(pd, ',.0f'):>12}", flush=True)

    matrix = None
    if args.validate:
        matrix = validate(results, cases, uri=args.uri, user=args.user,
                          password=args.password, database=args.database,
                          timeout_s=args.query_timeout, budget_rows=args.budget_rows)
        print(f"[gate] matrix {matrix}", flush=True)

    lines = ["# Pre-flight workload gate", "",
             f"cases `{args.cases}` · engine `{args.engine}` · budget "
             f"{args.budget_rows:,} predicted db hits"
             + (f" · cost model `{args.src}`" if args.src else " · shape only"), "",
             "Verdicts are actions, not rewritten queries: emitting a cleverer bound is the "
             "failure mode this gate exists to avoid (per-hop `ORDER BY ... LIMIT` measured "
             "70,000x worse than no bound on a hub).", "",
             "| case | operation | anchor | predicted db hits | terminable | bound safe | action |",
             "|---|---|---|---|---|---|---|"]
    for r in results:
        pd = r["predicted_db_hits"]
        lines.append(
            f"| {r['id']} | {r['operation']} | {r['anchor'] or '—'} | "
            f"{'—' if pd is None else format(pd, ',.0f')} | "
            f"{'yes' if r['terminable'] else 'NO'} | "
            f"{'yes' if r['bound_safe'] else 'NO'} | **{r['action']}** |")
    if matrix:
        total = sum(matrix.values())
        lines += [
            "", "## Verdict against outcome", "",
            "A **false clear** is the failure this gate exists to prevent: cleared, then "
            "did not return. A **false flag** trades answer quality for safety and is a "
            "tuning question, not a broken premise.", "",
            "| | actually within budget | actually over / timeout |",
            "|---|---|---|",
            f"| gate cleared | {matrix['true_clear']} ✓ | **{matrix['false_clear']} false clear** |",
            f"| gate flagged | {matrix['false_flag']} false flag | {matrix['true_flag']} ✓ |",
            "",
            f"{total} cases · "
            f"{(matrix['true_clear'] + matrix['true_flag']) / total:.0%} agreement"
            if total else "",
        ]
    markdown = "\n".join(lines) + "\n"

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(
            {"schema_version": "seocho.finbench.workload-gate.v1",
             "cases": str(args.cases), "engine": args.engine,
             "budget_rows": args.budget_rows,
             "cost_model": str(args.src) if args.src else None,
             "verdict_matrix": matrix,
             "db_hits_per_l2": DB_HITS_PER_L2,
             "results": results}, indent=2) + "\n")
        args.out.with_suffix(".md").write_text(markdown)
    print(markdown)


if __name__ == "__main__":
    main()
