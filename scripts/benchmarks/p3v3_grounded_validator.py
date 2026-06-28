"""P3 v3: does SOURCE-GROUNDED numeric validation (ADR-0131) recover recall on
"wrong-number-pulled" without wrecking precision — where OLD over-flagged
(recall 0.93 / FP 0.91) and NEW under-flagged (recall 0.00 / FP 0.045, ADR-0130)?

Same FinDER numeric protocol; per case compute three validator verdicts on the
extracted facts: OLD (SHACL+rigid rules), NEW (soft isolated-fact), GROUNDED
(value present in the source references). Metrics: recall on structural-wrong +
false-positive on correct.

Run: PYTHONPATH=src python3 scripts/benchmarks/p3v3_grounded_validator.py \
       --per-type 16 --workers 6 --out <file.json>
"""

from __future__ import annotations

import argparse
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

from seocho.ontology import NodeDef, Ontology, P, RelDef
from seocho.llm_structured import structured_complete
from seocho.numeric_validation import ground_facts, validate_numeric_facts
from seocho.store.llm import create_llm_backend

NUMERIC_TYPES = ["Compositional", "Division", "Multiplication", "Subtract", "Addition"]
_OLD_UNITS = {
    "usd",
    "$",
    "dollars",
    "percent",
    "%",
    "shares",
    "ratio",
    "x",
    "eur",
    "gbp",
    "jpy",
    "",
}
_OLD_SCALES = {
    "",
    "ones",
    "thousand",
    "thousands",
    "million",
    "millions",
    "billion",
    "billions",
}
_PERIOD_RE = re.compile(
    r"(19|20)\d{2}|q[1-4]|fy|h[12]|first|second|third|fourth|quarter|annual", re.I
)
_OLD_ONTO = Ontology(
    "fin-numeric",
    version="1.0.0",
    nodes={
        "Company": NodeDef(
            description="c", properties={"name": P(str, unique=True, required=True)}
        ),
        "FinancialMetric": NodeDef(
            description="m",
            properties={
                "name": P(str, required=True),
                "value": P(float, required=True),
                "unit": P(str),
                "scale": P(str),
                "period": P(str, required=True),
                "company": P(str),
            },
        ),
    },
    relationships={
        "REPORTED": RelDef(
            source="Company", target="FinancialMetric", cardinality="ONE_TO_MANY"
        )
    },
)


def old_flags(facts) -> bool:
    nodes = [
        {"id": f"m{i}", "label": "FinancialMetric", "properties": f}
        for i, f in enumerate(facts)
        if isinstance(f, dict)
    ]
    try:
        if _OLD_ONTO.validate_with_shacl({"nodes": nodes, "relationships": []}):
            return True
    except Exception:
        return True
    for f in facts:
        if not isinstance(f, dict):
            return True
        v = f.get("value")
        try:
            (
                float(str(v).replace(",", "").replace("$", "").replace("%", ""))
                if v not in (None, "")
                else None
            )
        except Exception:
            return True
        u = str(f.get("unit", "")).strip().lower()
        sc = str(f.get("scale", "")).strip().lower()
        if (u and u not in _OLD_UNITS) or (sc and sc not in _OLD_SCALES):
            return True
        p = str(f.get("period", "")).strip()
        if not p or not _PERIOD_RE.search(p):
            return True
    return False


_SYS = "You are a financial analyst. Extract numeric facts, then answer with a number. Return ONLY JSON."
_USR = (
    'FINANCIAL TEXT:\n{refs}\n\nQUESTION: {q}\n\nReturn JSON: {{"facts":[{{"name":"...","value":<number>,'
    '"unit":"...","scale":"...","period":"...","company":"..."}}],"answer":"..."}}'
)
_JS = "You grade financial answers. Return ONLY JSON."
_CU = 'QUESTION: {q}\nGOLD: {gold}\nMODEL: {ans}\nIs MODEL numerically correct vs GOLD? JSON {{"correct":true|false}}'
_KU = (
    'QUESTION: {q}\nGOLD: {gold}\nMODEL: {ans}\nFACTS: {facts}\nAnswer is WRONG. Error "structural" '
    '(wrong/missing/mis-typed fact) or "arithmetic" (facts ok, math wrong)? JSON {{"error_type":"..."}}'
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


def load_cases(per_type, max_chars):
    from huggingface_hub import hf_hub_download
    import pandas as pd

    df = pd.read_parquet(
        hf_hub_download(
            "Linq-AI-Research/FinDER",
            "data/train-00000-of-00001.parquet",
            repo_type="dataset",
        )
    )
    cases = []
    for t in NUMERIC_TYPES:
        n = 0
        for _, row in df[df["type"] == t].sort_values("_id").iterrows():
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
                    "type": t,
                    "refs": text.strip()[:max_chars],
                    "q": str(row["text"]),
                    "gold": str(row["answer"]),
                }
            )
            n += 1
            if n >= per_type:
                break
    return cases


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-type", type=int, default=16)
    ap.add_argument("--max-chars", type=int, default=3500)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--model", default="DeepSeek-V3.1")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    key = re.search(
        r'ontology_guardrail_mara_api_key\s*=\s*"([^"]+)"', Path(".env").read_text()
    ).group(1)
    be = create_llm_backend(provider="mara", model=args.model, api_key=key)
    cases = load_cases(args.per_type, args.max_chars)
    print(f"{len(cases)} cases")
    done = {"n": 0}
    lock = Lock()

    def run(case):
        o = {"id": case["id"], "type": case["type"]}
        try:
            ex = _retry(
                lambda: structured_complete(
                    be,
                    system=_SYS,
                    user=_USR.format(refs=case["refs"], q=case["q"]),
                    model=args.model,
                    task_hint="json_extraction",
                )
            )
            facts = ex.get("facts", []) if isinstance(ex, dict) else []
            ans = str(ex.get("answer", ""))
            o["old_flagged"] = old_flags(facts)
            o["new_flagged"] = bool(validate_numeric_facts(facts).warnings)
            o["grounded_flagged"] = ground_facts(facts, case["refs"]).any_ungrounded
            jc = _retry(
                lambda: structured_complete(
                    be,
                    system=_JS,
                    user=_CU.format(q=case["q"], gold=case["gold"], ans=ans),
                    model=args.model,
                )
            )
            o["correct"] = bool(jc.get("correct"))
            if not o["correct"]:
                jt = _retry(
                    lambda: structured_complete(
                        be,
                        system=_JS,
                        user=_KU.format(
                            q=case["q"],
                            gold=case["gold"],
                            ans=ans,
                            facts=json.dumps(facts)[:1500],
                        ),
                        model=args.model,
                    )
                )
                o["error_type"] = jt.get("error_type")
        except Exception as e:
            o["error"] = f"{type(e).__name__}: {str(e)[:80]}"
        with lock:
            done["n"] += 1
            if done["n"] % 20 == 0 or done["n"] == len(cases):
                print(f"  ... {done['n']}/{len(cases)}")
        return o

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run, cases))

    scored = [r for r in results if "correct" in r]
    correct = [r for r in scored if r["correct"]]
    structural = [
        r for r in scored if not r["correct"] and r.get("error_type") == "structural"
    ]

    def rate(sub, key):
        return round(sum(1 for r in sub if r.get(key)) / len(sub), 4) if sub else None

    summary = {
        "n_scored": len(scored),
        "n_correct": len(correct),
        "n_structural_wrong": len(structural),
    }
    for v, key in [
        ("OLD", "old_flagged"),
        ("NEW", "new_flagged"),
        ("GROUNDED", "grounded_flagged"),
    ]:
        summary[v] = {
            "recall_on_structural": rate(structural, key),
            "false_positive_on_correct": rate(correct, key),
        }
    rec = {
        "experiment": "P3v3-grounded-validator",
        "model": args.model,
        "summary": summary,
        "results": results,
    }
    Path(args.out).write_text(
        json.dumps(rec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        f"\n[written] {args.out}\nscored={len(scored)} correct={len(correct)} structural_wrong={len(structural)}"
    )
    for v in ("OLD", "NEW", "GROUNDED"):
        print(
            f"{v:9s}: recall={summary[v]['recall_on_structural']} FP_on_correct={summary[v]['false_positive_on_correct']}"
        )


if __name__ == "__main__":
    main()
