#!/usr/bin/env python3
"""
SEOCHO Workbench Matrix Test — automated parameter exploration.

Runs Workbench.vary() across ontology × prompt × chunk_size combinations
and produces a ranked leaderboard + CSV.

Usage:
    python examples/workbench_matrix.py
    python examples/workbench_matrix.py --quick  # 4 combos only
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from output_paths import evaluation_output_dir


def main(quick: bool = False):
    print("=" * 70)
    print("SEOCHO Workbench Matrix Test")
    print("=" * 70)

    from seocho import Ontology, enable_tracing, flush_tracing, disable_tracing
    from seocho.experiment import Workbench
    from seocho.query import PRESET_PROMPTS
    from seocho.tracing import export_traces_csv

    # Load FIBO variants
    datasets_dir = Path(__file__).parent / "datasets"
    fibo_base = str(datasets_dir / "fibo_base.jsonld")
    fibo_plus = str(datasets_dir / "fibo_plus.jsonld")
    fibo_minus = str(datasets_dir / "fibo_minus.jsonld")

    # Load sample text
    import json
    with open(datasets_dir / "tutorial_filings_sample.json") as f:
        dataset = json.load(f)
    texts = [d["text"] for d in dataset[:3]]

    # Tracing
    results_dir = evaluation_output_dir("workbench")
    jsonl_path = str(results_dir / "workbench_traces.jsonl")
    enable_tracing(backend=["console", "jsonl"], output=jsonl_path)

    # Opik
    try:
        import opik
        api_key = os.getenv("OPIK_API_KEY", "")
        if api_key:
            opik.configure(api_key=api_key, workspace="tteon", project_name="seocho-workbench", force=True)
    except Exception:
        pass

    # Build workbench
    wb = Workbench(input_texts=texts)

    if quick:
        wb.vary("ontology", [fibo_base, fibo_plus])
        wb.vary("model", ["gpt-4o-mini"])
        wb.vary("chunk_size", [6000])
    else:
        wb.vary("ontology", [fibo_minus, fibo_base, fibo_plus])
        wb.vary("model", ["gpt-4o-mini"])
        wb.vary("prompt_template", [
            PRESET_PROMPTS["general"],
            PRESET_PROMPTS["finance"],
            PRESET_PROMPTS["filing_financials"],
        ])
        wb.vary("chunk_size", [4000, 8000])

    print(f"\nCombinations: {wb.total_combinations}")
    print("Running...\n")

    wb.on_run(lambda i, t, p: print(
        f"  [{i}/{t}] " + " | ".join(
            f"{k}={Path(str(v)).stem if '/' in str(v) else v}"
            for k, v in p.items()
        )
    ))

    results = wb.run_all()

    # Leaderboard
    print(f"\n{'=' * 70}")
    print("LEADERBOARD")
    print(f"{'=' * 70}")
    print(results.leaderboard())

    # Save
    saved = results.save(str(results_dir / "workbench_run"))
    print(f"\nResults saved to {saved}")

    # CSV
    flush_tracing()
    disable_tracing()
    try:
        csv_path = str(results_dir / "workbench_traces.csv")
        count = export_traces_csv(jsonl_path, csv_path)
        print(f"Traces: {count} records → {csv_path}")
    except Exception:
        pass

    # DataFrame summary
    try:
        df = results.to_dataframe()
        print(f"\nDataFrame summary:")
        print(df.groupby("ontology")["score"].mean().to_string())
    except ImportError:
        pass

    print(f"\n{'=' * 70}")
    print(f"Best: {results.best_by('extraction_score').config_name} "
          f"(score={results.best_by('extraction_score').extraction_score:.1%})")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()
    main(quick=args.quick)
