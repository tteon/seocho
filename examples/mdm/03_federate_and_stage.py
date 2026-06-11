#!/usr/bin/env python3
"""Federate the department DBs and materialize the ER staging graph — $0.

1. Federated read of the three department DBs (mode-branched: composite
   ``USE``-union when the preflight proved support, client fan-out otherwise —
   identical record shapes either way).
2. Materialize ``mdmstaging``: one ``(:EntityProxy)`` per company-like source
   node (resolution scope: ``--resolve-labels``, default LegalEntity+Entity —
   FinancialMetric nodes are FACTS handled by attribute survivorship in step
   05, not master entities to be resolved).
3. Candidate match edges ``[:SAME_AS_CAND {method, score}]``, two tiers:
     a. normalized exact key / ordered-token-prefix (merge_entities.py rule)
     b. local BGE embedding cosine ≥ threshold ($0; skipped EXPLICITLY and
        recorded in the artifact if sentence-transformers is unavailable)
4. Metric facts + entity records are persisted as JSON artifacts for step 05
   (every later dashboard number traces back to these — §20.1).

Idempotent: the staging workspace is wiped and rebuilt each run.
Department DBs are READ-ONLY here; node counts are recorded so 06 can verify
they were never mutated.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from itertools import combinations
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

from lib import federation  # noqa: E402
from lib.normalize import is_token_prefix, norm_key, norm_tokens  # noqa: E402
from lib.survivorship import load_ruleset  # noqa: E402

STAGING_DB = "mdmstaging"
STAGING_WS = "mdm-staging-v1"

DEPARTMENTS = [
    federation.Department(name="risk", database="mdmrisk", model="DeepSeek-V3.1"),
    federation.Department(name="research", database="mdmresearch", model="gpt-oss-120b"),
    federation.Department(name="compliance", database="mdmcompliance", model="MiniMax-M2.5"),
]


def embedding_candidates(proxies: list[dict], *, threshold: float, model_name: str,
                         existing: set[tuple[int, int]]) -> tuple[list[dict], str]:
    """Tier-b: cosine over local BGE embeddings of normalized names.

    Returns (pairs, status). Skipping is explicit and recorded, never silent.
    """
    try:
        import numpy as np
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        return [], f"skipped: {exc}"
    names = sorted({p["norm_name"] for p in proxies})
    if len(names) < 2:
        return [], "skipped: fewer than 2 distinct names"
    model = SentenceTransformer(model_name)
    vecs = model.encode(names, normalize_embeddings=True, show_progress_bar=False)
    sim = np.asarray(vecs) @ np.asarray(vecs).T
    name_idx = {n: i for i, n in enumerate(names)}
    pairs = []
    for i, j in combinations(range(len(proxies)), 2):
        if (i, j) in existing:
            continue
        a, b = proxies[i], proxies[j]
        if a["norm_name"] == b["norm_name"]:
            continue
        score = float(sim[name_idx[a["norm_name"]], name_idx[b["norm_name"]]])
        if score >= threshold:
            pairs.append({"i": i, "j": j, "method": "embedding",
                          "score": round(score, 4)})
    return pairs, f"computed over {len(names)} distinct names"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resolve-labels", default="LegalEntity,Entity",
                    help="labels treated as master entities (comma-separated)")
    ap.add_argument("--run-prefix", default="seocho-capital-v1")
    args = ap.parse_args()
    resolve_labels = {x.strip() for x in args.resolve_labels.split(",") if x.strip()}

    ruleset = load_ruleset()
    mode = federation.read_mode()
    print(f"== federation mode: {mode} (ruleset v{ruleset.version}) ==")

    from seocho.store.graph import Neo4jGraphStore
    from extraction.config import db_registry
    gs = Neo4jGraphStore(os.environ["NEO4J_URI"],
                         os.environ.get("NEO4J_USER", "neo4j"),
                         os.environ.get("NEO4J_PASSWORD", ""))
    try:
        # --- 1. federated read (dept DBs are READ-ONLY from here on) --------
        t0 = time.perf_counter()
        if mode == "composite":
            from neo4j import GraphDatabase
            drv = GraphDatabase.driver(os.environ["NEO4J_URI"],
                                       auth=(os.environ.get("NEO4J_USER", "neo4j"),
                                             os.environ.get("NEO4J_PASSWORD", "")))
            try:
                federation.create_composite(drv, composite="mdmcomp",
                                            aliases={d.name: d.database for d in DEPARTMENTS})
                entities, metrics = federation.composite_read(
                    drv, composite="mdmcomp", departments=DEPARTMENTS)
            finally:
                drv.close()
        else:
            entities, metrics = federation.fanout_read(gs, DEPARTMENTS)
        read_s = round(time.perf_counter() - t0, 2)

        dept_counts = {}
        for d in DEPARTMENTS:
            c = gs.query("MATCH (n) RETURN count(n) AS c", database=d.database)
            dept_counts[d.database] = int(c[0]["c"]) if c else 0
        print(f"== read {len(entities)} entities + {len(metrics)} metric facts "
              f"in {read_s}s; dept node counts {dept_counts} ==")

        # --- 2. proxies for the resolution scope ----------------------------
        proxies = []
        excluded_junk = 0
        for e in entities:
            if not set(e["labels"]) & resolve_labels:
                continue
            if norm_key(e["name"]) in ruleset.exclude_norm_names:
                excluded_junk += 1   # counted + reported below, never silent
                continue
            proxies.append({
                "name": e["name"],
                "norm_name": norm_key(e["name"]),
                "labels": sorted(e["labels"]),
                "src_db": e["src_db"],
                "src_eid": e["eid"],
                "dept": e["dept"],
                "model": e["model"],
                "case_id": e["case_id"],
                "business_key": f"{norm_key(e['name'])}|{'/'.join(sorted(e['labels']))}",
            })
        print(f"== resolution scope ({'+'.join(sorted(resolve_labels))}): "
              f"{len(proxies)} proxies ({excluded_junk} boilerplate names "
              f"suppressed per ruleset v{ruleset.version}) ==")

        # --- 3. candidate pairs ---------------------------------------------
        pairs: list[dict] = []
        seen: set[tuple[int, int]] = set()
        toks = [norm_tokens(p["name"]) for p in proxies]
        for i, j in combinations(range(len(proxies)), 2):
            a, b = proxies[i], proxies[j]
            if a["norm_name"] and a["norm_name"] == b["norm_name"]:
                pairs.append({"i": i, "j": j, "method": "exact_key", "score": 1.0})
                seen.add((i, j))
            elif toks[i] and toks[j] and (is_token_prefix(toks[i], toks[j])
                                          or is_token_prefix(toks[j], toks[i])):
                pairs.append({"i": i, "j": j, "method": "token_prefix", "score": 0.9})
                seen.add((i, j))
        emb_pairs, emb_status = embedding_candidates(
            proxies, threshold=ruleset.embedding_threshold,
            model_name=ruleset.embedding_model, existing=seen)
        pairs.extend(emb_pairs)
        print(f"== candidates: {len(pairs)} pairs "
              f"(exact/prefix {len(seen)}, embedding {len(emb_pairs)} [{emb_status}]) ==")

        # --- 4. rebuild mdmstaging (idempotent) ------------------------------
        db_registry.register(STAGING_DB)
        gs.ensure_database(STAGING_DB, wait_online=True)
        gs.query("MATCH (n {_workspace_id:$ws}) DETACH DELETE n",
                 params={"ws": STAGING_WS}, database=STAGING_DB)
        gs.query(
            "UNWIND $rows AS r "
            "CREATE (p:EntityProxy {_workspace_id:$ws}) SET p += r",
            params={"rows": [{**p, "labels": "/".join(p["labels"]), "idx": i}
                             for i, p in enumerate(proxies)], "ws": STAGING_WS},
            database=STAGING_DB)
        gs.query(
            "UNWIND $pairs AS pr "
            "MATCH (a:EntityProxy {idx: pr.i, _workspace_id:$ws}), "
            "      (b:EntityProxy {idx: pr.j, _workspace_id:$ws}) "
            "CREATE (a)-[:SAME_AS_CAND {method: pr.method, score: pr.score, "
            "                           rule_set_version: $rv}]->(b)",
            params={"pairs": pairs, "ws": STAGING_WS, "rv": ruleset.version},
            database=STAGING_DB)
        check = gs.query(
            "MATCH (p:EntityProxy {_workspace_id:$ws}) "
            "OPTIONAL MATCH (p)-[c:SAME_AS_CAND]->() "
            "RETURN count(DISTINCT p) AS nodes, count(c) AS edges",
            params={"ws": STAGING_WS}, database=STAGING_DB)[0]
        print(f"== {STAGING_DB}: {check['nodes']} EntityProxy, "
              f"{check['edges']} SAME_AS_CAND ==")

        # --- 5. artifacts for step 05 (§20.1 traceability) -------------------
        out_dir = ROOT / "outputs" / "evaluation" / "mdm_demo" / args.run_prefix
        out_dir.mkdir(parents=True, exist_ok=True)
        artifact = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": mode, "ruleset_version": ruleset.version,
            "ruleset_sha256": ruleset.sha256,
            "resolve_labels": sorted(resolve_labels),
            "excluded_junk_count": excluded_junk,
            "embedding_tier": emb_status,
            "dept_node_counts": dept_counts,
            "entities": entities, "metrics": metrics,
            "proxies": proxies, "candidate_pairs": pairs,
        }
        path = out_dir / "staging_artifact.json"
        path.write_text(json.dumps(artifact, indent=1, default=str), encoding="utf-8")
        print(f"== wrote {path.relative_to(ROOT)} ==")
        return 0
    finally:
        gs.close()


if __name__ == "__main__":
    raise SystemExit(main())
