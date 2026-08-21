"""Index the GraphRAG-Bench MEDICAL corpus into a typed graph (medicallpg) via our own
ontology-guided extraction (gpt-oss-120b). GraphRAG-Bench is an INDEPENDENT benchmark
(corpus + gold answers + gold evidence triples), so it is a defensible instrument for
the arm×organ A/B — not a question set we derived from our own graph.

The corpus is one ~263k-token medical (oncology) context; we chunk it and index every
chunk (indexing the whole corpus is the benchmark protocol; only the QUESTIONS are
sampled). cross_source_unique on the clinical entity types gives cross-CHUNK canonical
convergence (the same disease/anatomy mentioned in many chunks fuses to ONE node) — the
intern organ's contribution to multi-hop (Complex Reasoning) answering.

Usage: python scripts/agentos/index_graphrag_medical.py [--chunk 2000] [--limit 0]
       [--model mara/gpt-oss-120b] [--database medicallpg]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "src"))
_CORPUS = "/home/hadry/openup/_graphrag_benchmark/Datasets/Corpus/medical.json"


def _load_mara() -> None:
    for envf in [os.path.join(_ROOT, ".env"), "/home/hadry/lab/seocho/.env"]:
        if os.path.exists(envf):
            for line in open(envf):
                if line.startswith(("MARA_API_KEY=", "MARA_BASE_URL=")) and "=" in line:
                    k, v = line.strip().split("=", 1)
                    os.environ.setdefault(k, v.strip().strip('"').strip("'"))
            return


def medical_ontology(*, cross_source_unique: bool = True):
    """A focused oncology ontology for the GraphRAG-Bench medical corpus. Clinical
    entity types are cross_source_unique so the same disease/anatomy/celltype fuses
    across chunks to ONE canonical node (intern organ = cross-chunk convergence).

    ``cross_source_unique=False`` is the intern-organ-OFF variant for the dual-index
    ablation (seocho-svf). Two OFF tiers exist:
    - OFF0 (no identity_keys either): raw extractor ids -> cross-chunk id COLLISION,
      unrelated entities fuse randomly (measured: 'Anal Cancer' deg-694 carrying
      prostate/breast treatments). The no-identity-layer catastrophe.
    - OFF1 (identity_keys=['name'], this variant's default): a COMPETENT name-keyed
      baseline — label-scoped composite ids, no canonical cross-label address, no
      alias/read-side resolve. The fair reviewer-proof comparison arm."""
    from seocho import NodeDef, Ontology, P, RelDef
    xs = cross_source_unique
    ik = [] if xs else ["name"]
    U = lambda: {"name": P(str, unique=True)}  # noqa: E731
    return Ontology(
        "medical" if xs else "medicalnx",
        package_id="medical" if xs else "medicalnx", version="1.0.0",
        nodes={
            "Disease": NodeDef(description="A disease, cancer type, condition, or diagnosis.",
                               properties=U(), cross_source_unique=xs, identity_keys=ik),
            "Symptom": NodeDef(description="A symptom, sign, or clinical presentation.",
                               properties=U(), cross_source_unique=xs, identity_keys=ik),
            "Treatment": NodeDef(description="A treatment, therapy, drug, or procedure.",
                                 properties=U(), cross_source_unique=xs, identity_keys=ik),
            "Anatomy": NodeDef(description="A body part, tissue, organ, or anatomical site.",
                               properties=U(), cross_source_unique=xs, identity_keys=ik),
            "CellType": NodeDef(description="A cell type (e.g. basal cell, melanocyte).",
                                properties=U(), cross_source_unique=xs, identity_keys=ik),
            "RiskFactor": NodeDef(description="A risk factor or cause (e.g. UV exposure).",
                                  properties=U(), cross_source_unique=xs, identity_keys=ik),
            "Test": NodeDef(description="A diagnostic test, screening, or imaging method.",
                            properties=U(), cross_source_unique=xs, identity_keys=ik),
        },
        relationships={
            "HAS_SYMPTOM": RelDef(description="A disease presents a symptom.",
                                  source="Disease", target="Symptom"),
            "TREATED_BY": RelDef(description="A disease is treated by a treatment.",
                                 source="Disease", target="Treatment"),
            "ARISES_FROM": RelDef(description="A disease arises from a cell type or anatomy.",
                                  source="Disease", target="CellType"),
            "LOCATED_IN": RelDef(description="A disease is located in / affects an anatomy.",
                                 source="Disease", target="Anatomy"),
            "CAUSED_BY": RelDef(description="A disease is caused by a risk factor.",
                                source="Disease", target="RiskFactor"),
            "DIAGNOSED_BY": RelDef(description="A disease is diagnosed by a test.",
                                   source="Disease", target="Test"),
            "SUBTYPE_OF": RelDef(description="A disease is a subtype of another disease.",
                                 source="Disease", target="Disease"),
        },
    )


def _chunks(text: str, size: int):
    words, buf, n = text.split(), [], 0
    for w in words:
        buf.append(w); n += len(w) + 1
        if n >= size:
            yield " ".join(buf); buf, n = [], 0
    if buf:
        yield " ".join(buf)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--chunk", type=int, default=2000, help="chunk size in chars")
    ap.add_argument("--limit", type=int, default=0, help="index only first N chunks (0=all)")
    ap.add_argument("--offset", type=int, default=0,
                    help="resume: skip the first N chunks (already indexed)")
    ap.add_argument("--model", default="mara/gpt-oss-120b")
    ap.add_argument("--uri", default="bolt://localhost:17687")
    ap.add_argument("--password", default="h0gatepass")
    ap.add_argument("--database", default="medicallpg")
    ap.add_argument("--workspace", default="med")
    ap.add_argument("--no-xsource", action="store_true",
                    help="intern organ OFF at index time (dual-index ablation, seocho-svf)")
    args = ap.parse_args()
    _load_mara()

    from seocho import Seocho

    ctx = json.load(open(_CORPUS))["context"]
    chunks = list(_chunks(ctx, args.chunk))
    if args.limit:
        chunks = chunks[: args.limit]
    # resume support: keep GLOBAL chunk indices for source_type so a resumed run
    # continues medical__chunkNNNN numbering instead of re-writing chunk0000.
    indexed = list(enumerate(chunks))[args.offset:]
    print(f"corpus chars={len(ctx)} chunks={len(chunks)} model={args.model} db={args.database}",
          flush=True)

    client = Seocho.local(
        medical_ontology(cross_source_unique=not args.no_xsource),
        llm=args.model, graph=args.uri, neo4j_user="neo4j",
        neo4j_password=args.password, api_key=os.environ.get("MARA_API_KEY"),
        workspace_id=args.workspace)
    client._engine.graph_store.ensure_database(args.database)

    totals = {"nodes": 0, "rels": 0, "errors": 0}
    for i, ch in indexed:
        try:
            r = client.add(ch, source_type=f"medical__chunk{i:04d}")
            md = r.metadata if hasattr(r, "metadata") else {}
            totals["nodes"] += md.get("nodes_created", 0)
            totals["rels"] += md.get("relationships_created", 0)
            totals["errors"] += len(md.get("write_errors", []) or [])
        except Exception as ex:
            totals["errors"] += 1
            print(f"  chunk{i} ERROR: {type(ex).__name__}: {str(ex)[:100]}", flush=True)
        if (i + 1) % 20 == 0:
            print(f"  [{i+1}/{len(chunks)}] nodes={totals['nodes']} rels={totals['rels']} "
                  f"errors={totals['errors']}", flush=True)

    print(f"\n=== indexed: nodes={totals['nodes']} rels={totals['rels']} "
          f"errors={totals['errors']} ===", flush=True)
    drv = client._engine.graph_store._driver
    with drv.session(database=args.database) as s:
        ent = s.run("MATCH (n:Entity {_workspace_id:$w}) RETURN count(n) AS c",
                    w=args.workspace).single()["c"]
        by = list(s.run("MATCH (n {_workspace_id:$w}) RETURN labels(n)[0] AS l, count(*) AS c "
                        "ORDER BY c DESC", w=args.workspace))
        print(f"generic Entity fallbacks: {ent}", flush=True)
        for r in by:
            print(f"  {r['l']:14s} {r['c']}", flush=True)


if __name__ == "__main__":
    main()
