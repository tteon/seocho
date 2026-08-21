"""Live smoke: does the structured engine axis actually run against DozerDB+MARA?

The critical unknown before the full arm×organ e2e: index a couple of tiny docs
into a live graph via our own extraction, then ask ONE question through BOTH
engines (deterministic + structured) and print the answers + the structured
metadata (arm, guardrail ledger, answer_source). Isolated by a unique workspace_id
on the default DB; cleaned up at the end.

Usage: python scripts/agentos/e2e_structured_smoke.py [--model mara/MiniMax-M2.7]
"""

from __future__ import annotations

import argparse
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "src"))


def _load_mara() -> None:
    for envf in [os.path.join(_ROOT, ".env"), "/home/hadry/lab/seocho/.env"]:
        if os.path.exists(envf):
            for line in open(envf):
                if line.startswith(("MARA_API_KEY=", "MARA_BASE_URL=")) and "=" in line:
                    k, v = line.strip().split("=", 1)
                    os.environ.setdefault(k, v.strip().strip('"').strip("'"))
            return


def _ontology():
    from seocho import NodeDef, Ontology, P, RelDef
    return Ontology(
        "enterprise", package_id="enterprise", version="1.0.0",
        nodes={
            # Company is globally name-unique across sources -> cross-source convergence
            "Company": NodeDef(description="A company/organization.",
                               properties={"name": P(str, unique=True)},
                               cross_source_unique=True),
            "Incident": NodeDef(description="An operational incident.",
                                properties={"name": P(str, unique=True),
                                            "summary": P(str)}),
        },
        relationships={
            "AFFECTS": RelDef(description="An incident affects a company.",
                              source="Incident", target="Company"),
        },
    )


DOCS = [
    ("jira__inc1", "Incident SUP-29410: a data-breach incident affecting Acme Corp "
                   "was reported on the support platform. Severity high."),
    ("confluence__policy1", "Acme Corp is an enterprise customer. The retention policy "
                            "for Acme Corp mandates 90-day log retention."),
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default="mara/MiniMax-M2.7")
    ap.add_argument("--uri", default="bolt://localhost:17687")
    ap.add_argument("--password", default="h0gatepass")
    args = ap.parse_args()
    _load_mara()

    from seocho import Seocho

    ws = f"e2e_smoke_{int(time.time())}"
    print(f"workspace={ws} model={args.model} uri={args.uri}")
    client = Seocho.local(
        _ontology(),
        llm=args.model,
        graph=args.uri,
        neo4j_user="neo4j",
        neo4j_password=args.password,
        api_key=os.environ.get("MARA_API_KEY"),
        workspace_id=ws,
    )

    # DozerDB needs the (auto-derived) database to exist before writes.
    db = getattr(client, "default_database", None) or "enterpriselpg"
    try:
        client._engine.graph_store.ensure_database(db)
        print(f"ensured database={db}")
    except Exception as e:
        print(f"ensure_database({db}) warning: {type(e).__name__}: {str(e)[:120]}")

    print("=== indexing tiny docs ===")
    for sid, text in DOCS:
        r = client.add(text, source_type=sid)
        print(f"  {sid}: {r}")

    q = "What incident affects Acme Corp?"
    print(f"\n=== ask: {q!r} ===")
    for engine in ("deterministic", "structured"):
        try:
            ans = client.ask(q, engine=engine)
            md = client.last_query_metadata
            print(f"\n[{engine}] answer_source={md.get('answer_source')} "
                  f"arm={md.get('arm', {}).get('name') if md.get('arm') else '-'}")
            print(f"  cypher={str(md.get('cypher',''))[:160]}")
            print(f"  ledger={md.get('guardrail_ledger', {})}")
            print(f"  ANSWER: {str(ans)[:300]}")
        except Exception as e:
            import traceback
            print(f"[{engine}] ERROR: {type(e).__name__}: {str(e)[:200]}")
            traceback.print_exc()

    # cleanup: drop this workspace's nodes (write mode via the driver; store.query
    # is READ-only on the governed path).
    try:
        drv = client._engine.graph_store._driver
        with drv.session(database=db) as s:
            s.run("MATCH (n {_workspace_id:$ws}) DETACH DELETE n", ws=ws)
        print(f"\ncleaned up workspace {ws}")
    except Exception as e:
        print(f"cleanup skipped: {type(e).__name__}: {str(e)[:120]}")


if __name__ == "__main__":
    main()
