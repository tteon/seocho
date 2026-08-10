#!/usr/bin/env python3
"""Ablation: what does the ontology-driven middleware actually buy?

The showcase claim is that as a schema grows (here: 12 transaction channels on
top of 5 relationship types), an agent must pick the right relationship/channel
out of many — and that the middleware's compiled ontology is what makes that
reliable. This script tests the claim by ablation, holding the graph, the model,
and the questions fixed and varying only the schema knowledge given to the agent:

* ``full``   — the real ontology (Account/Person/Company/Loan/Channel +
  TRANSFER/USES_CHANNEL/OWN/DEPOSIT/REPAY, with property lists and descriptions).
* ``minimal`` — labels only, no relationship types, no properties, no
  descriptions. The agent must guess how entities connect, which is the position
  a bare LLM is in without a middleware.

Both arms run the identical graph_cot path against the identical DozerDB graph,
so the delta is attributable to schema knowledge alone.

Usage:
    python scripts/finbench/ablation_ontology.py \
        --ontology examples/finbench/finbench.ontology.yaml \
        --cases examples/finbench/cases.json \
        --uri bolt://localhost:7687 --user neo4j --password "$NEO4J_PASSWORD" \
        --database finbenchsf1 --model gpt-oss-120b \
        --out outputs/finbench/sf1/ablation_ontology.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("finbench_breakdown", _HERE / "mara_breakdown.py")
breakdown = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(breakdown)  # type: ignore[union-attr]


def minimal_ontology(full: object) -> object:
    """Same node labels, but no relationship vocabulary and no property detail.

    This is deliberately not an empty ontology: the agent still knows which
    labels exist (otherwise it cannot form any MATCH at all), so the ablation
    isolates *relationship/property* knowledge — exactly what grows when a domain
    schema gains channels.
    """
    from seocho.ontology import NodeDef, Ontology

    nodes = {name: NodeDef(description="", properties={}) for name in full.nodes}  # type: ignore[attr-defined]
    return Ontology(name="finbench-minimal", nodes=nodes, relationships={})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ontology", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--uri", default="bolt://localhost:7687")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", default="")
    parser.add_argument("--database", required=True)
    parser.add_argument("--model", default="gpt-oss-120b")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    from seocho.ontology import Ontology
    full = Ontology.load(args.ontology)
    with args.cases.open('r', encoding='utf-8') as f:
        cases = json.load(f)["cases"]

    arms = {"full": full, "minimal": minimal_ontology(full)}
    results = {}
    for arm, ontology in arms.items():
        print(f"[ablation] arm={arm} model={args.model} over {len(cases)} cases ...", flush=True)
        results[arm] = breakdown._run_model(
            args.model, ontology, args.uri, args.user, args.password,
            args.database, cases, True)

    report = {
        "schema_version": "seocho.finbench.ablation-ontology.v1",
        "database": args.database, "model": args.model,
        "arms": results,
        "delta_sargable": (
            (results["full"].get("stages") or {}).get("s4_sargable_rate") or 0
        ) - ((results["minimal"].get("stages") or {}).get("s4_sargable_rate") or 0),
        "delta_accuracy": results["full"]["accuracy"] - results["minimal"]["accuracy"],
    }

    lines = ["# Ontology ablation — what the middleware buys", "",
             f"model `{args.model}` · database `{args.database}` · identical graph and questions", "",
             "| arm | schema knowledge | accuracy | correct/total | errors |",
             "|---|---|---|---|---|"]
    desc = {"full": "labels + relationships + properties",
            "minimal": "labels only (bare-LLM position)"}
    for arm in ("full", "minimal"):
        r = results[arm]
        lines.append(f"| {arm} | {desc[arm]} | {r['accuracy']:.0%} | {r['correct']}/{r['total']} | {r['errors']} |")
    lines += ["", "## Stage-wise — is the ontology a plan-shaping prior?", "",
              "S4 sargable = the generated query resolved through an index seek rather than a",
              "full scan. If schema grounding steers the model toward label-qualified,",
              "index-usable shapes, this is where it shows up.", "",
              "| arm | S2 slot-fill | S3 supported | S4 sargable | S4 dbHits total | S5 accuracy | S5 exact | guardrail repairs |",
              "|---|---|---|---|---|---|---|---|"]
    def _p(v):
        return "n/a" if v is None else f"{v:.0%}"
    for arm in ("full", "minimal"):
        st = results[arm].get("stages") or {}
        lines.append(
            f"| {arm} | {_p(st.get('s2_slot_fill_rate'))} | {_p(st.get('s3_supported_rate'))} | "
            f"{_p(st.get('s4_sargable_rate'))} | {st.get('s4_db_hits_total', 0):,} | "
            f"{_p(st.get('s5_accuracy'))} | {_p(st.get('s5_exact_rate'))} | "
            f"{_p(st.get('guardrail_repair_rate'))} |")

    lines += ["", "## Per-scenario (correct / plan shape)", "",
              "| scenario | full | minimal |", "|---|---|---|"]
    for case in cases:
        row = []
        for arm in ("full", "minimal"):
            c = next((x for x in results[arm]["cases"] if x["id"] == case["id"]), None)
            mark = "✓" if c and c["correct"] else ("⚠" if c and c["error"] else "✗")
            plan = c.get("s4_plan", {}) if c else {}
            if plan.get("available"):
                shape = "seek" if plan.get("sargable") else f"scan {plan.get('db_hits', 0):,}"
                mark = f"{mark} ({shape})"
            row.append(mark)
        lines.append(f"| {case['id']} | {row[0]} | {row[1]} |")
    markdown = "\n".join(lines) + "\n"

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n")
        args.out.with_suffix(".md").write_text(markdown)
    print(markdown)


if __name__ == "__main__":
    main()
