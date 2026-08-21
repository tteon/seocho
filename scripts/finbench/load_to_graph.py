#!/usr/bin/env python3
"""Workstream B2: load a generated FinBench Parquet snapshot into a graph store.

Maps the DuckDB snapshot (scripts/finbench/gen_duckdb.py output) to the canonical
``{nodes, relationships}`` payload and bulk-writes it through the GraphStore
abstraction. This is the *typed* on-ramp — it bypasses LLM extraction entirely,
which is correct for a scalability benchmark where the graph is already typed.

Backends:
* ``ladybug`` — embedded Kùzu, zero infra (default; good for a local slice)
* ``dozerdb`` / ``neo4j`` — bolt server (the primary path for scale runs)

The mapping (``build_graph_payload``) is a pure function over the snapshot dir so
it can be unit-tested without a live database.

Usage:
    python scripts/finbench/load_to_graph.py --src outputs/finbench/sf1 \
        --target ladybug --path outputs/finbench/sf1/graph.lbug --database finbench_sf1
    python scripts/finbench/load_to_graph.py --src outputs/finbench/sf1 \
        --target dozerdb --uri bolt://localhost:7687 --user neo4j --password "$NEO4J_PASSWORD" \
        --database finbenchsf1
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterator

import duckdb

NODE_LABELS = ("Account", "Person", "Company", "Loan", "Channel", "Medium")
# edge type -> (source label, target label). Edges whose source is Person-or-Company are
# resolved per-row below, because the label depends on the owner id range.
EDGE_ENDPOINTS = {
    "transfer": ("Account", "Account"),
    "deposit": ("Loan", "Account"),
    "repay": ("Account", "Loan"),
    "uses_channel": ("Account", "Channel"),
    # The device layer. Kept a distinct edge from TRANSFER even though WITHDRAW shares its
    # endpoints, because a withdrawal is money leaving the traceable system — the terminal
    # step of most typologies — and conflating them makes that step unaskable.
    "withdraw": ("Account", "Account"),
    "sign_in": ("Medium", "Account"),
}
# Edges whose source is a Person *or* a Company, decided by the id range — the same
# resolution "own" already needed. Without these the graph stops at the account layer and a
# question about the parties behind two accounts cannot be expressed at all.
PARTY_SOURCE_EDGES = {
    "apply": "Loan",
    "invest": "Company",
    "guarantee": None,   # target also varies
}
# Nodes keyed by a string code rather than a numeric id.
CODE_KEYED_LABELS = {"Channel": "code"}


def _nid(label: str, raw: Any) -> str:
    if label in CODE_KEYED_LABELS:
        return f"{label}:{raw}"
    return f"{label}:{int(raw)}"


def build_graph_payload(src: Path) -> tuple[list[dict], list[dict]]:
    """Pure mapping: snapshot dir -> (nodes, relationships) canonical payload."""
    con = duckdb.connect()
    manifest = json.loads((src / "manifest.json").read_text())
    persons = manifest["counts"]["person"]  # owner id < persons => Person, else Company

    nodes: list[dict] = []
    for label in NODE_LABELS:
        path = src / "nodes" / f"{label}.parquet"
        cols = [c[0] for c in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{path}')").fetchall()]
        key = CODE_KEYED_LABELS.get(label, "id")
        for row in con.execute(f"SELECT * FROM read_parquet('{path}')").fetchall():
            rec = dict(zip(cols, row))
            props = {k: v for k, v in rec.items() if k != "id"}
            if label not in CODE_KEYED_LABELS:
                # Retain the plain numeric id as a matchable property so NL questions
                # ("account number 9000200") map to Cypher without the label prefix.
                props["acct_no" if label == "Account" else f"{label.lower()}_no"] = int(rec["id"])
            nodes.append({"id": _nid(label, rec[key]), "label": label, "properties": props})

    rels: list[dict] = []
    for etype, (slabel, tlabel) in EDGE_ENDPOINTS.items():
        path = src / "edges" / f"{etype}.parquet"
        cols = [c[0] for c in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{path}')").fetchall()]
        for row in con.execute(f"SELECT * FROM read_parquet('{path}')").fetchall():
            rec = dict(zip(cols, row))
            props = {k: v for k, v in rec.items() if k not in ("src", "dst")}
            rels.append({
                "source": _nid(slabel, rec["src"]), "target": _nid(tlabel, rec["dst"]),
                # (endpoint ids resolve through _nid, which handles code-keyed labels)
                "type": etype.upper(), "source_label": slabel, "target_label": tlabel,
                "properties": props,
            })
    # "own": owner (Person|Company) -> Account, endpoint label by owner id range.
    own = src / "edges" / "own.parquet"
    for row in con.execute(f"SELECT src, dst FROM read_parquet('{own}')").fetchall():
        owner, acct = int(row[0]), int(row[1])
        slabel = "Person" if owner < persons else "Company"
        rels.append({
            "source": _nid(slabel, owner), "target": _nid("Account", acct),
            "type": "OWN", "source_label": slabel, "target_label": "Account", "properties": {},
        })

    for etype, fixed_target in PARTY_SOURCE_EDGES.items():
        path = src / "edges" / f"{etype}.parquet"
        if not path.exists():
            continue
        cols = [c[0] for c in
                con.execute(f"DESCRIBE SELECT * FROM read_parquet('{path}')").fetchall()]
        for row in con.execute(f"SELECT * FROM read_parquet('{path}')").fetchall():
            rec = dict(zip(cols, row))
            s_id, d_id = int(rec["src"]), int(rec["dst"])
            slabel = "Person" if s_id < persons else "Company"
            tlabel = fixed_target or ("Person" if d_id < persons else "Company")
            rels.append({
                "source": _nid(slabel, s_id), "target": _nid(tlabel, d_id),
                "type": etype.upper(), "source_label": slabel, "target_label": tlabel,
                "properties": {k: v for k, v in rec.items() if k not in ("src", "dst")},
            })
    return nodes, rels


def annotate_degrees(nodes: list[dict], rels: list[dict], *,
                     rel_type: str = "TRANSFER", hub_percentile: float = 0.99) -> dict:
    """Materialize degree statistics onto nodes as planner/guardrail input.

    Super-nodes (high fan-in/fan-out hubs) dominate traversal cost, but the query
    planner cannot know that before executing. Baking the degree into the
    projection turns it into a statistic the middleware can read cheaply:

    * ``_in_degree`` / ``_out_degree`` / ``_degree`` — answer fan-in/fan-out
      questions in O(1) instead of traversing.
    * ``_hub_tier`` — 0 normal, 1 above-median, 2 super-node (>= percentile).
      A guardrail can tighten (or refuse) an unbounded expansion through tier 2.

    Degrees are derived data; because loading is a batch projection (ADR-0155:
    the graph is a rebuildable derivative) recomputing on reload is correct.
    Returns a summary of the hub threshold and the top hubs.
    """
    indeg: dict[str, int] = {}
    outdeg: dict[str, int] = {}
    for r in rels:
        if r["type"] != rel_type:
            continue
        outdeg[r["source"]] = outdeg.get(r["source"], 0) + 1
        indeg[r["target"]] = indeg.get(r["target"], 0) + 1

    degrees = sorted(
        (indeg.get(n["id"], 0) + outdeg.get(n["id"], 0)) for n in nodes
    )
    nonzero = [d for d in degrees if d]
    threshold = nonzero[min(int(len(nonzero) * hub_percentile), len(nonzero) - 1)] if nonzero else 0
    median = nonzero[len(nonzero) // 2] if nonzero else 0

    for n in nodes:
        i, o = indeg.get(n["id"], 0), outdeg.get(n["id"], 0)
        total = i + o
        n["properties"].update({"_in_degree": i, "_out_degree": o, "_degree": total})
        n["properties"]["_hub_tier"] = 2 if total >= threshold and total > 0 else (1 if total > median else 0)

    hubs = sorted(((indeg.get(n["id"], 0) + outdeg.get(n["id"], 0), n["id"]) for n in nodes), reverse=True)
    return {"rel_type": rel_type, "hub_threshold": threshold, "median_degree": median,
            "top_hubs": [{"id": i, "degree": d} for d, i in hubs[:5]]}


# Indexes the tuned (sargable) query shapes depend on. Without these, an equality
# predicate still costs a label scan; with them it becomes a NodeIndexSeek.
INDEX_SPECS = (
    ("finbench_account_acct_no", "Account", "acct_no"),
    ("finbench_account_id", "Account", "id"),
    ("finbench_account_hub_tier", "Account", "_hub_tier"),
    # A sargable predicate still scans without an index to serve it. Indexing only
    # Account left path queries anchored on another label doing a 200k-dbHit scan
    # at SF1000 even though the predicate was already an equality, so every label's
    # identifying property is covered.
    ("finbench_person_id", "Person", "id"),
    ("finbench_company_id", "Company", "id"),
    ("finbench_loan_id", "Loan", "id"),
    ("finbench_channel_code", "Channel", "code"),
)


def ensure_indexes(store: Any, database: str) -> list[str]:
    """Create the supporting indexes; returns the statements applied."""
    applied: list[str] = []
    for name, label, prop in INDEX_SPECS:
        stmt = f"CREATE INDEX {name} IF NOT EXISTS FOR (n:{label}) ON (n.{prop})"
        try:
            store.execute_write(stmt, database=database)  # type: ignore[attr-defined]
        except AttributeError:
            driver = getattr(store, "_driver", None) or getattr(store, "driver", None)
            if driver is None:
                return applied
            with driver.session(database=database) as session:
                session.run(stmt).consume()
        applied.append(stmt)
    return applied


def _batched(items: list[dict], size: int) -> Iterator[list[dict]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _build_store(args: argparse.Namespace):
    if args.target in ("dozerdb", "neo4j"):
        from seocho.store.graph import Neo4jGraphStore
        return Neo4jGraphStore(args.uri, args.user, args.password)
    from seocho.store.graph import LadybugGraphStore
    return LadybugGraphStore(args.path or str(Path(args.src) / "graph.lbug"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, required=True, help="snapshot dir (…/sf1)")
    parser.add_argument("--target", choices=("ladybug", "dozerdb", "neo4j"), default="ladybug")
    parser.add_argument("--path", help="ladybug .lbug path")
    parser.add_argument("--uri", default="bolt://localhost:7687")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", default="")
    parser.add_argument("--database", default="finbench")
    parser.add_argument("--workspace-id", default="default")
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument("--no-degrees", action="store_true",
                        help="skip degree/hub-tier materialization")
    parser.add_argument("--no-indexes", action="store_true", help="skip index creation")
    args = parser.parse_args()

    nodes, rels = build_graph_payload(args.src)
    degree_summary = None if args.no_degrees else annotate_degrees(nodes, rels)
    store = _build_store(args)
    totals = {"nodes_created": 0, "relationships_created": 0, "errors": 0}
    for batch in _batched(nodes, args.batch_size):
        r = store.write(batch, [], database=args.database, workspace_id=args.workspace_id,
                        source_id="finbench")
        for k in totals:
            totals[k] += int(r.get(k, 0) or 0)
    for batch in _batched(rels, args.batch_size):
        r = store.write([], batch, database=args.database, workspace_id=args.workspace_id,
                        source_id="finbench")
        for k in totals:
            totals[k] += int(r.get(k, 0) or 0)
    indexes = [] if args.no_indexes else ensure_indexes(store, args.database)
    print(json.dumps({"src": str(args.src), "target": args.target, "database": args.database,
                      "input": {"nodes": len(nodes), "relationships": len(rels)}, "result": totals,
                      "degrees": degree_summary, "indexes": indexes}, indent=2))


if __name__ == "__main__":
    main()
