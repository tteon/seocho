"""Powered FinDER answer comparison with the IMPROVED guardrail (ADR-0141):
stable-bridged-FIBO-FBC (multi-model + 2-pass auto seed, ADR-0140, ZERO hand
curation) collapsed to its generic vocabulary, vs the hand-curated fibo_plus.
Large stratified N, 2 judges, McNemar + bootstrap CI.

Key from .env. Run:
  PYTHONPATH=src python3 scripts/benchmarks/fbc_stable_powered.py --per-category 50 --out <file>
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

from seocho.fibo_catalog import (bridge_to_corpus, catalog_module_to_ontology, catalog_provenance,
                                 derive_fibo_roots_stable, load_catalog, semantic_bridge)
from seocho.guardrail_selector import load_corpus_profile
from seocho.llm_structured import structured_complete
from seocho.ontology import NodeDef, Ontology
from seocho.store.llm import create_llm_backend

CATS = ["Accounting", "Company overview", "Financials", "Footnotes",
        "Governance", "Legal", "Risk", "Shareholder return"]
DERIVE_MODELS = ["DeepSeek-V3.1", "MiniMax-M2.5", "gpt-oss-120b"]

_ANS_SYS = ("You are a financial analyst. Use ONLY the entity types in the provided ontology to "
            "extract the relevant facts, then answer the question. Return ONLY JSON.")
_JUDGE_SYS = "You grade financial answers. Return ONLY JSON."
_JUDGE_USER = ('QUESTION: {q}\nGOLD: {gold}\nMODEL ANSWER: {ans}\nIs the model answer correct vs gold '
               '(same entity/number/fact, allowing phrasing/rounding)? Return JSON {{"correct": true|false}}')


def _ans_user(o, ctx, q):
    c = o.to_extraction_context()
    return (f"ONTOLOGY ENTITY TYPES:\n{c.get('entity_types','')}\n\nFINANCIAL TEXT:\n{ctx}\n\n"
            f"QUESTION: {q}\n\nReturn JSON: {{\"facts\":[{{\"label\":\"...\",\"name\":\"...\"}}],\"answer\":\"...\"}}")


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


def mcnemar(b, c):
    n = b + c
    if n == 0:
        return {"b": b, "c": c, "chi2": 0.0, "p_value": 1.0}
    chi2 = (abs(b - c) - 1) ** 2 / n
    return {"b": b, "c": c, "chi2": round(chi2, 4), "p_value": round(math.erfc(math.sqrt(chi2 / 2.0)), 4)}


def bootstrap_ci(diffs, iters=5000, seed=7):
    rng = random.Random(seed); n = len(diffs)
    if n == 0:
        return [0.0, 0.0]
    ms = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(iters))
    return [round(ms[int(0.025 * iters)], 4), round(ms[int(0.975 * iters)], 4)]


def build_fbc_stable_generic(catalog_path, corpus_path, key):
    cat = load_catalog(catalog_path); cp = load_corpus_profile(corpus_path)
    fbc = catalog_module_to_ontology(cat, "FBC")
    gterms = sorted(cp.label_frequencies, key=lambda k: -cp.label_frequencies[k])[:20]
    bes = [create_llm_backend(provider="mara", model=m, api_key=key) for m in DERIVE_MODELS]
    seed = derive_fibo_roots_stable(gterms, fbc, backends=bes, models=DERIVE_MODELS, passes=2)
    bridged = semantic_bridge(bridge_to_corpus(fbc, cp), seed)
    generic = set(cp.label_frequencies) | set(seed)
    terms = sorted({a for nd in bridged.nodes.values() for a in nd.aliases if a in generic})
    onto = Ontology("fibo_fbc_stable", package_id="fibo.FBC.stable",
                    version=catalog_provenance(cat)["fibo_commit"][:12],
                    nodes={t: NodeDef(description=f"{t} (FIBO-FBC stable-auto derived).") for t in terms})
    return onto, seed, terms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default="outputs/semantic_artifacts/fibo/latest/catalog.json")
    ap.add_argument("--corpus", default="docs/decisions/ADR-0116-corpus-aware-scorecard.json")
    ap.add_argument("--per-category", type=int, default=50)
    ap.add_argument("--answer-model", default="DeepSeek-V3.1")
    ap.add_argument("--judge2-model", default="gpt-oss-120b")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    from huggingface_hub import hf_hub_download
    import pandas as pd

    key = re.search(r'ontology_guardrail_mara_api_key\s*=\s*"([^"]+)"', Path(".env").read_text()).group(1)
    print("building stable-bridged FBC generic guardrail (multi-model derive)...", flush=True)
    fbc_stable, seed, terms = build_fbc_stable_generic(args.catalog, args.corpus, key)
    ontos = {"curated_plus": Ontology.load("examples/datasets/fibo_plus.jsonld"), "fibo_fbc_stable": fbc_stable}
    print(f"fibo_fbc_stable = {len(terms)} generic terms; seed={json.dumps(seed)[:300]}", flush=True)

    df = pd.read_parquet(hf_hub_download("Linq-AI-Research/FinDER", "data/train-00000-of-00001.parquet", repo_type="dataset"))
    cases = []
    for c in CATS:
        for _, r in df[df["category"] == c].sort_values("_id").head(args.per_category).iterrows():
            rf = r["references"]; ctx = " ".join(map(str, rf)) if hasattr(rf, "__iter__") and not isinstance(rf, str) else str(rf)
            cases.append({"id": str(r["_id"]), "category": c, "ctx": ctx[:3000], "q": str(r["text"]), "gold": str(r["answer"])})
    print(f"{len(cases)} cases; curated_plus={len(ontos['curated_plus'].nodes)} cls, fibo_fbc_stable={len(fbc_stable.nodes)} cls", flush=True)

    be_ans = create_llm_backend(provider="mara", model=args.answer_model, api_key=key)
    be_j2 = create_llm_backend(provider="mara", model=args.judge2_model, api_key=key)
    done = {"n": 0}; lock = Lock()

    def jdg(be, model, q, gold, ans):
        return bool(_retry(lambda: structured_complete(be, system=_JUDGE_SYS,
                    user=_JUDGE_USER.format(q=q, gold=gold, ans=ans), model=model)).get("correct"))

    def run(case):
        o = {"id": case["id"], "category": case["category"]}
        try:
            for arm, onto in ontos.items():
                ex = _retry(lambda: structured_complete(be_ans, system=_ANS_SYS,
                            user=_ans_user(onto, case["ctx"], case["q"]), model=args.answer_model, task_hint="json_extraction"))
                ans = str(ex.get("answer", "")) if isinstance(ex, dict) else ""
                j1 = jdg(be_ans, args.answer_model, case["q"], case["gold"], ans)
                j2 = jdg(be_j2, args.judge2_model, case["q"], case["gold"], ans)
                o[arm] = {"j1": j1, "j2": j2, "consensus": j1 and j2}
        except Exception as e:
            o["error"] = f"{type(e).__name__}: {str(e)[:80]}"
        with lock:
            done["n"] += 1
            if done["n"] % 40 == 0 or done["n"] == len(cases):
                print(f"  ... {done['n']}/{len(cases)}", flush=True)
        return o

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run, cases))

    scored = [r for r in results if "error" not in r and "curated_plus" in r and "fibo_fbc_stable" in r]
    def acc(arm, k): return round(sum(1 for r in scored if r[arm][k]) / len(scored), 4) if scored else 0.0
    A = [r["curated_plus"]["consensus"] for r in scored]
    B = [r["fibo_fbc_stable"]["consensus"] for r in scored]
    b = sum(1 for a, x in zip(A, B) if a and not x); c = sum(1 for a, x in zip(A, B) if x and not a)
    diffs = [int(x) - int(a) for a, x in zip(A, B)]
    jp = [(r[arm]["j1"], r[arm]["j2"]) for r in scored for arm in ontos]
    summary = {
        "n_scored": len(scored), "answer_model": args.answer_model, "judge2_model": args.judge2_model,
        "fibo_fbc_stable_terms": terms, "derive_models": DERIVE_MODELS,
        "curated_plus": {"consensus_acc": acc("curated_plus", "consensus"), "j1": acc("curated_plus", "j1"), "j2": acc("curated_plus", "j2")},
        "fibo_fbc_stable": {"consensus_acc": acc("fibo_fbc_stable", "consensus"), "j1": acc("fibo_fbc_stable", "j1"), "j2": acc("fibo_fbc_stable", "j2")},
        "accuracy_delta_consensus": round(acc("fibo_fbc_stable", "consensus") - acc("curated_plus", "consensus"), 4),
        "mcnemar": mcnemar(b, c), "bootstrap_95ci_delta": bootstrap_ci(diffs),
        "inter_judge_agreement": round(sum(1 for x, y in jp if x == y) / len(jp), 4) if jp else None,
    }
    Path(args.out).write_text(json.dumps({"experiment": "fbc-stable-powered", "provenance": catalog_provenance(args.catalog),
                                          "summary": summary, "results": results}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n[written] {args.out}\n{json.dumps(summary, indent=2)}", flush=True)


if __name__ == "__main__":
    main()
