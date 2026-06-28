"""P3: How much LLM financial-numeric error is catchable by SHACL/constraint
validation, vs inherently arithmetic?

Directly answers the open question ADR-0118 / the deep-research survey flagged
("no source measured a KG-grounding intervention's effect on numeric errors")
and targets the exact category our guardrail did NOT help (numeric).

Per FinDER numeric-reasoning case (DeepSeek-V3.1, reliable):
  1) extract numeric facts (FinancialMetric: name/value/unit/scale/period/company)
     + produce the final numeric answer.
  2) validate the extracted facts with SEOCHO's REAL validate_with_shacl()
     (datatype + required/cardinality) + a numeric-rule supplement (unit enum,
     period format, value parses, range/sign).
  3) LLM-judge answer correctness vs FinDER gold answer.
  4) for WRONG answers, LLM-judge the error type: STRUCTURAL (bad/missing/mis-typed
     extracted fact) vs ARITHMETIC (facts ok, math wrong).

Headline metrics:
  - numeric accuracy (baseline).
  - of wrong answers: % structural vs % arithmetic.
  - validator CATCH RATE on structural-wrong cases (the SHACL value).
  - validator FALSE-POSITIVE rate on correct answers.

Key from .env. Run:
  PYTHONPATH=src python3 scripts/benchmarks/p3_shacl_numeric_validation.py \
     --per-type 16 --max-chars 3500 --workers 16 --out <file.json>
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from seocho.ontology import NodeDef, Ontology, P, RelDef
from seocho.store.llm import create_llm_backend

NUMERIC_TYPES = ["Compositional", "Division", "Multiplication", "Subtract", "Addition"]
ALLOWED_UNITS = {
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
ALLOWED_SCALES = {
    "",
    "ones",
    "thousand",
    "thousands",
    "million",
    "millions",
    "billion",
    "billions",
}


def fin_numeric_ontology() -> Ontology:
    """Numeric-fact ontology with constraints validate_with_shacl can enforce:
    value must be FLOAT and present; name + period required."""
    return Ontology(
        "fin-numeric",
        version="1.0.0",
        nodes={
            "Company": NodeDef(
                description="A company.",
                properties={"name": P(str, unique=True, required=True)},
            ),
            "FinancialMetric": NodeDef(
                description="A reported financial metric value.",
                properties={
                    "name": P(
                        str,
                        required=True,
                        description="Metric name, e.g. 'total revenue'.",
                    ),
                    "value": P(float, required=True, description="Numeric value."),
                    "unit": P(str, description="Currency/unit."),
                    "scale": P(str, description="thousands/millions/billions."),
                    "period": P(
                        str,
                        required=True,
                        description="Fiscal period, e.g. FY2023 or Q2-2022.",
                    ),
                    "company": P(str, description="Reporting company."),
                },
            ),
        },
        relationships={
            "REPORTED": RelDef(
                source="Company",
                target="FinancialMetric",
                cardinality="ONE_TO_MANY",
                description="Company reported a metric.",
            ),
        },
    )


_PERIOD_RE = re.compile(
    r"(19|20)\d{2}|q[1-4]|fy|h[12]|first|second|third|fourth|quarter|annual", re.I
)


def numeric_rules(facts) -> list:
    """Supplementary structural checks beyond validate_with_shacl: unit enum,
    scale enum, period format, value parses as a finite number, sign sanity."""
    flags = []
    for i, f in enumerate(facts):
        if not isinstance(f, dict):
            flags.append(f"fact[{i}] not an object")
            continue
        name = str(f.get("name", "")) or f"fact[{i}]"
        v = f.get("value")
        try:
            fv = (
                float(str(v).replace(",", "").replace("$", "").replace("%", ""))
                if v not in (None, "")
                else None
            )
        except Exception:
            fv = None
            flags.append(f"{name}: value '{v}' not numeric")
        unit = str(f.get("unit", "")).strip().lower()
        if unit and unit not in ALLOWED_UNITS:
            flags.append(f"{name}: unit '{unit}' off-vocabulary")
        scale = str(f.get("scale", "")).strip().lower()
        if scale and scale not in ALLOWED_SCALES:
            flags.append(f"{name}: scale '{scale}' off-vocabulary")
        period = str(f.get("period", "")).strip()
        if not period:
            flags.append(f"{name}: missing period")
        elif not _PERIOD_RE.search(period):
            flags.append(f"{name}: period '{period}' not a recognizable fiscal period")
        nm = name.lower()
        if (
            fv is not None
            and fv < 0
            and any(k in nm for k in ("revenue", "assets", "shares", "cash"))
        ):
            flags.append(f"{name}: negative value {fv} implausible for this metric")
    return flags


def shacl_validate(onto: Ontology, facts) -> list:
    nodes = [
        {"id": f"m{i}", "label": "FinancialMetric", "properties": f}
        for i, f in enumerate(facts)
        if isinstance(f, dict)
    ]
    try:
        return onto.validate_with_shacl({"nodes": nodes, "relationships": []})
    except Exception as e:
        return [f"validator error: {e}"]


_EXTRACT_SYS = (
    "You are a financial analyst. Extract the numeric facts needed, then answer the "
    "question with a single number. Return ONLY JSON."
)
_EXTRACT_USER = (
    "FINANCIAL TEXT:\n{refs}\n\nQUESTION: {q}\n\n"
    'Return JSON: {{"facts":[{{"name":"...","value":<number>,"unit":"...","scale":"...",'
    '"period":"...","company":"..."}}],"answer_value":<number>,"answer_text":"..."}}'
)
_JUDGE_SYS = "You grade financial answers. Return ONLY JSON."
_CORRECT_USER = (
    "QUESTION: {q}\nGOLD ANSWER: {gold}\nMODEL ANSWER: {ans}\n"
    "Is the model answer numerically correct vs gold (same value allowing rounding/unit "
    'phrasing)? Return JSON {{"correct": true|false}}'
)
_CLASSIFY_USER = (
    "QUESTION: {q}\nGOLD ANSWER: {gold}\nMODEL ANSWER: {ans}\nFACTS THE MODEL EXTRACTED: {facts}\n"
    'The model answer is WRONG. Classify the error: "structural" = a wrong/missing/mis-typed '
    "extracted fact (wrong number pulled from text, wrong unit/scale, wrong period, wrong company, "
    'missing value); "arithmetic" = the extracted facts are correct but the calculation/reasoning '
    'is wrong. Return JSON {{"error_type":"structural"|"arithmetic","reason":"..."}}'
)


def _json(text: str) -> dict:
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


def load_cases(per_type: int, max_chars: int):
    from huggingface_hub import hf_hub_download
    import pandas as pd

    p = hf_hub_download(
        "Linq-AI-Research/FinDER",
        "data/train-00000-of-00001.parquet",
        repo_type="dataset",
    )
    df = pd.read_parquet(p)
    cases = []
    for t in NUMERIC_TYPES:
        sub = df[df["type"] == t].sort_values("_id")
        taken = 0
        for _, row in sub.iterrows():
            refs = row["references"]
            text = (
                " ".join(str(x) for x in refs)
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
            taken += 1
            if taken >= per_type:
                break
    return cases


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-type", type=int, default=16)
    ap.add_argument("--max-chars", type=int, default=3500)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--model", default="DeepSeek-V3.1")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    key = re.search(
        r'ontology_guardrail_mara_api_key\s*=\s*"([^"]+)"',
        Path(".env").read_text(encoding="utf-8"),
    ).group(1)
    onto = fin_numeric_ontology()
    cases = load_cases(args.per_type, args.max_chars)
    print(f"{len(cases)} numeric cases across {NUMERIC_TYPES}")
    be = create_llm_backend(provider="mara", model=args.model, api_key=key)

    done = {"n": 0}
    from threading import Lock

    lock = Lock()

    def run(case):
        out = {"id": case["id"], "type": case["type"]}
        try:
            r = be.complete(
                system=_EXTRACT_SYS,
                user=_EXTRACT_USER.format(refs=case["refs"], q=case["q"]),
                temperature=0.0,
                max_tokens=4096,
                response_format={"type": "json_object"},
            )
            ex = _json(r.text)
        except Exception as e:
            out["error"] = f"extract: {e}"
            return out
        facts = ex.get("facts", []) if isinstance(ex, dict) else []
        ans = ex.get("answer_text") or str(ex.get("answer_value", ""))
        out["n_facts"] = len(facts)
        # structural validation (the SHACL feature + numeric rules)
        shacl = shacl_validate(onto, facts)
        rules = numeric_rules(facts)
        out["validator_flagged"] = bool(shacl or rules)
        out["shacl_errors"] = shacl[:5]
        out["rule_flags"] = rules[:5]
        # correctness judge
        try:
            jc = _json(
                be.complete(
                    system=_JUDGE_SYS,
                    user=_CORRECT_USER.format(q=case["q"], gold=case["gold"], ans=ans),
                    temperature=0.0,
                    max_tokens=2048,
                    response_format={"type": "json_object"},
                ).text
            )
            out["correct"] = bool(jc.get("correct"))
        except Exception as e:
            out["error"] = f"judge: {e}"
            return out
        # classify wrong errors
        if not out["correct"]:
            try:
                jt = _json(
                    be.complete(
                        system=_JUDGE_SYS,
                        user=_CLASSIFY_USER.format(
                            q=case["q"],
                            gold=case["gold"],
                            ans=ans,
                            facts=json.dumps(facts)[:1500],
                        ),
                        temperature=0.0,
                        max_tokens=2048,
                        response_format={"type": "json_object"},
                    ).text
                )
                out["error_type"] = jt.get("error_type")
            except Exception:
                out["error_type"] = None
        with lock:
            done["n"] += 1
            if done["n"] % 20 == 0 or done["n"] == len(cases):
                print(f"  ... {done['n']}/{len(cases)}")
        return out

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run, cases))

    ok = [r for r in results if "error" not in r and "correct" in r]
    wrong = [r for r in ok if not r["correct"]]
    structural = [r for r in wrong if r.get("error_type") == "structural"]
    arithmetic = [r for r in wrong if r.get("error_type") == "arithmetic"]
    correct = [r for r in ok if r["correct"]]

    catch_structural = [r for r in structural if r["validator_flagged"]]
    fp = [r for r in correct if r["validator_flagged"]]

    summary = {
        "n_cases": len(cases),
        "n_scored": len(ok),
        "numeric_accuracy": round(len(correct) / len(ok), 4) if ok else 0.0,
        "n_wrong": len(wrong),
        "wrong_structural": len(structural),
        "wrong_arithmetic": len(arithmetic),
        "pct_wrong_structural": (
            round(len(structural) / len(wrong), 4) if wrong else 0.0
        ),
        "pct_wrong_arithmetic": (
            round(len(arithmetic) / len(wrong), 4) if wrong else 0.0
        ),
        "shacl_catch_rate_on_structural": (
            round(len(catch_structural) / len(structural), 4) if structural else None
        ),
        "validator_false_positive_rate_on_correct": (
            round(len(fp) / len(correct), 4) if correct else None
        ),
        "by_type_accuracy": {
            t: round(
                statistics.mean(
                    [1 if r["correct"] else 0 for r in ok if r["type"] == t]
                ),
                3,
            )
            for t in NUMERIC_TYPES
            if any(r["type"] == t for r in ok)
        },
    }
    record = {
        "experiment": "P3-shacl-numeric-validation",
        "model": args.model,
        "ontology": onto.name,
        "summary": summary,
        "results": results,
    }
    Path(args.out).write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\n[written] {args.out}\n")
    print(
        f"numeric accuracy: {summary['numeric_accuracy']}  ({len(correct)}/{len(ok)})"
    )
    print(
        f"wrong answers: {len(wrong)} → structural {summary['pct_wrong_structural']:.0%} / arithmetic {summary['pct_wrong_arithmetic']:.0%}"
    )
    print(
        f"SHACL+rules CATCH RATE on structural-wrong: {summary['shacl_catch_rate_on_structural']}"
    )
    print(
        f"validator FALSE-POSITIVE rate on correct answers: {summary['validator_false_positive_rate_on_correct']}"
    )


if __name__ == "__main__":
    main()
