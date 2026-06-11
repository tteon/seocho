#!/usr/bin/env python3
"""Cross-database entity resolution: GDS WCC over the staging graph — $0.

Runs on ``mdmstaging`` through the repo's safe GDS wrapper
(``seocho.gds.gds_session``: estimate-gated projection, auto-drop on exit).
WCC over SAME_AS_CAND candidate edges yields the resolved clusters — "these
N department nodes are the same real-world entity".

Steward overrides are honored: a ``[:NOT_SAME_AS]`` edge between two proxies
(written by a human reviewer) removes the candidate edge from the projection,
so a deterministic re-run splits the wrongly-merged golden record (the
classical MDM "unmerge" story — sources are never mutated, only re-clustered).

Note on ids: the legacy Cypher projection requires GDS's internal numeric
``id()`` inside node/rel queries; those ids never leave the projection and
results are joined back via ``elementId()`` (§8).

Output: outputs/evaluation/mdm_demo/<run>/resolve_artifact.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

MDM_ROOT = Path(__file__).resolve().parent
ROOT = MDM_ROOT.parents[1]
sys.path.insert(0, str(MDM_ROOT))
sys.path.insert(0, str(ROOT))

import os  # noqa: E402

from dotenv import dotenv_values  # noqa: E402

for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None:
        os.environ.setdefault(k, v)

from lib.survivorship import load_ruleset  # noqa: E402

STAGING_DB = "mdmstaging"
STAGING_WS = "mdm-staging-v1"

# Candidate edges minus steward NOT_SAME_AS overrides. (Projection-internal
# numeric ids — see module docstring.)
NODE_QUERY = (
    "MATCH (p:EntityProxy {_workspace_id:'" + STAGING_WS + "'}) "
    "RETURN id(p) AS id"
)
REL_QUERY = (
    "MATCH (a:EntityProxy {_workspace_id:'" + STAGING_WS + "'})"
    "-[c:SAME_AS_CAND]->"
    "(b:EntityProxy {_workspace_id:'" + STAGING_WS + "'}) "
    "WHERE NOT (a)-[:NOT_SAME_AS]-(b) "
    "RETURN id(a) AS source, id(b) AS target"
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-prefix", default="seocho-capital-v1")
    ap.add_argument("--similarity-top-k", type=int, default=10)
    args = ap.parse_args()

    ruleset = load_ruleset()

    from seocho.gds import MetricSpec, gds_session
    from seocho.store.graph import Neo4jGraphStore

    gs = Neo4jGraphStore(os.environ["NEO4J_URI"],
                         os.environ.get("NEO4J_USER", "neo4j"),
                         os.environ.get("NEO4J_PASSWORD", ""))
    try:
        excl = gs.query(
            "MATCH (:EntityProxy {_workspace_id:$ws})-[x:NOT_SAME_AS]-() "
            "RETURN count(x)/2 AS c", params={"ws": STAGING_WS}, database=STAGING_DB)
        n_excl = int(excl[0]["c"]) if excl else 0

        with gds_session(gs, name="mdm-er", database=STAGING_DB) as g:
            est = g.project_cypher(node_query=NODE_QUERY, rel_query=REL_QUERY,
                                   estimate_ok=True)
            print(f"== projection 'mdm-er': {est.node_count} nodes, "
                  f"{est.relationship_count} rels, ~{est.required_memory} "
                  f"(steward NOT_SAME_AS exclusions: {n_excl}) ==")
            rows = g.wcc(workspace_id=STAGING_WS)
            sim = g.metric(MetricSpec.NODE_SIMILARITY, top_k=args.similarity_top_k)

        # Join components back to proxy records via elementId (§8).
        proxies = gs.query(
            "MATCH (p:EntityProxy {_workspace_id:$ws}) "
            "RETURN elementId(p) AS eid, p.idx AS idx, p.name AS name, "
            "       p.norm_name AS norm_name, p.labels AS labels, "
            "       p.src_db AS src_db, p.src_eid AS src_eid, p.dept AS dept, "
            "       p.model AS model, p.business_key AS business_key, "
            "       p.case_id AS case_id, p.src_instance AS src_instance",
            params={"ws": STAGING_WS}, database=STAGING_DB)
        by_eid = {p["eid"]: p for p in proxies}
        clusters: dict[int, list[dict]] = {}
        for r in rows:
            p = by_eid.get(r["eid"])
            if p is not None:
                clusters.setdefault(int(r["componentId"]), []).append(p)

        multi = {cid: ms for cid, ms in clusters.items() if len(ms) > 1}
        print(f"== WCC: {len(clusters)} components from {len(proxies)} proxies "
              f"({len(multi)} multi-member, largest "
              f"{max((len(m) for m in clusters.values()), default=0)}) ==")
        for cid, members in sorted(multi.items(), key=lambda kv: -len(kv[1]))[:8]:
            names = ", ".join(f"{m['name']}({m['dept']})" for m in members)
            print(f"   #{cid}: {names}")

        if sim:
            print("== nodeSimilarity (bonus signal, top pairs) ==")
            for s in sim[:5]:
                print(f"   {s.get('a')} ~ {s.get('b')}: {s.get('similarity'):.3f}")

        out_dir = ROOT / "outputs" / "evaluation" / "mdm_demo" / args.run_prefix
        out_dir.mkdir(parents=True, exist_ok=True)
        artifact = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "ruleset_version": ruleset.version,
            "projection": {"nodes": est.node_count, "rels": est.relationship_count,
                           "required_memory": est.required_memory,
                           "not_same_as_exclusions": n_excl},
            "component_count": len(clusters),
            "multi_member_count": len(multi),
            "clusters": {str(cid): members for cid, members in clusters.items()},
            "node_similarity_top": sim,
        }
        path = out_dir / "resolve_artifact.json"
        path.write_text(json.dumps(artifact, indent=1, default=str), encoding="utf-8")
        print(f"== wrote {path.relative_to(ROOT)} ==")
        return 0
    finally:
        gs.close()


if __name__ == "__main__":
    raise SystemExit(main())
