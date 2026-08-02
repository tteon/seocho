#!/usr/bin/env python3
"""Does the ontology make independent extractions comparable?

This is the question the project never actually tested. What was reported before
compared entities that received a declared type against entities that fell back
to a generic one, inside a single graph. That is not an ontology effect: whether
an entity gets a declared type is decided by what kind of thing it is, so the
comparison contrasts coarse entities with fine ones and attributes the
difference to typing.

A counterfactual needs the same documents, the same models, and the same prompt,
with the ontology as the only thing that moves. That grid exists on disk:

    4 prompts x 5 ontologies x 3 provider models, 16 shared cases per cell,
    including generic_baseline, which declares nothing

For each cell this measures the rate the federation claim depends on: given a
fact extracted by one model, how often does another model name the same fact.
Prompt is a blocking factor, so the ontology contrast is read within a prompt.

Read-only, no model calls. Traced through the minimal harness.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parents[2]

import verify as verify_mod  # noqa: E402
from observe import Run  # noqa: E402

OUT = ROOT / "outputs/evaluation/mdm_fedcat/log2026-ontology-counterfactual-v1"
VIEWS = {"deepseek": "mdmdeepseek", "gptoss": "mdmgptoss", "minimax27": "mdmminimax27"}
PROMPTS = ["category-aware-fact-first-v1", "duplicate-aware-survivorship-v1",
           "fibo-strict-entity-first-v1", "neutral-kg-v1"]
# Ordered from no ontology to the largest module set, so a monotone trend is
# visible if one exists.
ONTOLOGIES = ["generic-baseline", "fibo-capital-markets", "fibo-finance-core",
              "fibo-medium-current", "fibo-full-local"]
BOOTSTRAP = 10_000
SEED = 42

WORKSPACE_FACTS = """
MATCH (n)
WHERE any(l IN labels(n) WHERE l IN ['MonetaryAmount', 'CashFlow'])
  AND n.id IS NOT NULL AND n.workspace_id = $workspace
RETURN n.id AS slug, coalesce(n.amount, n.value, '') AS amount
"""

WORKSPACE_LIST = """
MATCH (n) WHERE n.workspace_id STARTS WITH $prefix
RETURN DISTINCT n.workspace_id AS workspace
"""


class _Fact:
    __slots__ = ("key", "raw")

    def __init__(self, slug: str, raw: str) -> None:
        import re
        self.key = re.sub(r"[^a-z0-9]+", "_", str(slug).strip().lower()).strip("_")
        self.raw = str(raw).strip()


def paired_bootstrap(deltas: list[float], seed: int = SEED) -> tuple[float, float]:
    """Case-paired percentile interval; cases are the resampling unit."""
    if len(deltas) < 2:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    means = []
    for _ in range(BOOTSTRAP):
        sample = [deltas[rng.randrange(len(deltas))] for _ in deltas]
        means.append(mean(sample))
    means.sort()
    return (round(means[int(0.025 * (len(means) - 1))], 6),
            round(means[int(0.975 * (len(means) - 1))], 6))


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import logging
    logging.getLogger("neo4j").setLevel(logging.ERROR)
    import os
    from neo4j import GraphDatabase

    decisive = {
        "views": VIEWS, "prompts": PROMPTS, "ontologies": ONTOLOGIES,
        "key_rule": "exact_slug", "value_rule": {"scale_words": True},
        "seed": SEED, "bootstrap": BOOTSTRAP,
    }
    run = Run(root=ROOT / "outputs/minimal", name="ontology-counterfactual",
              config={"decisive": decisive})

    driver = GraphDatabase.driver(
        "bolt://localhost:7687",
        auth=(os.getenv("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"]))

    results: dict[str, dict[str, dict]] = {}
    per_case_rates: dict[tuple[str, str], dict[str, float]] = {}
    try:
        for prompt in PROMPTS:
            results[prompt] = {}
            for onto in ONTOLOGIES:
                with run.stage("cell", prompt=prompt, ontology=onto) as out:
                    # Which cases exist in every view for this cell.
                    per_view_cases = {}
                    for view, db in VIEWS.items():
                        prefix = f"fedcat-scenario-{prompt}-{onto}-{view}-"
                        with driver.session(database=db) as session:
                            rows = session.run(WORKSPACE_LIST, prefix=prefix)
                            per_view_cases[view] = {
                                r["workspace"][len(prefix):]: r["workspace"] for r in rows}
                    shared = set.intersection(*(set(v) for v in per_view_cases.values()))
                    if not shared:
                        out["cases"] = 0
                        continue

                    case_rates, pooled = {}, []
                    for case in sorted(shared):
                        views_facts = {}
                        for view, db in VIEWS.items():
                            ws = per_view_cases[view][case]
                            with driver.session(database=db) as session:
                                views_facts[view] = [
                                    _Fact(r["slug"], r["amount"])
                                    for r in session.run(WORKSPACE_FACTS, workspace=ws)]
                        report = verify_mod.compare(
                            views=views_facts,
                            key_of=lambda f: f.key,
                            value_of=lambda f: f.raw)
                        case_rates[case] = report["comparable_key_rate"]
                        pooled.append(report)

                    total_keys = sum(r["distinct_keys"] for r in pooled)
                    comparable = sum(r["comparable_keys"] for r in pooled)
                    pairs = sum(r["pairs"] for r in pooled)
                    disagree = sum(r["disagree"] for r in pooled)
                    cell = {
                        "cases": len(shared),
                        "distinct_keys": total_keys,
                        "comparable_keys": comparable,
                        "comparable_key_rate": round(comparable / total_keys, 6) if total_keys else 0.0,
                        "pairs": pairs,
                        "disagree": disagree,
                        "disagreement_rate": round(disagree / pairs, 6) if pairs else 0.0,
                        "mean_case_rate": round(mean(case_rates.values()), 6) if case_rates else 0.0,
                    }
                    results[prompt][onto] = cell
                    per_case_rates[(prompt, onto)] = case_rates
                    out.update(cell)

        # Contrast every ontology against the no-ontology arm, paired by case.
        contrasts = {}
        for prompt in PROMPTS:
            base = per_case_rates.get((prompt, "generic-baseline"), {})
            if not base:
                continue
            for onto in ONTOLOGIES:
                if onto == "generic-baseline":
                    continue
                arm = per_case_rates.get((prompt, onto), {})
                common = sorted(set(base) & set(arm))
                if len(common) < 2:
                    continue
                deltas = [arm[c] - base[c] for c in common]
                lo, hi = paired_bootstrap(deltas)
                contrasts[f"{prompt}|{onto}_minus_generic"] = {
                    "cases": len(common),
                    "delta_mean_comparable_key_rate": round(mean(deltas), 6),
                    "paired_bootstrap_95_ci": [lo, hi],
                    "detected": (lo > 0) or (hi < 0),
                }
    finally:
        driver.close()

    payload = {
        "contract": "log2026.ontology_counterfactual.v1",
        "question": ("Holding documents, models, and prompt fixed, does the ontology "
                     "change how often two independent extractions name the same fact?"),
        "method": "read-only census over the frozen prompt x ontology grid; no model calls",
        "claim_boundary": ("Comparability only. This says nothing about answer quality, "
                           "and nothing about whether either extraction is correct."),
        "decisive": decisive,
        "cells": results,
        "contrasts_vs_no_ontology": contrasts,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ontology_counterfactual.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    lines = ["# Does the ontology make extractions comparable?", "",
             "Comparable-key rate by prompt and ontology, same 16 cases, same three models.", "",
             "| Prompt | " + " | ".join(ONTOLOGIES) + " |",
             "|---" * (len(ONTOLOGIES) + 1) + "|"]
    for prompt in PROMPTS:
        row = [prompt]
        for onto in ONTOLOGIES:
            cell = results.get(prompt, {}).get(onto)
            row.append(f"{cell['comparable_key_rate']:.3f}" if cell else "-")
        lines.append("| " + " | ".join(row) + " |")
    lines += ["", "## Against the no-ontology arm, paired by case", "",
              "| Contrast | Cases | Delta | 95% CI | Detected |", "|---|---:|---:|---|---|"]
    for name, c in sorted(contrasts.items()):
        ci = c["paired_bootstrap_95_ci"]
        lines.append(f"| {name} | {c['cases']} | {c['delta_mean_comparable_key_rate']:+.4f} "
                     f"| [{ci[0]:+.4f}, {ci[1]:+.4f}] | {'yes' if c['detected'] else 'no'} |")
    lines += ["", payload["claim_boundary"], ""]
    (OUT / "ontology_counterfactual.md").write_text("\n".join(lines))
    run.finish({"cells": sum(len(v) for v in results.values()),
                "contrasts": len(contrasts)})
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
