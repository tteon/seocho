"""Demo: versioned snapshot store with evidence-backed version-to-version
guardrail comparison. Deterministic — reuses the FinDER corpus profile recorded
by the ADR-0116 experiment (no new LLM calls).

Stores fibo_minus as v1.0.0 and fibo_plus as v2.0.0 (each with a guardrail+corpus
scorecard), then compares them: schema diff + recommended bump + measured
guardrail-value delta. Run:
    PYTHONPATH=src python3 scripts/benchmarks/ontology_versioning_demo.py --out <file.json>
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from seocho.ontology import Ontology
from seocho.ontology_scorecard import CorpusProfile, score_ontology
from seocho.ontology_snapshot_store import OntologySnapshotStore

CORPUS_SRC = "docs/decisions/ADR-0116-corpus-aware-scorecard.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--store", default=None, help="store dir (default: temp)")
    args = ap.parse_args()

    with open(Path(CORPUS_SRC), "r", encoding="utf-8") as f:
        corpus_data = json.load(f)
    corpus = CorpusProfile(
        label_frequencies=corpus_data["label_frequencies"],
        doc_count=corpus_data.get("doc_count", 0),
        source=corpus_data.get("source", ""),
    )

    # Same package, two versions: a sparse v1 and a richer v2 (the FinDER guardrails).
    v1 = Ontology.load("examples/datasets/fibo_minus.jsonld")
    v1.package_id, v1.version = "fibo_finder", "1.0.0"
    v2 = Ontology.load("examples/datasets/fibo_plus.jsonld")
    v2.package_id, v2.version = "fibo_finder", "2.0.0"

    store_dir = args.store or tempfile.mkdtemp(prefix="seocho_snap_")
    store = OntologySnapshotStore(store_dir)
    store.save(v1, scorecard=score_ontology(v1, corpus_profile=corpus, profile="guardrail"),
               corpus_profile=corpus, weight_profile="guardrail",
               notes="sparse FIBO (2 classes) — initial guardrail")
    store.save(v2, scorecard=score_ontology(v2, corpus_profile=corpus, profile="guardrail"),
               corpus_profile=corpus, weight_profile="guardrail",
               notes="rich FIBO (9 classes) — refined guardrail")

    history = store.history("fibo_finder")
    comparison = store.compare("fibo_finder", "1.0.0", "2.0.0")

    record = {
        "experiment": "ontology-versioning-snapshot-demo",
        "store_dir": store_dir,
        "corpus_source": corpus.source,
        "history": history,
        "comparison": comparison,
    }
    Path(args.out).write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[written] {args.out}\n")
    print("HISTORY (fibo_finder):")
    for h in history:
        print(f"  v{h['version']:6s} grade={h['grade']} overall={h['overall_score']} "
              f"corpus_coverage={h['corpus_coverage']}  fp={h['schema_fingerprint']}  — {h['notes']}")
    gv = comparison["guardrail_verdict"]
    print(f"\nCOMPARE 1.0.0 -> 2.0.0: schema_changed={comparison['schema_changed']} "
          f"bump={comparison['recommended_bump']}")
    print(f"  added nodes: {comparison['changes'].get('nodes', {}).get('added')}")
    print(f"  GUARDRAIL VERDICT: v2 is '{gv['verdict']}' (basis={gv['basis']}, delta={gv['delta']:+})")


if __name__ == "__main__":
    main()
