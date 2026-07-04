"""Large-corpus guardrail ablation on FinDER (Linq-AI-Research/FinDER, 5,703 cases).

Extends the small guardrail ablation to real SEC financial text. Compares a
SPARSE FIBO guardrail (fibo_minus, 2 classes) against a RICH/refined FIBO
guardrail (fibo_plus, 9 classes) as the LLM extraction guardrail, over a
category-stratified sample, across all MARA models. Reports, per arm:

  - coverage (nodes/rels per doc), conformance to the arm's own guardrail,
    extraction_score, and exp-001-style label consistency (distinct labels,
    label entropy in bits, top-1 share).

Plus a scorecard reading of each FIBO variant (so the quality ladder the
scorecard assigns is shown next to the downstream effect).

Key from .env (ontology_guardrail_mara_api_key). Deterministic stratified
sampling (sorted by _id, first N per category — no RNG). Run:
    PYTHONPATH=src python3 scripts/benchmarks/finder_guardrail_ablation.py \
        --per-category 8 --max-chars 3000 --out <file.json>
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

from seocho.ontology import Ontology
from seocho.ontology_scorecard import score_ontology
from seocho.store.llm import create_llm_backend

MODELS = ["DeepSeek-V3.1", "MiniMax-M2.5", "MiniMax-M2.7", "gpt-oss-120b"]
ARMS = {
    "A_sparse_fibo_minus": "examples/datasets/fibo_minus.jsonld",
    "B_rich_fibo_plus": "examples/datasets/fibo_plus.jsonld",
}

_EXTRACT_SYSTEM = (
    "You extract a knowledge graph from financial text, STRICTLY conforming to the "
    "provided ontology. Use ONLY the listed entity labels and relationship types. "
    "Return ONLY a JSON object."
)


def load_finder(per_category: int, max_chars: int):
    from huggingface_hub import hf_hub_download
    import pandas as pd

    p = hf_hub_download("Linq-AI-Research/FinDER", "data/train-00000-of-00001.parquet", repo_type="dataset")
    df = pd.read_parquet(p)
    docs = []
    for cat, group in df.sort_values("_id").groupby("category"):
        taken = 0
        for _, row in group.iterrows():
            refs = row["references"]
            text = " ".join(str(x) for x in refs) if hasattr(refs, "__iter__") and not isinstance(refs, str) else str(refs)
            text = text.strip()
            if len(text) < 80:
                continue
            docs.append({"id": str(row["_id"]), "category": str(cat), "text": text[:max_chars]})
            taken += 1
            if taken >= per_category:
                break
    return docs


def extraction_prompt(onto: Ontology, doc: str) -> str:
    ctx = onto.to_extraction_context()
    return (
        f"ENTITY TYPES (use only these labels):\n{ctx.get('entity_types','')}\n\n"
        f"RELATIONSHIP TYPES (use only these):\n{ctx.get('relationship_types','')}\n\n"
        f"CONSTRAINTS:\n{ctx.get('constraints_summary','')}\n\n"
        f"FINANCIAL TEXT:\n{doc}\n\n"
        'Return JSON: {"nodes":[{"id":"n1","label":"<Label>","properties":{"name":"..."}}],'
        '"relationships":[{"source":"n1","target":"n2","type":"<TYPE>"}]}'
    )


def _parse_graph(text: str) -> dict:
    s = text.strip()
    if s.startswith("```"):
        s = "\n".join(l for l in s.split("\n") if not l.strip().startswith("```"))
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, re.DOTALL)
        try:
            return json.loads(m.group(0)) if m else {}
        except Exception:
            return {}


def _entropy(labels) -> float:
    if not labels:
        return 0.0
    c = Counter(labels)
    total = sum(c.values())
    return round(-sum((n / total) * math.log2(n / total) for n in c.values()), 4)


def _doc_metrics(graph: dict, guardrail: Ontology) -> dict:
    nodes = [n for n in graph.get("nodes", []) if isinstance(n, dict)]
    rels = [r for r in graph.get("relationships", []) if isinstance(r, dict)]
    labels = [str(n.get("label", "")) for n in nodes]
    rtypes = [str(r.get("type", "")) for r in rels]
    label_ok = sum(1 for l in labels if l in guardrail.nodes)
    rtype_ok = sum(1 for t in rtypes if t in guardrail.relationships)
    sc = guardrail.score_extraction({"nodes": nodes, "relationships": rels})
    return {
        "nodes": len(nodes), "rels": len(rels),
        "label_conformance": round(label_ok / len(labels), 4) if labels else 0.0,
        "rel_conformance": round(rtype_ok / len(rtypes), 4) if rtypes else 0.0,
        "extraction_score": sc["overall"],
        "labels": labels,
    }


def _aggregate(per_doc):
    ok = [d for d in per_doc if "error" not in d]
    if not ok:
        return {"docs_ok": 0}
    all_labels = [l for d in ok for l in d["labels"]]
    top1 = 0.0
    if all_labels:
        top1 = round(Counter(all_labels).most_common(1)[0][1] / len(all_labels), 4)
    return {
        "docs_ok": len(ok),
        "mean_nodes": round(statistics.mean([d["nodes"] for d in ok]), 3),
        "mean_rels": round(statistics.mean([d["rels"] for d in ok]), 3),
        "mean_label_conformance": round(statistics.mean([d["label_conformance"] for d in ok]), 4),
        "mean_rel_conformance": round(statistics.mean([d["rel_conformance"] for d in ok]), 4),
        "mean_extraction_score": round(statistics.mean([d["extraction_score"] for d in ok]), 4),
        "distinct_labels": len(set(all_labels)),
        "label_entropy_bits": _entropy(all_labels),
        "top1_label_share": top1,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-category", type=int, default=8)
    ap.add_argument("--max-chars", type=int, default=3000)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    key = re.search(r'ontology_guardrail_mara_api_key\s*=\s*"([^"]+)"',
                    Path(".env").read_text(encoding="utf-8")).group(1)

    docs = load_finder(args.per_category, args.max_chars)
    cats = Counter(d["category"] for d in docs)
    print(f"sampled {len(docs)} docs across {len(cats)} categories: {dict(cats)}")

    ontos = {arm: Ontology.load(path) for arm, path in ARMS.items()}
    cq_path = Path("examples/finder/datasets/competency_questions.yaml")
    cqs = None
    if cq_path.exists():
        import yaml
        with open(cq_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        cqs = raw.get("competency_questions", raw) if isinstance(raw, dict) else raw
    scorecards = {arm: score_ontology(o, competency_questions=cqs).to_dict() for arm, o in ontos.items()}
    for arm, sc in scorecards.items():
        print(f"scorecard {arm}: {sc['grade']} ({sc['overall_score']})")

    # One backend per model, shared across threads (openai client is thread-safe
    # for independent requests). Calls are network I/O bound, so a thread pool
    # over the full (arm, model, doc) grid gives a large speedup.
    backends = {m: create_llm_backend(provider="mara", model=m, api_key=key) for m in MODELS}
    tasks = [(arm, m, di, doc) for arm in ontos for m in MODELS for di, doc in enumerate(docs)]

    done = {"n": 0}
    lock = Lock()

    def run_task(t):
        arm, m, di, doc = t
        try:
            r = backends[m].complete(
                system=_EXTRACT_SYSTEM, user=extraction_prompt(ontos[arm], doc["text"]),
                temperature=0.0, max_tokens=4096, response_format={"type": "json_object"})
            md = _doc_metrics(_parse_graph(r.text), ontos[arm])
        except Exception as e:
            md = {"error": f"{type(e).__name__}: {str(e)[:80]}"}
        md["category"] = doc["category"]
        with lock:
            done["n"] += 1
            if done["n"] % 25 == 0 or done["n"] == len(tasks):
                print(f"  ... {done['n']}/{len(tasks)} extractions done")
        return arm, m, di, md

    # collect, preserving per-(arm,model) doc order by index
    buckets = {arm: {m: [None] * len(docs) for m in MODELS} for arm in ontos}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for arm, m, di, md in pool.map(run_task, tasks):
            buckets[arm][m][di] = md

    results = {}
    for arm in ontos:
        results[arm] = {"per_model": {}}
        for m in MODELS:
            per_doc = buckets[arm][m]
            agg = _aggregate(per_doc)
            results[arm]["per_model"][m] = {"aggregate": agg, "per_doc": per_doc}
            print(f"[{arm} / {m}] nodes={agg.get('mean_nodes')} conf={agg.get('mean_label_conformance')} "
                  f"score={agg.get('mean_extraction_score')} entropy={agg.get('label_entropy_bits')} "
                  f"distinct={agg.get('distinct_labels')}")

    # cross-model means per arm
    summary = {}
    keys = ("mean_nodes", "mean_rels", "mean_label_conformance", "mean_rel_conformance",
            "mean_extraction_score", "distinct_labels", "label_entropy_bits", "top1_label_share")
    for arm in ontos:
        aggs = [results[arm]["per_model"][m]["aggregate"] for m in MODELS
                if results[arm]["per_model"][m]["aggregate"].get("docs_ok")]
        summary[arm] = {k: round(statistics.mean([a[k] for a in aggs]), 4) for k in keys} if aggs else {}
    if all(summary.values()):
        summary["delta_B_minus_A"] = {
            k: round(summary["B_rich_fibo_plus"][k] - summary["A_sparse_fibo_minus"][k], 4) for k in keys
        }

    record = {
        "experiment": "finder-guardrail-ablation",
        "corpus": "Linq-AI-Research/FinDER",
        "sample": {"per_category": args.per_category, "total_docs": len(docs),
                   "max_chars": args.max_chars, "categories": dict(cats)},
        "models": MODELS,
        "arms": ARMS,
        "scorecards": scorecards,
        "results": results,
        "summary": summary,
    }
    Path(args.out).write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n[written] {args.out}")
    if "delta_B_minus_A" in summary:
        a, b, d = summary["A_sparse_fibo_minus"], summary["B_rich_fibo_plus"], summary["delta_B_minus_A"]
        print(f"\nCROSS-MODEL MEANS:")
        print(f"  A (sparse): nodes={a['mean_nodes']} conf={a['mean_label_conformance']} score={a['mean_extraction_score']} entropy={a['label_entropy_bits']} distinct={a['distinct_labels']}")
        print(f"  B (rich)  : nodes={b['mean_nodes']} conf={b['mean_label_conformance']} score={b['mean_extraction_score']} entropy={b['label_entropy_bits']} distinct={b['distinct_labels']}")
        print(f"  Δ(B-A)    : nodes={d['mean_nodes']:+} conf={d['mean_label_conformance']:+} score={d['mean_extraction_score']:+} entropy={d['label_entropy_bits']:+}")


if __name__ == "__main__":
    main()
