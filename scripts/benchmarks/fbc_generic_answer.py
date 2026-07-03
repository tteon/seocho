"""Capstone FIBO experiment: a bridged-FBC-derived GENERIC guardrail vs the
hand-curated fibo_plus, on FinDER answer accuracy (ADR-0137).

Raw FIBO modules are too large to inject as an extraction prompt (ADR-0134). But
after lexical+semantic bridging (ADR-0135/0136) the FBC module's classes carry
generic aliases; collapsing those to the distinct generic VOCABULARY yields a
small, prompt-sized, version-pinned guardrail derived from official FIBO. This
compares it to the hand-curated slice on answers.

Key from .env. Run:
  PYTHONPATH=src python3 scripts/benchmarks/fbc_generic_answer.py --per-category 5 --out <file>
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from seocho.evaluation import AnswerCase, compare_guardrails_by_answer
from seocho.fibo_catalog import (FINDER_FIBO_ROOTS, bridge_to_corpus, catalog_module_to_ontology,
                                 catalog_provenance, load_catalog, semantic_bridge)
from seocho.guardrail_selector import load_corpus_profile
from seocho.ontology import NodeDef, Ontology
from seocho.store.llm import create_llm_backend


def build_fbc_generic(catalog_path: str, corpus_path: str) -> Ontology:
    """Bridge FBC (lexical + semantic) and collapse to its corpus-relevant generic
    vocabulary — a small, prompt-sized, FIBO-version-pinned guardrail."""
    cat = load_catalog(catalog_path)
    cp = load_corpus_profile(corpus_path)
    fbc = semantic_bridge(bridge_to_corpus(catalog_module_to_ontology(cat, "FBC"), cp), FINDER_FIBO_ROOTS)
    generic = set(cp.label_frequencies) | set(FINDER_FIBO_ROOTS)
    terms = sorted({a for nd in fbc.nodes.values() for a in nd.aliases if a in generic})
    commit = catalog_provenance(cat)["fibo_commit"][:12]
    return Ontology("fibo_fbc_generic", package_id="fibo.FBC.generic", version=commit,
                    nodes={t: NodeDef(description=f"{t} (FIBO-FBC derived).") for t in terms})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default="outputs/semantic_artifacts/fibo/latest/catalog.json")
    ap.add_argument("--corpus", default="docs/decisions/ADR-0116-corpus-aware-scorecard.json")
    ap.add_argument("--categories", default="Company overview,Financials,Governance")
    ap.add_argument("--per-category", type=int, default=5)
    ap.add_argument("--model", default="DeepSeek-V3.1")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    from huggingface_hub import hf_hub_download
    import pandas as pd

    with open(".env", "r", encoding="utf-8") as env_file:
        key = re.search(r'ontology_guardrail_mara_api_key\s*=\s*"([^"]+)"', env_file.read()).group(1)
    fbc_generic = build_fbc_generic(args.catalog, args.corpus)
    ontos = {"curated_plus": Ontology.load("examples/datasets/fibo_plus.jsonld"), "fibo_fbc_generic": fbc_generic}

    df = pd.read_parquet(hf_hub_download("Linq-AI-Research/FinDER", "data/train-00000-of-00001.parquet", repo_type="dataset"))
    cases = []
    for c in args.categories.split(","):
        for _, r in df[df["category"] == c.strip()].sort_values("_id").head(args.per_category).iterrows():
            rf = r["references"]
            ctx = " ".join(map(str, rf)) if hasattr(rf, "__iter__") and not isinstance(rf, str) else str(rf)
            cases.append(AnswerCase(question=str(r["text"]), gold_answer=str(r["answer"]),
                                    context=ctx[:3000], category=str(r["category"]), case_id=str(r["_id"])))
    print(f"{len(cases)} cases; curated_plus={len(ontos['curated_plus'].nodes)} cls, "
          f"fibo_fbc_generic={len(fbc_generic.nodes)} cls", flush=True)
    be = create_llm_backend(provider="mara", model=args.model, api_key=key)
    reps = compare_guardrails_by_answer(be, ontos, cases, workers=args.workers)
    out = {"experiment": "fibo-fbc-generic-vs-curated",
           "provenance": catalog_provenance(args.catalog),
           "fbc_generic_terms": list(fbc_generic.nodes),
           **{name: {"accuracy": r.accuracy, "by_category": r.by_category, "n": r.n_scored, "errors": r.errors}
              for name, r in reps.items()}}
    Path(args.out).write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for name, r in reps.items():
        print(f"{name:16s} acc={r.accuracy} by={r.by_category} n={r.n_scored} err={r.errors}", flush=True)
    print(f"[written] {args.out}")


if __name__ == "__main__":
    main()
