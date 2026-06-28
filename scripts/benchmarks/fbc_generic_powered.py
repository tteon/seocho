"""Powered confirmation: fibo_fbc_generic vs curated_plus on FinDER answers, with
a LARGER N, a SECOND independent judge, and proper paired statistics (ADR-0138).

ADR-0137 found fibo_fbc_generic ≥ curated_plus on N=15 (directional). This widens
to all 8 categories, adds a second-model judge (consensus = both judges agree
correct), and reports McNemar's paired test + a bootstrap 95% CI on the accuracy
difference — a powered, statistically-defensible comparison.

Key from .env. Run:
  PYTHONPATH=src python3 scripts/benchmarks/fbc_generic_powered.py --per-category 12 --out <file>
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

from seocho.fibo_catalog import (
    FINDER_FIBO_ROOTS,
    bridge_to_corpus,
    catalog_module_to_ontology,
    catalog_provenance,
    load_catalog,
    semantic_bridge,
)
from seocho.guardrail_selector import load_corpus_profile
from seocho.llm_structured import structured_complete
from seocho.ontology import NodeDef, Ontology
from seocho.store.llm import create_llm_backend

CATS = [
    "Accounting",
    "Company overview",
    "Financials",
    "Footnotes",
    "Governance",
    "Legal",
    "Risk",
    "Shareholder return",
]

_ANS_SYS = (
    "You are a financial analyst. Use ONLY the entity types in the provided ontology to "
    "extract the relevant facts, then answer the question. Return ONLY JSON."
)
_JUDGE_SYS = "You grade financial answers. Return ONLY JSON."
_JUDGE_USER = (
    "QUESTION: {q}\nGOLD: {gold}\nMODEL ANSWER: {ans}\n"
    "Is the model answer correct vs gold (same entity/number/fact, allowing phrasing/"
    'rounding)? Return JSON {{"correct": true|false}}'
)


def _ans_user(onto: Ontology, ctx: str, q: str) -> str:
    c = onto.to_extraction_context()
    return (
        f"ONTOLOGY ENTITY TYPES:\n{c.get('entity_types','')}\n\nFINANCIAL TEXT:\n{ctx}\n\n"
        f'QUESTION: {q}\n\nReturn JSON: {{"facts":[{{"label":"...","name":"..."}}],"answer":"..."}}'
    )


def _retry(fn, attempts=5, base=2.0):
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            last = e
            if any(
                s in str(e).lower()
                for s in ("429", "rate limit", "timeout", "temporarily")
            ):
                time.sleep(base * (2**i))
                continue
            raise
    raise last


def build_fbc_generic(catalog_path, corpus_path):
    cat = load_catalog(catalog_path)
    cp = load_corpus_profile(corpus_path)
    fbc = semantic_bridge(
        bridge_to_corpus(catalog_module_to_ontology(cat, "FBC"), cp), FINDER_FIBO_ROOTS
    )
    generic = set(cp.label_frequencies) | set(FINDER_FIBO_ROOTS)
    terms = sorted({a for nd in fbc.nodes.values() for a in nd.aliases if a in generic})
    return Ontology(
        "fibo_fbc_generic",
        package_id="fibo.FBC.generic",
        version=catalog_provenance(cat)["fibo_commit"][:12],
        nodes={t: NodeDef(description=f"{t} (FIBO-FBC derived).") for t in terms},
    )


def mcnemar(b: int, c: int):
    """Paired binary test. b = A-correct&B-wrong, c = B-correct&A-wrong.
    Continuity-corrected chi-square, df=1; p via erfc (no scipy)."""
    n = b + c
    if n == 0:
        return {"b": b, "c": c, "chi2": 0.0, "p_value": 1.0}
    chi2 = (abs(b - c) - 1) ** 2 / n
    p = math.erfc(math.sqrt(chi2 / 2.0))
    return {"b": b, "c": c, "chi2": round(chi2, 4), "p_value": round(p, 4)}


def bootstrap_ci(diffs, *, iters=5000, seed=7):
    rng = random.Random(seed)
    n = len(diffs)
    if n == 0:
        return [0.0, 0.0]
    means = []
    for _ in range(iters):
        s = sum(diffs[rng.randrange(n)] for _ in range(n)) / n
        means.append(s)
    means.sort()
    return [round(means[int(0.025 * iters)], 4), round(means[int(0.975 * iters)], 4)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--catalog", default="outputs/semantic_artifacts/fibo/latest/catalog.json"
    )
    ap.add_argument(
        "--corpus", default="docs/decisions/ADR-0116-corpus-aware-scorecard.json"
    )
    ap.add_argument("--per-category", type=int, default=12)
    ap.add_argument("--answer-model", default="DeepSeek-V3.1")
    ap.add_argument("--judge2-model", default="gpt-oss-120b")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    from huggingface_hub import hf_hub_download
    import pandas as pd

    key = re.search(
        r'ontology_guardrail_mara_api_key\s*=\s*"([^"]+)"', Path(".env").read_text()
    ).group(1)
    ontos = {
        "curated_plus": Ontology.load("examples/datasets/fibo_plus.jsonld"),
        "fibo_fbc_generic": build_fbc_generic(args.catalog, args.corpus),
    }
    df = pd.read_parquet(
        hf_hub_download(
            "Linq-AI-Research/FinDER",
            "data/train-00000-of-00001.parquet",
            repo_type="dataset",
        )
    )
    cases = []
    for c in CATS:
        for _, r in (
            df[df["category"] == c]
            .sort_values("_id")
            .head(args.per_category)
            .iterrows()
        ):
            rf = r["references"]
            ctx = (
                " ".join(map(str, rf))
                if hasattr(rf, "__iter__") and not isinstance(rf, str)
                else str(rf)
            )
            cases.append(
                {
                    "id": str(r["_id"]),
                    "category": c,
                    "ctx": ctx[:3000],
                    "q": str(r["text"]),
                    "gold": str(r["answer"]),
                }
            )
    print(
        f"{len(cases)} cases; curated_plus={len(ontos['curated_plus'].nodes)} cls, "
        f"fibo_fbc_generic={len(ontos['fibo_fbc_generic'].nodes)} cls; judges={args.answer_model}+{args.judge2_model}",
        flush=True,
    )

    be_ans = create_llm_backend(provider="mara", model=args.answer_model, api_key=key)
    be_j2 = create_llm_backend(provider="mara", model=args.judge2_model, api_key=key)
    done = {"n": 0}
    lock = Lock()

    def judge(be, model, q, gold, ans):
        jc = _retry(
            lambda: structured_complete(
                be,
                system=_JUDGE_SYS,
                user=_JUDGE_USER.format(q=q, gold=gold, ans=ans),
                model=model,
            )
        )
        return bool(jc.get("correct"))

    def run(case):
        o = {"id": case["id"], "category": case["category"]}
        try:
            for arm, onto in ontos.items():
                ex = _retry(
                    lambda: structured_complete(
                        be_ans,
                        system=_ANS_SYS,
                        user=_ans_user(onto, case["ctx"], case["q"]),
                        model=args.answer_model,
                        task_hint="json_extraction",
                    )
                )
                ans = str(ex.get("answer", "")) if isinstance(ex, dict) else ""
                j1 = judge(be_ans, args.answer_model, case["q"], case["gold"], ans)
                j2 = judge(be_j2, args.judge2_model, case["q"], case["gold"], ans)
                o[arm] = {"j1": j1, "j2": j2, "consensus": j1 and j2}
        except Exception as e:
            o["error"] = f"{type(e).__name__}: {str(e)[:80]}"
        with lock:
            done["n"] += 1
            if done["n"] % 20 == 0 or done["n"] == len(cases):
                print(f"  ... {done['n']}/{len(cases)}", flush=True)
        return o

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run, cases))

    scored = [
        r
        for r in results
        if "error" not in r and "curated_plus" in r and "fibo_fbc_generic" in r
    ]

    def acc(arm, key):
        return (
            round(sum(1 for r in scored if r[arm][key]) / len(scored), 4)
            if scored
            else 0.0
        )

    # paired consensus correctness
    A = [r["curated_plus"]["consensus"] for r in scored]
    B = [r["fibo_fbc_generic"]["consensus"] for r in scored]
    b = sum(1 for a, x in zip(A, B) if a and not x)  # curated right, fibo wrong
    c = sum(1 for a, x in zip(A, B) if x and not a)  # fibo right, curated wrong
    diffs = [int(x) - int(a) for a, x in zip(A, B)]
    # inter-judge agreement (over both arms)
    j_pairs = [(r[arm]["j1"], r[arm]["j2"]) for r in scored for arm in ontos]
    agree = (
        round(sum(1 for x, y in j_pairs if x == y) / len(j_pairs), 4)
        if j_pairs
        else None
    )

    summary = {
        "n_scored": len(scored),
        "answer_model": args.answer_model,
        "judge2_model": args.judge2_model,
        "curated_plus": {
            "consensus_acc": acc("curated_plus", "consensus"),
            "judge1_acc": acc("curated_plus", "j1"),
            "judge2_acc": acc("curated_plus", "j2"),
        },
        "fibo_fbc_generic": {
            "consensus_acc": acc("fibo_fbc_generic", "consensus"),
            "judge1_acc": acc("fibo_fbc_generic", "j1"),
            "judge2_acc": acc("fibo_fbc_generic", "j2"),
        },
        "accuracy_delta_consensus": round(
            acc("fibo_fbc_generic", "consensus") - acc("curated_plus", "consensus"), 4
        ),
        "mcnemar": mcnemar(b, c),
        "bootstrap_95ci_delta": bootstrap_ci(diffs),
        "inter_judge_agreement": agree,
    }
    Path(args.out).write_text(
        json.dumps(
            {
                "experiment": "fbc-generic-powered",
                "summary": summary,
                "results": results,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\n[written] {args.out}\n{json.dumps(summary, indent=2)}", flush=True)


if __name__ == "__main__":
    main()
