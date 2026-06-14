"""Does the corpus-aware / guardrail-weighted scorecard predict downstream
guardrail value, where the intrinsic 'balanced' grade did not?

ADR-0115's FinDER ablation found the intrinsic scorecard ranked fibo_minus
(sparse) ABOVE fibo_plus (rich) — yet fibo_plus was the better extraction
guardrail (extraction_score 0.898 vs 0.734). This experiment closes that gap:

  1. OPEN-extract a FinDER sample with MARA (no ontology constraint) -> the
     entity types the corpus actually needs -> a CorpusProfile.
  2. Score fibo_minus / base / plus two ways:
       before: profile='balanced', no corpus  (the intrinsic ranking)
       after:  profile='guardrail', + corpus  (the corpus-aware ranking)
  3. Compare both rankings to the measured downstream guardrail extraction_score
     (fibo_plus > fibo_base > fibo_minus). The 'after' ranking should match it;
     the 'before' ranking does not.

Key from .env. Run:
    PYTHONPATH=src python3 scripts/benchmarks/corpus_aware_scorecard_experiment.py \
        --per-category 5 --max-chars 2500 --out <file.json>
"""

from __future__ import annotations

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from seocho.ontology import Ontology
from seocho.ontology_scorecard import build_corpus_profile, score_ontology
from seocho.store.llm import create_llm_backend

# Downstream guardrail extraction_score measured in ADR-0115 (cross-model means),
# the ground truth the scorecard should track.
DOWNSTREAM_GUARDRAIL_SCORE = {"fibo_minus": 0.734, "fibo_plus": 0.898}

VARIANTS = {
    "fibo_minus": "examples/datasets/fibo_minus.jsonld",
    "fibo_base": "examples/datasets/fibo_base.jsonld",
    "fibo_plus": "examples/datasets/fibo_plus.jsonld",
}

_OPEN_SYSTEM = (
    "You extract entities from financial text. For each entity give a short, "
    "GENERAL type label (e.g. Company, Person, FinancialMetric, Regulation, Risk, "
    "Product, LegalIssue). Do NOT use a fixed schema — choose the type that fits. "
    "Return ONLY JSON."
)
_OPEN_USER = (
    'TEXT:\n{doc}\n\nReturn JSON: {{"nodes":[{{"label":"<GeneralType>","name":"..."}}]}}'
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
            docs.append({"category": str(cat), "text": text[:max_chars]})
            taken += 1
            if taken >= per_category:
                break
    return docs


def _parse(text: str) -> dict:
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


def _ranking(scores: dict) -> list:
    return [k for k, _ in sorted(scores.items(), key=lambda kv: -kv[1])]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-category", type=int, default=5)
    ap.add_argument("--max-chars", type=int, default=2500)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--model", default="DeepSeek-V3.1")  # reliable, 0 parse errors in ADR-0115
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    key = re.search(r'ontology_guardrail_mara_api_key\s*=\s*"([^"]+)"',
                    Path(".env").read_text(encoding="utf-8")).group(1)

    docs = load_finder(args.per_category, args.max_chars)
    print(f"open-extracting {len(docs)} FinDER docs with {args.model} ...")
    be = create_llm_backend(provider="mara", model=args.model, api_key=key)

    def extract(doc):
        try:
            r = be.complete(system=_OPEN_SYSTEM, user=_OPEN_USER.format(doc=doc["text"]),
                            temperature=0.0, max_tokens=4096, response_format={"type": "json_object"})
            return _parse(r.text)
        except Exception:
            return {}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        graphs = list(pool.map(extract, docs))
    profile = build_corpus_profile(graphs, source=f"FinDER open-extract N={len(docs)} {args.model}")
    top = sorted(profile.label_frequencies.items(), key=lambda kv: -kv[1])[:15]
    print(f"corpus profile: {len(profile.label_frequencies)} distinct types; top: {top[:8]}")

    cq_path = Path("examples/finder/datasets/competency_questions.yaml")
    cqs = None
    if cq_path.exists():
        import yaml
        raw = yaml.safe_load(cq_path.read_text())
        cqs = raw.get("competency_questions", raw) if isinstance(raw, dict) else raw

    before, after = {}, {}
    detail = {}
    for name, path in VARIANTS.items():
        o = Ontology.load(path)
        b = score_ontology(o, competency_questions=cqs, profile="balanced")
        a = score_ontology(o, competency_questions=cqs, corpus_profile=profile, profile="guardrail")
        before[name] = round(b.overall_score, 4)
        after[name] = round(a.overall_score, 4)
        cc = a.dimension("corpus_coverage")
        detail[name] = {
            "before_balanced": {"overall": before[name], "grade": b.grade},
            "after_guardrail_corpus": {"overall": after[name], "grade": a.grade,
                                       "corpus_coverage": round(cc.score, 4) if cc else None},
            "top_uncovered": cc.stats.get("top_uncovered", [])[:5] if cc else [],
        }

    record = {
        "experiment": "corpus-aware-scorecard-predicts-guardrail-value",
        "model": args.model,
        "corpus_profile": profile.to_dict(),
        "downstream_guardrail_score": DOWNSTREAM_GUARDRAIL_SCORE,
        "downstream_ranking": _ranking(DOWNSTREAM_GUARDRAIL_SCORE),
        "before_balanced": before,
        "before_ranking": _ranking(before),
        "after_guardrail_corpus": after,
        "after_ranking": _ranking(after),
        "detail": detail,
    }
    Path(args.out).write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n[written] {args.out}")
    print(f"\nDownstream guardrail value (ground truth): {_ranking(DOWNSTREAM_GUARDRAIL_SCORE)}  {DOWNSTREAM_GUARDRAIL_SCORE}")
    print(f"BEFORE (balanced, intrinsic):            {_ranking(before)}  {before}")
    print(f"AFTER  (guardrail + corpus-aware):       {_ranking(after)}  {after}")


if __name__ == "__main__":
    main()
