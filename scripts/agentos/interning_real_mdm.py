"""Real-data interning validation against the MDM golden master (mdmmaster).

FinBench (ADR-0160/0161) used planted synthetic duplicates. This validates the
same intern function on REAL entities that multiple models (DeepSeek-V3.1,
gpt-oss-120b, MiniMax-M2.5) and categories (risk, research, compliance, …)
extracted from SEC filings — and where a human/rule MDM pipeline already built
the ground-truth consolidation:

  * GoldenEntity  — the consolidated canonical entities (the golden intern table)
  * SourceRef -[:DERIVED_FROM]-> GoldenEntity — each raw model/category extraction
    and the golden entity it was merged into. THIS IS THE GROUND TRUTH.
  * SourceRef.business_key — the MDM pipeline's OWN identity key (name|label).

Question: does ``seocho.index.identity.compute_node_identity`` reproduce the
golden clustering? We cluster SourceRefs by each policy's key and score the
clustering against the golden clusters with pairwise precision/recall/F1:

  * recall  = of SourceRef pairs that ARE co-golden, fraction sharing a key
              (does exact interning collapse real duplicates?)
  * precision = of pairs sharing a key, fraction actually co-golden
              (does exact interning avoid merging distinct entities?)

Arms: ``intern_name`` (compute_node_identity, name only), ``intern_name_label``
(name+label), ``business_key`` (MDM's own key — a real baseline), and
``vector_bge`` (semantic single-link at its best-F1 threshold — an oracle
ceiling for the fuzzy fallback the ADR-0161 hybrid recommends).

Usage:
  python scripts/agentos/interning_real_mdm.py --container graphrag-neo4j \
      --database mdmmaster --out outputs/agentos/interning_real_mdm.json
"""

from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from seocho.index.identity import compute_node_identity  # noqa: E402


def auth_of(container: str) -> Tuple[str, str]:
    out = subprocess.check_output(
        ["docker", "inspect", container, "--format",
         "{{range .Config.Env}}{{println .}}{{end}}"]).decode()
    for line in out.splitlines():
        if line.startswith("NEO4J_AUTH="):
            u, p = line[len("NEO4J_AUTH="):].split("/", 1)
            return u, p
    raise SystemExit(f"no NEO4J_AUTH on {container}")


def load_sourcerefs(container: str, uri: str, database: str) -> List[Dict[str, Any]]:
    from neo4j import GraphDatabase

    u, p = auth_of(container)
    drv = GraphDatabase.driver(uri, auth=(u, p))
    try:
        with drv.session(database=database, default_access_mode="READ") as s:
            rows = s.run(
                "MATCH (sr:SourceRef)-[:DERIVED_FROM]-(g:GoldenEntity) "
                "RETURN sr.name AS name, sr.business_key AS bk, sr.model AS model, "
                "sr.src_db AS src_db, elementId(g) AS golden, g.name AS golden_name, "
                "labels(sr) AS labels, sr.business_key AS raw_bk "
                "ORDER BY golden").data()
    finally:
        drv.close()
    out = []
    for r in rows:
        # label for name+label key: prefer the business_key's label suffix, else
        # the node's non-SourceRef label.
        bk = r.get("bk") or ""
        label = bk.split("|")[-1] if "|" in bk else (
            next((l for l in (r.get("labels") or []) if l != "SourceRef"), "Entity"))
        out.append({
            "name": r["name"] or "",
            "label": label,
            "business_key": bk,
            "model": r.get("model"),
            "src_db": r.get("src_db"),
            "golden": r["golden"],
        })
    return out


def _pairwise_prf(items: List[Dict[str, Any]], key_fn, golden_fn) -> Dict[str, float]:
    tp = fp = fn = 0
    for a, b in itertools.combinations(range(len(items)), 2):
        same_key = key_fn(items[a]) is not None and key_fn(items[a]) == key_fn(items[b])
        same_gold = golden_fn(items[a]) == golden_fn(items[b])
        if same_key and same_gold:
            tp += 1
        elif same_key and not same_gold:
            fp += 1
        elif not same_key and same_gold:
            fn += 1
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4),
            "f1": round(f1, 4), "tp": tp, "fp": fp, "fn": fn,
            "distinct_keys": len({key_fn(i) for i in items if key_fn(i) is not None})}


def _vector_best_f1(items, golden_fn) -> Dict[str, Any]:
    """Single-link clustering over bge name similarity, swept threshold, best F1
    (an oracle-threshold ceiling for the semantic fallback — labeled as such)."""
    from seocho.store.fastembed_backend import make_fastembed_backend
    backend = make_fastembed_backend()
    if backend is None:
        return {"unavailable": True}
    import math
    vecs = backend.embed([i["name"] for i in items])

    def cos(a, b):
        d = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(y * y for y in b)) or 1.0
        return d / (na * nb)

    n = len(items)
    sims = {(a, b): cos(vecs[a], vecs[b]) for a, b in itertools.combinations(range(n), 2)}
    sweep = []
    best = {"f1": -1.0}
    for thr_i in range(50, 100, 2):
        thr = thr_i / 100.0
        parent = list(range(n))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for (a, b), sc in sims.items():
            if sc >= thr:
                parent[find(a)] = find(b)
        comp = [find(i) for i in range(n)]
        tp = fp = fn = 0
        for a, b in itertools.combinations(range(n), 2):
            sk = comp[a] == comp[b]
            sg = golden_fn(items[a]) == golden_fn(items[b])
            tp += sk and sg
            fp += sk and not sg
            fn += (not sk) and sg
        prec = tp / (tp + fp) if (tp + fp) else 1.0
        rec = tp / (tp + fn) if (tp + fn) else 1.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        point = {"threshold": thr, "precision": round(prec, 4),
                 "recall": round(rec, 4), "f1": round(f1, 4), "false_merges": fp}
        sweep.append(point)
        if f1 > best["f1"]:
            best = dict(point)
    # The whole sweep is kept, not just the peak: vector's precision is
    # threshold-dependent and collapses just below the best-F1 point, so the
    # best-F1 number is an ORACLE ceiling, not an operational result.
    best["note"] = "best-F1 over swept threshold = oracle ceiling (see full sweep)"
    best["sweep"] = sweep
    # precision at the operational thresholds just below the peak — the honesty
    # check: how fragile is that 1.000?
    below = [p for p in sweep if p["threshold"] in (0.78, 0.80)]
    best["precision_just_below_peak"] = below
    return best


def evaluate(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    golden_fn = lambda i: i["golden"]

    def intern_name(i):
        return compute_node_identity("Entity", {"name": i["name"]}, ["name"])

    def intern_name_label(i):
        return compute_node_identity(i["label"], {"name": i["name"]}, ["name"])

    def biz_key(i):
        return i["business_key"] or None

    return {
        "intern_name": _pairwise_prf(items, intern_name, golden_fn),
        "intern_name_label": _pairwise_prf(items, intern_name_label, golden_fn),
        "business_key": _pairwise_prf(items, biz_key, golden_fn),
        "vector_bge": _vector_best_f1(items, golden_fn),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--container", default="graphrag-neo4j")
    ap.add_argument("--uri", default="bolt://localhost:7687")
    ap.add_argument("--database", default="mdmmaster")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    items = load_sourcerefs(args.container, args.uri, args.database)
    golden_clusters = {}
    for i in items:
        golden_clusters.setdefault(i["golden"], []).append(i)
    multi = {g: v for g, v in golden_clusters.items() if len(v) > 1}
    models = sorted({i["model"] for i in items if i["model"]})
    print(f"{args.database}: {len(items)} SourceRefs, {len(golden_clusters)} golden "
          f"clusters ({len(multi)} multi-source), models={models}")

    report = {"database": args.database, "n_sourcerefs": len(items),
              "n_golden_clusters": len(golden_clusters),
              "n_multisource_clusters": len(multi), "models": models}
    report["all"] = evaluate(items)
    # multi-source-only: the real duplicates (drop singletons that trivially match)
    multi_items = [i for g, v in multi.items() for i in v]
    report["multisource_only"] = evaluate(multi_items) if multi_items else {}

    print("\n=== clustering vs MDM golden ground truth (pairwise P / R / F1) ===")
    for scope in ("all", "multisource_only"):
        print(f"\n-- {scope} ({len(items) if scope=='all' else len(multi_items)} refs) --")
        for arm, r in report[scope].items():
            if r.get("unavailable"):
                print(f"  {arm:18s} (fastembed unavailable)"); continue
            extra = f" @thr={r['threshold']}" if "threshold" in r else ""
            print(f"  {arm:18s} P={r['precision']:.3f} R={r['recall']:.3f} "
                  f"F1={r['f1']:.3f}{extra}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
