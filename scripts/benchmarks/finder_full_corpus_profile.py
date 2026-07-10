"""Full-corpus FinDER profile: open-extract ALL 5,703 references once to build the
definitive corpus profile, then score the guardrail candidates against it (ADR-0143).

Resilient: each doc's extracted labels are appended to a JSONL as they complete, so
the run is resumable (--resume skips already-done ids). The profile is aggregated
from the JSONL; the selector then ranks curated + lexical + stable-bridged FIBO
candidates against the full profile.

Key from .env. Run:
  PYTHONPATH=src python3 scripts/benchmarks/finder_full_corpus_profile.py \
     --jsonl outputs/finder_full_open.jsonl --out docs/decisions/ADR-0143-full-corpus.json
"""

from __future__ import annotations

import argparse
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

from seocho.llm_structured import structured_complete
from seocho.store.llm import create_llm_backend

_OPEN_SYS = ("You extract entities from financial text. For each entity give a short, GENERAL type "
             "label (Company, Person, FinancialMetric, Regulation, Risk, Product, Exchange, ...). "
             "Do NOT use a fixed schema. Return ONLY JSON.")
_OPEN_USER = 'TEXT:\n{doc}\n\nReturn JSON: {{"nodes":[{{"label":"<GeneralType>","name":"..."}}]}}'


def _retry(fn, attempts=5, base=2.0):
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            last = e
            if any(s in str(e).lower() for s in ("429", "rate limit", "timeout", "temporarily")):
                time.sleep(base * (2 ** i)); continue
            raise
    raise last


def open_extract_all(jsonl_path, *, model, workers, max_chars, resume):
    from huggingface_hub import hf_hub_download
    import pandas as pd
    df = pd.read_parquet(hf_hub_download("Linq-AI-Research/FinDER", "data/train-00000-of-00001.parquet", repo_type="dataset"))
    jp = Path(jsonl_path); jp.parent.mkdir(parents=True, exist_ok=True)
    done_ids = set()
    if resume and jp.exists():
        with open(jp, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    done_ids.add(json.loads(line)["id"])
                except Exception:
                    pass
    docs = []
    for _, row in df.iterrows():
        _id = str(row["_id"])
        if _id in done_ids:
            continue
        refs = row["references"]
        text = " ".join(map(str, refs)) if hasattr(refs, "__iter__") and not isinstance(refs, str) else str(refs)
        if len(text.strip()) < 40:
            continue
        docs.append({"id": _id, "category": str(row["category"]), "text": text.strip()[:max_chars]})
    print(f"total in corpus minus done = {len(docs)} to extract ({len(done_ids)} resumed)", flush=True)

    with open(".env", "r", encoding="utf-8") as env_file:
        key = re.search(r'ontology_guardrail_mara_api_key\s*=\s*"([^"]+)"', env_file.read()).group(1)
    be = create_llm_backend(provider="mara", model=model, api_key=key)
    fh = jp.open("a", encoding="utf-8"); lock = Lock(); done = {"n": 0}

    def run(doc):
        try:
            ex = _retry(lambda: structured_complete(be, system=_OPEN_SYS, user=_OPEN_USER.format(doc=doc["text"]),
                        model=model, task_hint="json_extraction"))
            labels = [str(n.get("label", "")).strip() for n in ex.get("nodes", []) if isinstance(n, dict)]
        except Exception:
            labels = []
        rec = {"id": doc["id"], "category": doc["category"], "labels": [l for l in labels if l]}
        with lock:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n"); fh.flush()
            done["n"] += 1
            if done["n"] % 200 == 0:
                print(f"  ... {done['n']}/{len(docs)}", flush=True)
        return None

    if docs:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(run, docs))
    fh.close()


def build_profile_from_jsonl(jsonl_path):
    from collections import Counter
    freqs = Counter(); ndoc = 0
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            ndoc += 1
            for l in rec.get("labels", []):
                freqs[l] += 1
    return dict(freqs), ndoc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", default="outputs/finder_full_open.jsonl")
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="DeepSeek-V3.1")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--max-chars", type=int, default=2500)
    ap.add_argument("--no-resume", action="store_true")
    ap.add_argument("--skip-extract", action="store_true", help="only aggregate + score from existing jsonl")
    args = ap.parse_args()

    if not args.skip_extract:
        open_extract_all(args.jsonl, model=args.model, workers=args.workers, max_chars=args.max_chars, resume=not args.no_resume)

    freqs, ndoc = build_profile_from_jsonl(args.jsonl)
    print(f"profile: {ndoc} docs, {len(freqs)} distinct labels", flush=True)

    # score candidates against the full-corpus profile
    from seocho.fibo_catalog import (load_catalog, fibo_guardrail_candidates, bridge_to_corpus,
                                     semantic_bridge, derive_fibo_roots_stable)
    from seocho.guardrail_selector import select_guardrail, numeric_intensity
    from seocho.ontology_scorecard import CorpusProfile, score_ontology
    from seocho.ontology import Ontology
    from seocho.store.llm import create_llm_backend

    cp = CorpusProfile(label_frequencies={str(k): int(v) for k, v in freqs.items()},
                       doc_count=ndoc, source="FinDER full open-extraction")
    cat_path = "outputs/semantic_artifacts/fibo/latest/catalog.json"
    cands = {"curated_plus": Ontology.load("examples/datasets/fibo_plus.jsonld")}
    stable_cov = {}
    if Path(cat_path).exists():
        cat = load_catalog(cat_path)
        gterms = sorted(cp.label_frequencies, key=lambda k: -cp.label_frequencies[k])[:20]
        with open(".env", "r", encoding="utf-8") as env_file:
            key = re.search(r'ontology_guardrail_mara_api_key\s*=\s*"([^"]+)"', env_file.read()).group(1)
        bes = [create_llm_backend(provider="mara", model=m, api_key=key) for m in ["DeepSeek-V3.1", "MiniMax-M2.5", "gpt-oss-120b"]]
        for m, o in fibo_guardrail_candidates(cat).items():
            seed = derive_fibo_roots_stable(gterms, o, backends=bes, models=["DeepSeek-V3.1", "MiniMax-M2.5", "gpt-oss-120b"], passes=2)
            cands[f"fibo_{m}_stable"] = semantic_bridge(bridge_to_corpus(o, cp), seed)

    def cov(o):
        d = score_ontology(o, corpus_profile=cp, profile="guardrail").dimension("corpus_coverage")
        return round(d.score, 4) if d else 0.0
    coverage = {n: cov(o) for n, o in cands.items()}
    rec = select_guardrail(cands, cp)
    top = sorted(cp.label_frequencies.items(), key=lambda kv: -kv[1])[:25]
    out = {"experiment": "finder-full-corpus-profile", "doc_count": ndoc,
           "distinct_labels": len(freqs), "numeric_intensity": numeric_intensity(cp),
           "top_labels": top, "coverage": coverage, "chosen": rec.chosen,
           "corpus_profile": cp.to_dict()}
    Path(args.out).write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n[written] {args.out}")
    print(f"docs={ndoc} labels={len(freqs)} numeric_intensity={out['numeric_intensity']}")
    print("coverage:", json.dumps(coverage))
    print("chosen:", rec.chosen)


if __name__ == "__main__":
    main()
