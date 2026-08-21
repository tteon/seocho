"""D3 full single-federated-graph indexing: index ALL working-set docs (jira +
confluence + github + drive) into ONE workspace-graph via our own extraction, so a
cross-source question is a single-DB join on canonical ids (source-agnostic ~xs|
ids from ADR-0204; workspace-scoped MERGE from ADR-0206). NEVER built from
answer_facts (#561) — only the raw doc text is indexed.

Reports the graph built + cross-source convergence (how many canonical entities are
referenced by >1 source platform) — the mechanism the join relies on.

Usage: python scripts/agentos/e2e_index_workingset.py [--limit N] [--workspace erb]
       [--model mara/MiniMax-M2.7] [--database erblpg]
"""

from __future__ import annotations

import argparse
import glob
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "src"))
_DOCS = os.path.join(_ROOT, "outputs", "agentos", "erb_xsource_workingset", "docs")


def _load_mara() -> None:
    for envf in [os.path.join(_ROOT, ".env"), "/home/hadry/lab/seocho/.env"]:
        if os.path.exists(envf):
            for line in open(envf):
                if line.startswith(("MARA_API_KEY=", "MARA_BASE_URL=")) and "=" in line:
                    k, v = line.strip().split("=", 1)
                    os.environ.setdefault(k, v.strip().strip('"').strip("'"))
            return


def _ontology():
    """A faithful-enough ontology for the Redwood engineering-support corpus.
    Customer and Component are globally name-unique across sources (cross_source_unique)
    so the same customer/component mentioned in a jira issue and a confluence runbook
    converges to ONE canonical node — the cross-source join key."""
    from seocho import NodeDef, Ontology, P, RelDef
    return Ontology(
        "erb", package_id="erb", version="1.0.0",
        nodes={
            "Customer": NodeDef(description="An enterprise customer/organization.",
                                properties={"name": P(str, unique=True)},
                                cross_source_unique=True),
            "Component": NodeDef(description="A product component, feature, or service "
                                 "(e.g. streaming endpoint, private installer, request-log TTL).",
                                 properties={"name": P(str, unique=True)},
                                 cross_source_unique=True),
            "Issue": NodeDef(description="A reported issue, incident, bug, or ticket.",
                             properties={"name": P(str, unique=True), "summary": P(str)}),
            "Policy": NodeDef(description="A policy, contract term, or enforcement rule "
                              "(e.g. residency, retention, TTL policy).",
                              properties={"name": P(str, unique=True), "detail": P(str)}),
        },
        relationships={
            "AFFECTS": RelDef(description="An issue affects a customer.",
                              source="Issue", target="Customer"),
            "CONCERNS": RelDef(description="An issue concerns a component.",
                               source="Issue", target="Component"),
            "GOVERNS": RelDef(description="A policy governs a component.",
                              source="Policy", target="Component"),
            "RELATES_TO": RelDef(description="An issue relates to another issue.",
                                 source="Issue", target="Issue"),
        },
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0, help="index only the first N docs (0=all)")
    ap.add_argument("--workspace", default="erb")
    ap.add_argument("--model", default="mara/MiniMax-M2.7")
    ap.add_argument("--uri", default="bolt://localhost:17687")
    ap.add_argument("--password", default="h0gatepass")
    ap.add_argument("--database", default="")
    args = ap.parse_args()
    _load_mara()

    from seocho import Seocho

    docs = sorted(glob.glob(os.path.join(_DOCS, "*.txt")))
    if args.limit:
        docs = docs[: args.limit]
    print(f"workspace={args.workspace} model={args.model} docs={len(docs)}")

    client = Seocho.local(
        _ontology(), llm=args.model, graph=args.uri, neo4j_user="neo4j",
        neo4j_password=args.password, api_key=os.environ.get("MARA_API_KEY"),
        workspace_id=args.workspace,
    )
    db = args.database or getattr(client, "default_database", None) or "erblpg"
    client._engine.graph_store.ensure_database(db)
    print(f"database={db}")

    totals = {"nodes": 0, "rels": 0, "errors": 0}
    for path in docs:
        platform = os.path.basename(path).split("__", 1)[0]
        text = open(path, encoding="utf-8").read()
        try:
            r = client.add(text, source_type=f"{platform}__{os.path.basename(path)[:-4]}")
            md = r.metadata if hasattr(r, "metadata") else {}
            n, e = md.get("nodes_created", 0), md.get("relationships_created", 0)
            werr = len(md.get("write_errors", []) or [])
            totals["nodes"] += n; totals["rels"] += e; totals["errors"] += werr
            print(f"  [{platform:12s}] {os.path.basename(path):40s} nodes={n} rels={e} werr={werr}")
        except Exception as ex:
            print(f"  [{platform}] {os.path.basename(path)} ERROR: {type(ex).__name__}: {str(ex)[:120]}")

    print(f"\n=== indexed: nodes={totals['nodes']} rels={totals['rels']} write_errors={totals['errors']} ===")

    # cross-source convergence census: canonical entities referenced by >1 source
    drv = client._engine.graph_store._driver
    with drv.session(database=db) as s:
        rows = list(s.run(
            "MATCH (n {_workspace_id:$ws}) WHERE n.id STARTS WITH '~xs|' "
            "RETURN labels(n)[0] AS label, n.name AS name, size(coalesce(n._sources,[])) AS srcs "
            "ORDER BY srcs DESC LIMIT 20", ws=args.workspace))
        multi = [r for r in rows if (r["srcs"] or 0) > 1]
        print(f"canonical (~xs|) entities: {len(rows)}; referenced by >1 source: {len(multi)}")
        for r in rows[:12]:
            print(f"    {r['label']:10s} {str(r['name'])[:44]:44s} sources={r['srcs']}")


if __name__ == "__main__":
    main()
