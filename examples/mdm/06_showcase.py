#!/usr/bin/env python3
"""The payoff: money questions + steward dashboard over ``mdmmaster`` — $0.

Every number printed here traces to a queryable node in ``mdmmaster`` or a
row in the run's JSON artifacts (§20.1). The quarantine queue is shown as a
feature — an empty steward queue would be a red flag, not a success.

Also verifies the sovereignty contract: department DBs were never mutated by
the pipeline (node counts vs. the staging artifact's snapshot).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

MDM_ROOT = Path(__file__).resolve().parent
ROOT = MDM_ROOT.parents[1]
sys.path.insert(0, str(MDM_ROOT))
sys.path.insert(0, str(ROOT))

import os  # noqa: E402

from dotenv import dotenv_values  # noqa: E402

for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None:
        os.environ.setdefault(k, v)

MASTER_DB = "mdmmaster"
MASTER_WS = "mdm-master-v1"


def hr(title: str) -> None:
    print(f"\n{'=' * 74}\n  {title}\n{'=' * 74}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-prefix", default="seocho-capital-v1")
    args = ap.parse_args()

    out_dir = ROOT / "outputs" / "evaluation" / "mdm_demo" / args.run_prefix
    with (out_dir / "staging_artifact.json").open("r", encoding="utf-8") as f:
        staging = json.load(f)
    with (out_dir / "master_artifact.json").open("r", encoding="utf-8") as f:
        master = json.load(f)
    goldens = master["golden_entities"]
    facts = master["golden_facts"]
    tasks = master["steward_tasks"]

    from seocho.store.graph import Neo4jGraphStore
    gs = Neo4jGraphStore(os.environ["NEO4J_URI"],
                         os.environ.get("NEO4J_USER", "neo4j"),
                         os.environ.get("NEO4J_PASSWORD", ""))
    try:
        # Q1 — entity duplication census (the Lehman counting problem)
        hr("Q1  Entity duplication census")
        n_src = sum(len(g["members"]) for g in goldens)
        print(f"  {n_src} department entity records -> {len(goldens)} resolved entities")
        by_count = defaultdict(int)
        for g in goldens:
            by_count[g["model_count"]] += 1
        for c in sorted(by_count, reverse=True):
            print(f"    found by {c}/3 models: {by_count[c]} entities")

        # Q2 — the CRO question on the most-corroborated entity
        hr("Q2  The CRO question: consolidated view with lineage")
        def fact_richness(g):
            return sum(1 for f in facts if f["golden_id"] == g["golden_id"]
                       and f["agreement_count"] >= 2)
        star = max(goldens, key=fact_richness, default=None)
        if star:
            print(f"  Entity: {star['name']}  (golden_id {star['golden_id']})")
            print(f"  Aliases across departments: {star['aliases']}")
            print(f"  Models contributing: {star['models']}")
            for f in [f for f in facts if f["golden_id"] == star["golden_id"]][:6]:
                print(f"    {f['metric_raw']} [{f['period']}] = {f['value_raw']}"
                      f"  (rule={f['rule']}, agreement {f['agreement_count']}/"
                      f"{f['sources_reporting']}, confidence {f['confidence']})")
                for c in f["contributing"]:
                    print(f"        <- {c['source']}: {c['value']}")

        # Q3 — conflicts: the quarantine queue (a feature, not a bug)
        hr("Q3  Steward queue: cross-model conflicts quarantined (no silent pick)")
        print(f"  open tasks: {len(tasks)}")
        by_reason = defaultdict(int)
        for t in tasks:
            by_reason[t["reason"]] += 1
        for r, c in sorted(by_reason.items()):
            print(f"    {r}: {c}")
        for t in tasks[:5]:
            gname = next((g["name"] for g in goldens
                          if g["golden_id"] == t["golden_id"]), "?")
            print(f"  - {gname} :: {t['metric_raw']} [{t['period']}] ({t['reason']})")
            for c in t["contributing"]:
                print(f"        {c['source']}: {c['value']}")

        # Q4 — coverage asymmetry + per-model corroboration
        hr("Q4  Model scorecard: coverage + corroboration")
        per_model = defaultdict(lambda: {"reported": 0, "corroborated": 0})
        for f in facts + tasks:
            winners = set()
            if f.get("status") == "golden":
                dissent_sources = {d.get("source") for d in f["dissents"]}
                winners = {c["source"] for c in f["contributing"]} - dissent_sources
            for c in f["contributing"]:
                per_model[c["source"]]["reported"] += 1
                if c["source"] in winners and f.get("agreement_count", 0) >= 2:
                    per_model[c["source"]]["corroborated"] += 1
        for m, s in sorted(per_model.items()):
            rate = s["corroborated"] / s["reported"] if s["reported"] else 0.0
            print(f"  {m:<28} facts reported {s['reported']:>3}, "
                  f"corroborated by another model {s['corroborated']:>3} ({rate:.0%})")

        # Dashboard — auto-merge vs quarantine + agreement rate
        hr("Steward dashboard")
        n_groups = len(facts) + len(tasks)
        if n_groups:
            print(f"  fact groups: {n_groups}  -> golden {len(facts)} "
                  f"({len(facts)/n_groups:.0%}), quarantined {len(tasks)} "
                  f"({len(tasks)/n_groups:.0%})")
        multi = [f for f in facts if f["sources_reporting"] >= 2]
        if multi:
            agree = sum(1 for f in multi if f["agreement_count"] == f["sources_reporting"])
            print(f"  multi-source fact groups: {len(multi)}, full inter-model "
                  f"agreement: {agree} ({agree/len(multi):.0%})")
        print(f"  ruleset: v{master['ruleset_version']} "
              f"(sha {master['ruleset_sha256'][:12]}…) — every golden node stamped")

        # Sovereignty check — dept DBs unmutated since the staging snapshot
        hr("Sovereignty check: department DBs unmutated")
        ok = True
        for db, before in staging["dept_node_counts"].items():
            now = int(gs.query("MATCH (n) RETURN count(n) AS c", database=db)[0]["c"])
            mark = "OK" if now == before else "MUTATED!"
            ok = ok and (now == before)
            print(f"  {db}: {before} -> {now}  [{mark}]")
        if not ok:
            print("  !! a department DB changed during the pipeline — investigate")
            return 1

        print(f"\nartifacts: {out_dir.relative_to(ROOT)}/"
              "{staging,resolve,master}_artifact.json")
        return 0
    finally:
        gs.close()


if __name__ == "__main__":
    raise SystemExit(main())
