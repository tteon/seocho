"""Comprehensive FinDER guardrail verification: does the ontology guardrail improve
downstream ANSWER correctness across ALL case types (8 categories × lookup/reasoning)?

Prior runs measured extraction CONFORMANCE (ADR-0115/0118) and numeric-fact
VALIDATION (P3/ADR-0119). The open gap: whether the guardrail improves the thing
users actually want — correct ANSWERS — across the full case space. FinDER's
reasoning cases live only in Company overview + Financials; the other 6 categories
are lookup. This builds the full category × arm matrix.

Per case × arm (sparse fibo_minus vs rich fibo_plus, injected as guardrail):
  extract facts + answer (one robust structured call via ub5) → LLM-judge answer
  correctness vs FinDER gold. Reports per-category answer accuracy under each arm
  + delta, so we see WHERE the guardrail helps answering and develop accordingly.

Key from .env. Run:
  PYTHONPATH=src python3 scripts/benchmarks/finder_guardrail_answer_matrix.py \
     --per-category 12 --max-chars 3500 --workers 16 --out <file.json>
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock


def _with_retry(fn, *, attempts: int = 5, base: float = 2.0):
    """Retry on rate-limit / transient errors with exponential backoff."""
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            msg = str(e).lower()
            if (
                "429" in msg
                or "rate limit" in msg
                or "timeout" in msg
                or "temporarily" in msg
            ):
                time.sleep(base * (2**i))
                continue
            raise
    raise last


from seocho.ontology import Ontology
from seocho.llm_structured import StructuredOutputError, structured_complete
from seocho.store.llm import create_llm_backend

ARMS = {
    "A_sparse": "examples/datasets/fibo_minus.jsonld",
    "B_rich": "examples/datasets/fibo_plus.jsonld",
}

_ANS_SYS = (
    "You are a financial analyst. Use ONLY the entity/relationship types in the provided "
    "ontology to extract the relevant facts, then answer the question. Return ONLY JSON."
)


def _ans_user(onto: Ontology, refs: str, q: str) -> str:
    ctx = onto.to_extraction_context()
    return (
        f"ONTOLOGY ENTITY TYPES:\n{ctx.get('entity_types','')}\n\n"
        f"ONTOLOGY RELATIONSHIP TYPES:\n{ctx.get('relationship_types','')}\n\n"
        f"FINANCIAL TEXT:\n{refs}\n\nQUESTION: {q}\n\n"
        'Return JSON: {"facts":[{"label":"...","name":"...","value":"..."}],"answer":"..."}'
    )


_JUDGE_SYS = "You grade financial answers. Return ONLY JSON."
_JUDGE_USER = (
    "QUESTION: {q}\nGOLD: {gold}\nMODEL ANSWER: {ans}\n"
    "Is the model answer correct vs gold (same entity/number/fact, allowing phrasing/rounding)? "
    'Return JSON {{"correct": true|false}}'
)


def load_matrix(per_category: int, max_chars: int):
    from huggingface_hub import hf_hub_download
    import pandas as pd

    df = pd.read_parquet(
        hf_hub_download(
            "Linq-AI-Research/FinDER",
            "data/train-00000-of-00001.parquet",
            repo_type="dataset",
        )
    )
    df["reasoning_bool"] = df["reasoning"].astype(str).isin(["True", "true"])
    cases = []
    for cat, g in df.sort_values("_id").groupby("category"):
        # balance lookup/reasoning when both exist
        kinds = (
            [True, False]
            if g["reasoning_bool"].nunique() > 1
            else [g["reasoning_bool"].iloc[0]]
        )
        per_kind = max(1, per_category // len(kinds))
        for kind in kinds:
            sub = g[g["reasoning_bool"] == kind]
            taken = 0
            for _, row in sub.iterrows():
                refs = row["references"]
                text = (
                    " ".join(map(str, refs))
                    if hasattr(refs, "__iter__") and not isinstance(refs, str)
                    else str(refs)
                )
                if len(text.strip()) < 80:
                    continue
                cases.append(
                    {
                        "id": str(row["_id"]),
                        "category": str(cat),
                        "kind": "reasoning" if kind else "lookup",
                        "refs": text.strip()[:max_chars],
                        "q": str(row["text"]),
                        "gold": str(row["answer"]),
                    }
                )
                taken += 1
                if taken >= per_kind:
                    break
    return cases


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-category", type=int, default=12)
    ap.add_argument("--max-chars", type=int, default=3500)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--model", default="DeepSeek-V3.1")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    key = re.search(
        r'ontology_guardrail_mara_api_key\s*=\s*"([^"]+)"', Path(".env").read_text()
    ).group(1)
    ontos = {arm: Ontology.load(p) for arm, p in ARMS.items()}
    be = create_llm_backend(provider="mara", model=args.model, api_key=key)
    cases = load_matrix(args.per_category, args.max_chars)
    print(f"{len(cases)} cases; running {len(cases)*len(ARMS)} answer+judge pairs")

    done = {"n": 0}
    lock = Lock()
    tasks = [(arm, ci) for arm in ontos for ci in range(len(cases))]

    def run(t):
        arm, ci = t
        case = cases[ci]
        out = {
            "arm": arm,
            "id": case["id"],
            "category": case["category"],
            "kind": case["kind"],
        }
        try:
            ex = _with_retry(
                lambda: structured_complete(
                    be,
                    system=_ANS_SYS,
                    user=_ans_user(ontos[arm], case["refs"], case["q"]),
                    model=args.model,
                    task_hint="json_extraction",
                )
            )
            ans = str(ex.get("answer", ""))
            facts = ex.get("facts", []) if isinstance(ex, dict) else []
            labels = [str(f.get("label", "")) for f in facts if isinstance(f, dict)]
            out["label_conformance"] = (
                round(sum(1 for l in labels if l in ontos[arm].nodes) / len(labels), 4)
                if labels
                else 0.0
            )
            jc = _with_retry(
                lambda: structured_complete(
                    be,
                    system=_JUDGE_SYS,
                    user=_JUDGE_USER.format(q=case["q"], gold=case["gold"], ans=ans),
                    model=args.model,
                )
            )
            out["correct"] = bool(jc.get("correct"))
        except StructuredOutputError as e:
            out["error"] = str(e)[:80]
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {str(e)[:80]}"
        with lock:
            done["n"] += 1
            if done["n"] % 40 == 0 or done["n"] == len(tasks):
                print(f"  ... {done['n']}/{len(tasks)}")
        return out

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run, tasks))

    # aggregate per category × arm
    acc = defaultdict(lambda: defaultdict(list))  # category -> arm -> [0/1]
    conf = defaultdict(lambda: defaultdict(list))
    kindacc = defaultdict(lambda: defaultdict(list))  # kind -> arm -> [0/1]
    for r in results:
        if "correct" not in r:
            continue
        acc[r["category"]][r["arm"]].append(1 if r["correct"] else 0)
        kindacc[r["kind"]][r["arm"]].append(1 if r["correct"] else 0)
        if "label_conformance" in r:
            conf[r["category"]][r["arm"]].append(r["label_conformance"])

    def _mean(xs):
        return round(statistics.mean(xs), 4) if xs else None

    per_category = {}
    for cat in sorted(acc):
        a, b = _mean(acc[cat]["A_sparse"]), _mean(acc[cat]["B_rich"])
        per_category[cat] = {
            "acc_sparse": a,
            "acc_rich": b,
            "acc_delta": round((b or 0) - (a or 0), 4),
            "conf_sparse": _mean(conf[cat]["A_sparse"]),
            "conf_rich": _mean(conf[cat]["B_rich"]),
            "n": len(acc[cat]["A_sparse"]),
        }
    by_kind = {
        k: {
            "acc_sparse": _mean(kindacc[k]["A_sparse"]),
            "acc_rich": _mean(kindacc[k]["B_rich"]),
        }
        for k in kindacc
    }
    overall = {
        "acc_sparse": _mean([v for c in acc.values() for v in c["A_sparse"]]),
        "acc_rich": _mean([v for c in acc.values() for v in c["B_rich"]]),
    }

    record = {
        "experiment": "finder-guardrail-answer-matrix",
        "model": args.model,
        "n_cases": len(cases),
        "overall": overall,
        "by_kind": by_kind,
        "per_category": per_category,
        "results": results,
    }
    Path(args.out).write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\n[written] {args.out}\n")
    print(
        f"OVERALL answer accuracy: sparse {overall['acc_sparse']}  rich {overall['acc_rich']}"
    )
    print(f"by kind: {by_kind}")
    print(
        f"{'category':20s} {'sparse':>7s} {'rich':>7s} {'Δacc':>7s}  {'conf A→B':>14s}"
    )
    for cat, m in per_category.items():
        print(
            f"{cat:20s} {str(m['acc_sparse']):>7s} {str(m['acc_rich']):>7s} {m['acc_delta']:>+7.3f}  "
            f"{str(m['conf_sparse'])}→{str(m['conf_rich'])}"
        )


if __name__ == "__main__":
    main()
