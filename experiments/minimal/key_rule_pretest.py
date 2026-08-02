#!/usr/bin/env python3
"""Would populating the ontology's identity properties make views comparable?

The reported 8.0% comparable-key rate was measured on a key the ontology never
specified: a slug the extractor invented, filled on 100% of fact nodes. The key
the ontology does specify for these classes is `name + period + basis`, and it
is filled on 0-39% of them. So the failure attributed to ontology-based
federation may instead be a failure to populate the ontology.

This tests that before any re-extraction is paid for, by re-keying the frozen
graphs under four rules and reporting, for each:

    eligible        facts carrying every field the rule needs
    comparable      eligible keys present in two or more views
    rate            comparable / distinct eligible keys

Rate alone would mislead. A strict rule discards most facts, and a high rate
over a tiny eligible set is not progress, so the absolute comparable count is
reported beside it. If no rule beats the slug baseline in absolute terms,
re-extraction cannot be justified by this hypothesis.

Read-only, no model calls.
"""
from __future__ import annotations

import collections
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/evaluation/mdm_fedcat/log2026-key-rule-pretest-v1"

# Three models under study; minimax25 is retired.
VIEWS = {"deepseek": "mdmdeepseek", "gptoss": "mdmgptoss", "minimax27": "mdmminimax27"}
INVALID = {"", "n/a", "na", "none", "not reported", "unknown", "null", "-"}

FACTS = """
MATCH (n)
WHERE any(l IN labels(n) WHERE l IN ['MonetaryAmount', 'CashFlow', 'FinancialMetric', 'Revenue'])
  AND n.workspace_id STARTS WITH $prefix
RETURN coalesce(n.id, '') AS slug,
       coalesce(n.name, '') AS name,
       coalesce(n.period, '') AS period,
       coalesce(n.basis, '') AS basis,
       coalesce(n.amount, n.value, '') AS amount,
       n.workspace_id AS workspace
"""


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).strip().lower()).strip("_")


def clean(text: str) -> str:
    value = str(text).strip().lower()
    return "" if value in INVALID else value


# rule name -> (fields it needs, how it builds the key)
RULES = {
    "slug": (("slug",), lambda f: norm(f["slug"])),
    "name": (("name",), lambda f: norm(f["name"])),
    "name_period": (("name", "period"), lambda f: f"{norm(f['name'])}|{norm(f['period'])}"),
    "name_period_basis": (("name", "period", "basis"),
                          lambda f: f"{norm(f['name'])}|{norm(f['period'])}|{norm(f['basis'])}"),
}


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import logging
    logging.getLogger("neo4j").setLevel(logging.ERROR)
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        "bolt://localhost:7687",
        auth=(os.getenv("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"]))

    facts: dict[str, list[dict]] = {}
    try:
        for view, database in VIEWS.items():
            with driver.session(database=database) as session:
                rows = [dict(r) for r in session.run(FACTS, prefix=f"fedcat-{view}-")]
            for row in rows:
                row["case"] = str(row["workspace"]).rsplit("-", 1)[-1].lower()
            facts[view] = rows
            print(f"{view}: {len(rows)} fact nodes")
    finally:
        driver.close()

    results = {}
    for rule, (needed, build) in RULES.items():
        owners: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
        eligible_per_view = {}
        for view, rows in facts.items():
            eligible = 0
            for row in rows:
                if any(not clean(row[field]) for field in needed):
                    continue
                eligible += 1
                owners[(row["case"], build(row))].add(view)
            eligible_per_view[view] = eligible
        distinct = len(owners)
        comparable = sum(1 for v in owners.values() if len(v) >= 2)
        results[rule] = {
            "fields": list(needed),
            "eligible_facts_per_view": eligible_per_view,
            "eligible_total": sum(eligible_per_view.values()),
            "distinct_keys": distinct,
            "comparable_keys": comparable,
            "comparable_key_rate": round(comparable / distinct, 6) if distinct else 0.0,
        }
        print(f"  {rule:20s} eligible {sum(eligible_per_view.values()):7d}  "
              f"distinct {distinct:7d}  comparable {comparable:6d}  "
              f"rate {results[rule]['comparable_key_rate']:.4f}")

    base = results["slug"]
    verdict = {}
    for rule, cell in results.items():
        if rule == "slug":
            continue
        verdict[rule] = {
            "rate_vs_slug": round(cell["comparable_key_rate"] - base["comparable_key_rate"], 6),
            "comparable_vs_slug": cell["comparable_keys"] - base["comparable_keys"],
            "beats_slug_in_absolute_terms": cell["comparable_keys"] > base["comparable_keys"],
        }

    payload = {
        "contract": "log2026.key_rule_pretest.v1",
        "question": ("Does keying facts on the ontology's declared identity properties "
                     "make independent extractions more comparable than the slug the "
                     "extractor invented?"),
        "method": "re-key the frozen graphs; read-only; no model calls",
        "claim_boundary": ("A rule that raises the rate while collapsing the eligible "
                           "set has not improved federation. Absolute comparable keys "
                           "decide, and both are reported."),
        "views": list(VIEWS),
        "rules": results,
        "verdict_vs_slug": verdict,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "key_rule_pretest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    lines = ["# Key-rule pre-test", "",
             "Same frozen graphs, three models, four ways of deciding that two views "
             "are describing the same fact.", "",
             "| Key rule | Fields | Eligible facts | Distinct keys | Comparable | Rate |",
             "|---|---|---:|---:|---:|---:|"]
    for rule, cell in results.items():
        lines.append(f"| `{rule}` | {'+'.join(cell['fields'])} | {cell['eligible_total']:,} | "
                     f"{cell['distinct_keys']:,} | {cell['comparable_keys']:,} | "
                     f"{cell['comparable_key_rate']:.4f} |")
    lines += ["", "## Against the slug baseline", "",
              "| Rule | Rate change | Comparable keys change | More comparable facts? |",
              "|---|---:|---:|---|"]
    for rule, v in verdict.items():
        lines.append(f"| `{rule}` | {v['rate_vs_slug']:+.4f} | {v['comparable_vs_slug']:+,} | "
                     f"{'yes' if v['beats_slug_in_absolute_terms'] else 'no'} |")
    lines += ["", payload["claim_boundary"], ""]
    (OUT / "key_rule_pretest.md").write_text("\n".join(lines))
    print()
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
