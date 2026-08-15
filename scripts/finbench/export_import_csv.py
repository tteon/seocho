#!/usr/bin/env python3
"""Export a FinBench Parquet snapshot to neo4j-admin bulk-import CSVs.

Why this exists: the transactional loader (load_to_graph.py) writes through
GraphStore.write and measured ~142 edges/sec on DozerDB — bolt round-trip bound,
not CPU bound. Extrapolated, SF100 takes 2.4 hours and SF1000 takes a full day,
which makes large scale factors infeasible for reasons that have nothing to do
with the database's capability. ``neo4j-admin database import full`` is the
offline bulk path and runs orders of magnitude faster.

Everything is computed in DuckDB SQL and streamed out with COPY, so memory does
not grow with the scale factor (building a Python payload for SF1000 — 3.3M nodes
and 12M edges — would not fit comfortably).

Degree/hub-tier statistics are computed here too (see load_to_graph.annotate_degrees
for the rationale): super-nodes dominate traversal cost, and materializing degree
turns that into a statistic the middleware and guardrail can read cheaply.

Header conventions are neo4j-admin's: ``:ID``/``:LABEL`` on nodes,
``:START_ID``/``:END_ID``/``:TYPE`` on relationships, and ``name:type`` for typed
properties. Node ids are label-namespaced ("Account:9000200") exactly as the
transactional loader does, so both paths produce interchangeable graphs.

Usage:
    python scripts/finbench/export_import_csv.py --src outputs/finbench/sf10 \
        --out data/neo4j/import/finbenchsf10
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb

# Node id is "<Label>:<key>" to match load_to_graph._nid so the two loaders agree.
_NODE_EXPORTS = {
    # label: (parquet stem, key column, extra property columns with CSV types)
    "Account": ("Account", "id", [("iban", "string"), ("acct_type", "long"),
                                  ("risk_tier", "long"), ("flagged", "boolean"),
                                  ("owner_id", "long"), ("acct_no", "long")]),
    "Person": ("Person", "id", [("name", "string"), ("age", "long"), ("country", "string")]),
    "Company": ("Company", "id", [("name", "string"), ("sector", "string")]),
    "Loan": ("Loan", "id", [("principal", "long"), ("term_years", "long")]),
    "Channel": ("Channel", "code", [("code", "string"), ("label", "string"),
                                    ("risk_weight", "long"), ("share", "long")]),
    "Medium": ("Medium", "id", [("type", "string"), ("risk_level", "long"),
                                ("is_blocked", "boolean")]),
}

_EDGE_EXPORTS = {
    # rel type: (parquet stem, source label, target label, property columns)
    "TRANSFER": ("transfer", "Account", "Account",
                 [("amount", "long"), ("ts", "long"), ("channel", "string"),
                  ("channel_risk", "long"), ("cross_border", "boolean")]),
    "DEPOSIT": ("deposit", "Loan", "Account", [("amount", "long")]),
    "REPAY": ("repay", "Account", "Loan", [("amount", "long")]),
    "USES_CHANNEL": ("uses_channel", "Account", "Channel", [("tx_count", "long")]),
    # The party and device layers. WITHDRAW is Account->Account like TRANSFER but stays a
    # separate type because it means something different — money leaving the traceable
    # system — and a query that conflates the two cannot ask about the terminal step.
    "WITHDRAW": ("withdraw", "Account", "Account",
                 [("amount", "long"), ("ts", "long")]),
    "SIGN_IN": ("sign_in", "Medium", "Account",
                [("ts", "long"), ("location", "string")]),
    # APPLY, INVEST and GUARANTEE have Person-or-Company sources, which the fixed
    # (source label, target label) shape here cannot express — OWN already needed a
    # per-endpoint split for the same reason. Handled below alongside OWN rather than
    # silently exported with a wrong source label.
}

# Relationships whose source is Person *or* Company depending on the id range. Exported per
# endpoint, the way OWN already is: neo4j-admin needs one START_ID label per file, and
# guessing a single label would attach edges to the wrong node type.
_SPLIT_SOURCE_EDGES = {
    "APPLY": ("apply", "Loan", [("ts", "long"), ("organization", "string")]),
    "INVEST": ("invest", "Company", [("ts", "long"), ("ratio", "double")]),
    "GUARANTEE": ("guarantee", None, [("ts", "long"), ("relationship", "string")]),
}


def _q(src: Path, stem: str) -> str:
    return f"read_parquet('{src}/{'nodes' if stem[0].isupper() else 'edges'}/{stem}.parquet')"


def export(src: Path, out: Path, *, workspace_id: str = "default",
           source_id: str = "finbench") -> dict:
    con = duckdb.connect()
    out.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((src / "manifest.json").read_text())
    persons = manifest["counts"]["person"]

    # ---- degree / hub tier, entirely in SQL ----
    # Directed in/out degree over TRANSFER, then a 99th-percentile hub threshold.
    con.execute(f"""
        CREATE VIEW deg AS
        WITH t AS (SELECT src, dst FROM {_q(src, 'transfer')}),
             o AS (SELECT src AS id, count(*) AS out_d FROM t GROUP BY src),
             i AS (SELECT dst AS id, count(*) AS in_d  FROM t GROUP BY dst),
             u AS (SELECT id FROM o UNION SELECT id FROM i)
        SELECT u.id,
               coalesce(i.in_d, 0) AS in_d,
               coalesce(o.out_d, 0) AS out_d,
               coalesce(i.in_d, 0) + coalesce(o.out_d, 0) AS deg
        FROM u LEFT JOIN o ON o.id = u.id LEFT JOIN i ON i.id = u.id;
    """)
    stats = con.execute("""
        SELECT quantile_cont(deg, 0.99) AS hub_threshold,
               median(deg) AS median_deg, max(deg) AS max_deg, count(*) AS with_edges
        FROM deg WHERE deg > 0
    """).fetchone()
    hub_threshold, median_deg = float(stats[0] or 0), float(stats[1] or 0)

    written: dict[str, int] = {}

    # ---- node CSVs ----
    for label, (stem, key, props) in _NODE_EXPORTS.items():
        table = _q(src, stem)
        # Tenancy/provenance columns must match what GraphStore.write stamps, or
        # queries carrying a workspace predicate return nothing on a bulk-loaded
        # graph while working on a transactionally-loaded one — the two paths have
        # to be interchangeable, which is the whole premise of the bulk path.
        cols = [f"'{label}:' || CAST(n.{key} AS VARCHAR) AS \"id:ID\"",
                f"'{label}' AS \":LABEL\"",
                f"'{workspace_id}' AS \"_workspace_id:string\"",
                f"'{source_id}' AS \"_source_id:string\""]
        for name, ctype in props:
            cols.append(f'n."{name}" AS "{name}:{ctype}"')
        if label == "Account":
            # acct_no is the plain numeric id kept matchable for NL questions.
            cols = [c for c in cols if not c.startswith('n."acct_no"')]
            cols.append('CAST(n.id AS BIGINT) AS "acct_no:long"')
            cols += [
                'coalesce(d.in_d, 0) AS "_in_degree:long"',
                'coalesce(d.out_d, 0) AS "_out_degree:long"',
                'coalesce(d.deg, 0) AS "_degree:long"',
                f'CASE WHEN coalesce(d.deg,0) >= {hub_threshold} AND coalesce(d.deg,0) > 0 THEN 2 '
                f'     WHEN coalesce(d.deg,0) > {median_deg} THEN 1 ELSE 0 END AS "_hub_tier:long"',
            ]
            join = "LEFT JOIN deg d ON d.id = n.id"
        else:
            join = ""
        path = out / f"nodes_{label}.csv"
        con.execute(f"COPY (SELECT {', '.join(cols)} FROM {table} n {join}) "
                    f"TO '{path}' (HEADER, DELIMITER ',')")
        written[f"nodes_{label}.csv"] = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    # ---- relationship CSVs ----
    for rtype, (stem, slabel, tlabel, props) in _EDGE_EXPORTS.items():
        table = _q(src, stem)
        cols = [f"'{slabel}:' || CAST(e.src AS VARCHAR) AS \":START_ID\"",
                f"'{tlabel}:' || CAST(e.dst AS VARCHAR) AS \":END_ID\"",
                f"'{rtype}' AS \":TYPE\""]
        for name, ctype in props:
            cols.append(f'e."{name}" AS "{name}:{ctype}"')
        path = out / f"rels_{rtype}.csv"
        con.execute(f"COPY (SELECT {', '.join(cols)} FROM {table} e) "
                    f"TO '{path}' (HEADER, DELIMITER ',')")
        written[f"rels_{rtype}.csv"] = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    # OWN endpoints depend on the owner id range (Person below `persons`, else Company),
    # so it is emitted as two files rather than one typed join.
    own = _q(src, "own")
    for endpoint, label, predicate in (("person", "Person", f"e.src < {persons}"),
                                       ("company", "Company", f"e.src >= {persons}")):
        path = out / f"rels_OWN_{endpoint}.csv"
        con.execute(
            f"COPY (SELECT '{label}:' || CAST(e.src AS VARCHAR) AS \":START_ID\", "
            f"'Account:' || CAST(e.dst AS VARCHAR) AS \":END_ID\", 'OWN' AS \":TYPE\" "
            f"FROM {own} e WHERE {predicate}) TO '{path}' (HEADER, DELIMITER ',')")
        written[f"rels_OWN_{endpoint}.csv"] = con.execute(
            f"SELECT count(*) FROM {own} e WHERE {predicate}").fetchone()[0]

    # Same split for the party-layer edges. GUARANTEE splits on *both* ends, since either
    # side can be a Person or a Company, so it needs the cross product of endpoints.
    for rtype, (stem, tlabel, props) in _SPLIT_SOURCE_EDGES.items():
        table = _q(src, stem)
        prop_cols = "".join(f', e."{n}" AS "{n}:{t}"' for n, t in props)
        src_sides = (("person", "Person", f"e.src < {persons}"),
                     ("company", "Company", f"e.src >= {persons}"))
        if tlabel is None:  # both ends vary
            sides = [(f"{sn}_{tn}", sl, tl, f"{sp} AND {tp}")
                     for sn, sl, sp in src_sides
                     for tn, tl, tp in (("person", "Person", f"e.dst < {persons}"),
                                        ("company", "Company", f"e.dst >= {persons}"))]
        else:
            sides = [(sn, sl, tlabel, sp) for sn, sl, sp in src_sides]
        for suffix, slabel, dst_label, predicate in sides:
            name = f"rels_{rtype}_{suffix}.csv"
            path = out / name
            con.execute(
                f"COPY (SELECT '{slabel}:' || CAST(e.src AS VARCHAR) AS \":START_ID\", "
                f"'{dst_label}:' || CAST(e.dst AS VARCHAR) AS \":END_ID\", "
                f"'{rtype}' AS \":TYPE\"{prop_cols} "
                f"FROM {table} e WHERE {predicate}) TO '{path}' (HEADER, DELIMITER ',')")
            written[name] = con.execute(
                f"SELECT count(*) FROM {table} e WHERE {predicate}").fetchone()[0]

    summary = {
        "schema_version": "seocho.finbench.import-csv.v1",
        "src": str(src), "out": str(out),
        "hub_threshold": hub_threshold, "median_degree": median_deg,
        "max_degree": float(stats[2] or 0), "nodes_with_edges": int(stats[3] or 0),
        "files": written,
    }
    (out / "import_manifest.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def import_command(database: str, csv_dir_in_container: str) -> str:
    """The neo4j-admin invocation for the exported files."""
    nodes = " ".join(
        f"--nodes={csv_dir_in_container}/nodes_{label}.csv" for label in _NODE_EXPORTS
    )
    rels = " ".join(
        f"--relationships={csv_dir_in_container}/rels_{rtype}.csv" for rtype in _EDGE_EXPORTS
    )
    rels += (f" --relationships={csv_dir_in_container}/rels_OWN_person.csv"
             f" --relationships={csv_dir_in_container}/rels_OWN_company.csv")
    for rtype, (_stem, tlabel, _props) in _SPLIT_SOURCE_EDGES.items():
        suffixes = (["person_person", "person_company", "company_person", "company_company"]
                    if tlabel is None else ["person", "company"])
        for suffix in suffixes:
            rels += f" --relationships={csv_dir_in_container}/rels_{rtype}_{suffix}.csv"
    return (
        f"neo4j-admin database import full {database} {nodes} {rels} "
        "--id-type=string --overwrite-destination=true "
        "--skip-bad-relationships=true --skip-duplicate-nodes=true"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, required=True, help="snapshot dir (…/sf10)")
    parser.add_argument("--out", type=Path, required=True, help="CSV output dir (under data/neo4j/import)")
    parser.add_argument("--database", default="", help="print the neo4j-admin command for this db")
    parser.add_argument("--container-dir", default="", help="path of --out as seen inside the container")
    args = parser.parse_args()

    summary = export(args.src, args.out)
    print(json.dumps(summary, indent=2))
    if args.database:
        container_dir = args.container_dir or f"/var/lib/neo4j/import/{args.out.name}"
        print("\n# run inside the container:")
        print(import_command(args.database, container_dir))


if __name__ == "__main__":
    main()
