"""P3 v2: does the new precision-first numeric validator (ADR-0127) keep recall
while fixing the precision problem ADR-0119 measured (recall 0.94 / FP 0.91)?

Same protocol as P3 (ADR-0119): FinDER numeric-reasoning cases, extract facts +
answer (MARA via the ub5 structured layer), judge correctness + error type. Then
apply BOTH validators to each case's facts and compare:
  - OLD = validate_with_shacl (required value/period) + rigid unit/scale enum rules
  - NEW = seocho.numeric_validation.validate_numeric_facts (soft, normalized,
          reconciliation; a `warn` finding = flagged)
Headline metrics for each: recall on structural-wrong cases + false-positive rate
on correct cases.

Key from .env. Run:
  PYTHONPATH=src python3 scripts/benchmarks/p3v2_validator_precision.py \
     --per-type 16 --max-chars 3500 --workers 6 --out <file.json>
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
from seocho.llm_structured import StructuredOutputError, structured_complete
from seocho.numeric_validation import validate_numeric_facts
from seocho.store.llm import create_llm_backend

NUMERIC_TYPES = ["Compositional", "Division", "Multiplication", "Subtract", "Addition"]
# OLD validator's rigid enums (the ADR-0119 design that over-flagged)
_OLD_UNITS = {"usd", "$", "dollars", "percent", "%", "shares", "ratio", "x", "eur", "gbp", "jpy", ""}
_OLD_SCALES = {"", "ones", "thousand", "thousands", "million", "millions", "billion", "billions"}
_PERIOD_RE = re.compile(r"(19|20)\d{2}|q[1-4]|fy|h[12]|first|second|third|fourth|quarter|annual", re.I)


def _fin_numeric_ontology() -> Ontology:
    return Ontology("fin-numeric", version="1.0.0", nodes={
        "Company": NodeDef(description="A company.", properties={"name": P(str, unique=True, required=True)}),
        "FinancialMetric": NodeDef(description="A reported metric.", properties={
            "name": P(str, required=True), "value": P(float, required=True),
            "unit": P(str), "scale": P(str), "period": P(str, required=True), "company": P(str)}),
    }, relationships={"REPORTED": RelDef(source="Company", target="FinancialMetric", cardinality="ONE_TO_MANY")})


_OLD_ONTO = _fin_numeric_ontology()


def old_validator_flags(facts) -> bool:
    """Replicates the ADR-0119 validator: validate_with_shacl + rigid enum/period
    rules. Returns True if it flags anything."""
    nodes = [{"id": f"m{i}", "label": "FinancialMetric", "properties": f} for i, f in enumerate(facts) if isinstance(f, dict)]
    try:
        shacl = _OLD_ONTO.validate_with_shacl({"nodes": nodes, "relationships": []})
    except Exception:
        shacl = ["err"]
    if shacl:
        return True
    for f in facts:
        if not isinstance(f, dict):
            return True
        v = f.get("value")
        try:
            float(str(v).replace(",", "").replace("$", "").replace("%", "")) if v not in (None, "") else None
        except Exception:
            return True
        if str(f.get("unit", "")).strip().lower() and str(f.get("unit", "")).strip().lower() not in _OLD_UNITS:
            return True
        if str(f.get("scale", "")).strip().lower() and str(f.get("scale", "")).strip().lower() not in _OLD_SCALES:
            return True
        period = str(f.get("period", "")).strip()
        if not period or not _PERIOD_RE.search(period):
            return True
    return False


def new_validator_flags(facts) -> bool:
    """The ADR-0127 validator: a `warn` finding = flagged (info does not count)."""
    return bool(validate_numeric_facts(facts).warnings)


_EXTRACT_SYS = "You are a financial analyst. Extract numeric facts, then answer with a number. Return ONLY JSON."
_EXTRACT_USER = ("FINANCIAL TEXT:\n{refs}\n\nQUESTION: {q}\n\n"
                 'Return JSON: {{"facts":[{{"name":"...","value":<number>,"unit":"...","scale":"...",'
                 '"period":"...","company":"..."}}],"answer":"..."}}')
_JUDGE_SYS = "You grade financial answers. Return ONLY JSON."
_CORRECT_USER = 'QUESTION: {q}\nGOLD: {gold}\nMODEL: {ans}\nIs MODEL numerically correct vs GOLD? JSON {{"correct":true|false}}'
_CLASSIFY_USER = ('QUESTION: {q}\nGOLD: {gold}\nMODEL: {ans}\nFACTS: {facts}\nThe answer is WRONG. '
                  'Is the error "structural" (wrong/missing/mis-typed extracted fact) or "arithmetic" '
                  '(facts ok, math wrong)? JSON {{"error_type":"structural"|"arithmetic"}}')


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


def load_cases(per_type, max_chars):
    from huggingface_hub import hf_hub_download
    import pandas as pd
    df = pd.read_parquet(hf_hub_download("Linq-AI-Research/FinDER", "data/train-00000-of-00001.parquet", repo_type="dataset"))
    cases = []
    for t in NUMERIC_TYPES:
        taken = 0
        for _, row in df[df["type"] == t].sort_values("_id").iterrows():
            refs = row["references"]
            text = " ".join(map(str, refs)) if hasattr(refs, "__iter__") and not isinstance(refs, str) else str(refs)
            if len(text.strip()) < 80:
                continue
            cases.append({"id": str(row["_id"]), "type": t, "refs": text.strip()[:max_chars],
                          "q": str(row["text"]), "gold": str(row["answer"])})
            taken += 1
            if taken >= per_type:
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
    key = re.search(r'ontology_guardrail_mara_api_key\s*=\s*"([^"]+)"', Path(".env").read_text()).group(1)
    be = create_llm_backend(provider="mara", model=args.model, api_key=key)
    cases = load_cases(args.per_type, args.max_chars)
    print(f"{len(cases)} numeric cases")
    done = {"n": 0}; lock = Lock()

    def run(case):
        out = {"id": case["id"], "type": case["type"]}
        try:
            ex = _retry(lambda: structured_complete(be, system=_EXTRACT_SYS,
                user=_EXTRACT_USER.format(refs=case["refs"], q=case["q"]), model=args.model, task_hint="json_extraction"))
            facts = ex.get("facts", []) if isinstance(ex, dict) else []
            ans = str(ex.get("answer", ""))
            out["old_flagged"] = old_validator_flags(facts)
            out["new_flagged"] = new_validator_flags(facts)
            jc = _retry(lambda: structured_complete(be, system=_JUDGE_SYS,
                user=_CORRECT_USER.format(q=case["q"], gold=case["gold"], ans=ans), model=args.model))
            out["correct"] = bool(jc.get("correct"))
            if not out["correct"]:
                jt = _retry(lambda: structured_complete(be, system=_JUDGE_SYS,
                    user=_CLASSIFY_USER.format(q=case["q"], gold=case["gold"], ans=ans, facts=json.dumps(facts)[:1500]),
                    model=args.model))
                out["error_type"] = jt.get("error_type")
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {str(e)[:80]}"
        with lock:
            done["n"] += 1
            if done["n"] % 20 == 0 or done["n"] == len(cases):
                print(f"  ... {done['n']}/{len(cases)}")
        return out

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run, cases))

    scored = [r for r in results if "correct" in r]
    correct = [r for r in scored if r["correct"]]
    structural = [r for r in scored if not r["correct"] and r.get("error_type") == "structural"]

    def rate(subset, key):
        return round(sum(1 for r in subset if r.get(key)) / len(subset), 4) if subset else None

    summary = {
        "n_scored": len(scored), "n_correct": len(correct), "n_structural_wrong": len(structural),
        "OLD": {"recall_on_structural": rate(structural, "old_flagged"), "false_positive_on_correct": rate(correct, "old_flagged")},
        "NEW": {"recall_on_structural": rate(structural, "new_flagged"), "false_positive_on_correct": rate(correct, "new_flagged")},
    }
    rec = {"experiment": "P3v2-validator-precision", "model": args.model, "summary": summary, "results": results}
    Path(args.out).write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n[written] {args.out}\n")
    print(f"scored={len(scored)} correct={len(correct)} structural_wrong={len(structural)}")
    print(f"OLD: recall={summary['OLD']['recall_on_structural']} FP_on_correct={summary['OLD']['false_positive_on_correct']}")
    print(f"NEW: recall={summary['NEW']['recall_on_structural']} FP_on_correct={summary['NEW']['false_positive_on_correct']}")


if __name__ == "__main__":
    main()
