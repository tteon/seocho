#!/usr/bin/env python3
"""Can an agent read this schema, and which problems can a prompt fix?

Asking whether the loaded properties are "agent-readable" runs two questions
together. Whether a model can write a query that finds the data, which is about
the schema being legible. And whether it can use what comes back, which is about
the rows being legible. The second is rarely the problem — a language model
reads a name and a snippet of text fine. The first is where the failures are.

More usefully, the failures split by whether a prompt can repair them:

    prompt-fixable   the agent does not know a property exists, or is called
                     something unexpected. Telling it the schema fixes this,
                     and text2cypher prompting is exactly that
    shape defects    the data cannot answer the query the agent would sensibly
                     write, no matter how well it is described. A figure stored
                     as the string "$5.2 billion" cannot be compared with `>`,
                     and no amount of prompting produces an operator that works
                     on it

So this inspects the loaded databases for the hazards that decide which kind a
problem is, and reports them separately. No judgement about "readability" —
each hazard is a specific query an agent would write and the specific way it
would fail.

    python3 experiments/schema_legibility.py --tag v2
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for path in (str(ROOT / "experiments/minimal"), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from dotenv import dotenv_values  # noqa: E402

for _key, _value in dotenv_values(ROOT / ".env").items():
    if _value is not None:
        os.environ.setdefault(_key, _value)

import parallel  # noqa: E402

URI = os.environ.get("SEOCHO_NEO4J_URI", "bolt://localhost:7687")
OUT_ROOT = ROOT / "outputs/minimal"

CATEGORIES = ["Accounting", "Company overview", "Financials", "Footnotes",
              "Governance", "Legal", "Risk", "Shareholder return"]

_NUMERIC = re.compile(r"^\s*-?[\$€£]?\s*\d[\d,]*\.?\d*\s*[%]?\s*$")


def auth() -> tuple[str, str]:
    return (os.environ.get("NEO4J_USER", "neo4j"),
            os.environ.get("NEO4J_PASSWORD", ""))


def database_for(tag: str, category: str) -> str:
    return f"{re.sub(r'[^a-z0-9]', '', tag.lower())}cat" \
           f"{re.sub(r'[^a-z0-9]', '', category.lower())}"


def survey(driver, database: str) -> dict[str, Any]:
    """One database's shape, from the angles a query-writing agent runs into."""
    with driver.session(database=database) as session:
        total = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        if not total:
            return {"database": database, "nodes": 0}

        labels = {r["l"]: r["c"] for r in session.run(
            "MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) AS c "
            "ORDER BY c DESC")}
        rel_types = {r["t"]: r["c"] for r in session.run(
            "MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c "
            "ORDER BY c DESC")}
        keys = {r["k"]: r["c"] for r in session.run(
            "MATCH (n) UNWIND keys(n) AS k RETURN k, count(*) AS c "
            "ORDER BY c DESC")}

        # Whether a property an agent would compare numerically holds a number.
        typed: dict[str, dict[str, int]] = {}
        for prop in ("value", "amount", "period", "value_numeric",
                     "period_year"):
            rows = session.run(
                f"MATCH (n) WHERE n.{prop} IS NOT NULL "
                f"RETURN n.{prop} AS v LIMIT 4000").data()
            if not rows:
                continue
            values = [r["v"] for r in rows]
            numeric = sum(1 for v in values if isinstance(v, (int, float)))
            parseable = sum(1 for v in values
                            if isinstance(v, str) and _NUMERIC.match(v))
            typed[prop] = {"present": len(values),
                           "stored_as_number": numeric,
                           "string_but_a_bare_number": parseable,
                           "string_needing_parsing": len(values) - numeric - parseable}
    return {"database": database, "nodes": total, "labels": labels,
            "relationship_types": rel_types, "property_keys": keys,
            "typed_properties": typed}


def hazards(survey_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Each hazard as the query it breaks, and whether a prompt can repair it."""
    labels: Counter = Counter()
    rels: Counter = Counter()
    keys: Counter = Counter()
    nodes = 0
    value_stats = {"present": 0, "stored_as_number": 0,
                   "string_but_a_bare_number": 0, "string_needing_parsing": 0}
    numeric_companion = {"present": 0, "stored_as_number": 0}
    for row in survey_rows:
        if not row.get("nodes"):
            continue
        nodes += row["nodes"]
        labels.update(row["labels"])
        rels.update(row["relationship_types"])
        keys.update(row["property_keys"])
        for key, count in row.get("typed_properties", {}).get("value", {}).items():
            value_stats[key] += count
        for key, count in row.get("typed_properties", {}).get(
                "value_numeric", {}).items():
            if key in numeric_companion:
                numeric_companion[key] += count

    found = []

    # 1. A figure with no comparable form. Keeping the written text is right —
    #    a reader should see "$5.2 billion" — so the hazard is not that `value`
    #    is a string but that nothing beside it is a number. An earlier version
    #    checked only `value` and would have gone on reporting this after it was
    #    fixed, which is worse than not checking.
    if value_stats["present"]:
        numeric = numeric_companion["present"]
        covered = numeric / value_stats["present"] if value_stats["present"] else 0
        if covered < 0.5:
            found.append({
                "hazard": "figures have no comparable form",
                "query_it_breaks": "MATCH (n) WHERE n.value > 1000000 RETURN n",
                "how_it_fails": ("Cypher compares a string to a number "
                                 "lexicographically or returns nothing; the "
                                 "agent sees an empty result and no error"),
                "prompt_fixable": False,
                "evidence": {**value_stats,
                             "value_numeric_present": numeric,
                             "coverage": round(covered, 4)},
            })
        else:
            found.append({
                "hazard": "resolved: figures carry a parsed companion",
                "query_it_breaks": "—",
                "how_it_fails": ("`value` keeps what the model wrote and "
                                 "`value_numeric` holds it parsed, so a "
                                 "comparison works and a reader still sees the "
                                 "original"),
                "prompt_fixable": None,
                "resolved": True,
                "evidence": {"value_present": value_stats["present"],
                             "value_numeric_present": numeric,
                             "coverage": round(covered, 4)},
            })

    # 2. Two names for one idea. An agent picks one and misses the other's rows.
    synonym_pairs = [("value", "amount")]
    for left, right in synonym_pairs:
        if keys.get(left) and keys.get(right):
            found.append({
                "hazard": f"`{left}` and `{right}` both carry a figure",
                "query_it_breaks": f"MATCH (n) WHERE n.{left} IS NOT NULL RETURN n",
                "how_it_fails": (f"silently misses every node that used "
                                 f"`{right}` instead"),
                "prompt_fixable": True,
                "evidence": {left: keys[left], right: keys[right]},
            })

    # 3. A property present on a small minority. An agent filtering on it gets
    #    a near-empty result and cannot tell that from "no matches".
    sparse = {k: c for k, c in keys.items()
              if not k.startswith("_") and 0 < c / nodes < 0.1}
    if sparse:
        found.append({
            "hazard": "properties present on under a tenth of nodes",
            "query_it_breaks": "a filter on any of them",
            "how_it_fails": ("returns almost nothing, which reads as 'no such "
                             "fact' rather than 'this property is rare'"),
            "prompt_fixable": True,
            "evidence": {k: round(c / nodes, 4)
                         for k, c in sorted(sparse.items(),
                                            key=lambda kv: -kv[1])[:10]},
        })

    # 4. Too many labels or relationship types to state in a prompt. Beyond a
    #    point the schema stops fitting in the instruction and the agent guesses.
    if len(labels) > 40 or len(rels) > 40:
        found.append({
            "hazard": "the schema is larger than a prompt can usefully carry",
            "query_it_breaks": "any query naming a label or relationship type",
            "how_it_fails": ("the agent invents a plausible name that does not "
                             "exist and gets an empty result"),
            "prompt_fixable": False,
            "evidence": {"labels": len(labels),
                         "relationship_types": len(rels),
                         "labels_covering_90pct": sum(
                             1 for _ in _top_covering(labels, 0.9)),
                         "types_covering_90pct": sum(
                             1 for _ in _top_covering(rels, 0.9))},
        })

    # 5. Internal properties outnumbering the ones a question is about.
    internal = sum(c for k, c in keys.items() if k.startswith("_"))
    domain = sum(c for k, c in keys.items() if not k.startswith("_"))
    if internal > domain:
        found.append({
            "hazard": "bookkeeping properties outnumber domain ones",
            "query_it_breaks": "any prompt that lists the schema",
            "how_it_fails": ("the description an agent receives is mostly "
                             "workspace and provenance keys, crowding out the "
                             "few properties a question is actually about"),
            "prompt_fixable": True,
            "evidence": {"internal_uses": internal, "domain_uses": domain},
        })

    return {"nodes": nodes, "labels": len(labels),
            "relationship_types": len(rels), "property_keys": len(keys),
            "value_typing": value_stats,
            "numeric_companion": numeric_companion,
            "unresolved_hazards": sum(1 for h in found
                                      if not h.get("resolved")),
            "hazards": found}


def _top_covering(counter: Counter, share: float) -> list[str]:
    """The few names that cover most of the data — what a prompt should list."""
    total = sum(counter.values()) or 1
    running, kept = 0, []
    for name, count in counter.most_common():
        kept.append(name)
        running += count
        if running / total >= share:
            break
    return kept


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default="v2")
    args = ap.parse_args()

    import observe
    from neo4j import GraphDatabase

    run = observe.Run(OUT_ROOT, "schema-legibility", {"decisive": {
        "tag": args.tag, "categories": CATEGORIES, "seed": 42}})

    driver = GraphDatabase.driver(URI, auth=auth())
    try:
        with run.stage("survey", categories=len(CATEGORIES)) as out:
            rows = parallel.io_map(
                lambda c: survey(driver, database_for(args.tag, c)), CATEGORIES)
            surveyed = [r for r in rows if r]
            out["databases"] = len(surveyed)
            out["nodes"] = sum(r.get("nodes", 0) for r in surveyed)
    finally:
        driver.close()

    with run.stage("hazards") as out:
        report = hazards(surveyed)
        out["labels"] = report["labels"]
        out["relationship_types"] = report["relationship_types"]
        out["hazards"] = len(report["hazards"])
        out["prompt_fixable"] = sum(1 for h in report["hazards"]
                                    if h["prompt_fixable"])

    payload = {
        "contract": "log2026.schema_legibility.v1",
        "question": ("Which of the obstacles to an agent querying this graph "
                     "can a prompt remove, and which are shape defects a prompt "
                     "cannot touch?"),
        "method": ("the loaded category databases inspected for label and "
                   "relationship counts, property coverage, and whether the "
                   "properties an agent would compare numerically hold numbers; "
                   "each hazard reported as the query it breaks and how"),
        "claim_boundary": ("Static inspection of the schema. It predicts where "
                           "a query-writing agent will fail; it does not measure "
                           "how often one does, which needs the agent to be run "
                           "and is Part 2's job."),
        "tag": args.tag,
        **{k: v for k, v in report.items() if k != "hazards"},
        "hazards": report["hazards"],
        "labels_covering_90pct": _top_covering(
            Counter({k: v for r in surveyed if r.get("labels")
                     for k, v in r["labels"].items()}), 0.9),
        "by_database": surveyed,
    }
    (run.dir / "schema_legibility.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    print()
    print(f"{payload['nodes']:,} nodes, {payload['labels']} labels, "
          f"{payload['relationship_types']} relationship types, "
          f"{payload['property_keys']} property keys")
    v = payload["value_typing"]
    if v["present"]:
        print(f"\n`value`: {v['present']:,} present, "
              f"{v['stored_as_number']:,} stored as a number, "
              f"{v['string_but_a_bare_number']:,} a bare number in a string, "
              f"{v['string_needing_parsing']:,} needing parsing")
    print(f"\n{payload['unresolved_hazards']} unresolved of "
          f"{len(payload['hazards'])} checked:")
    for entry in payload["hazards"]:
        fix = ("resolved" if entry.get("resolved")
               else "prompt can fix" if entry["prompt_fixable"]
               else "PROMPT CANNOT FIX")
        print(f"\n  [{fix}] {entry['hazard']}")
        print(f"    breaks: {entry['query_it_breaks']}")
        print(f"    how:    {entry['how_it_fails']}")
        print(f"    {json.dumps(entry['evidence'])[:150]}")
    print(f"\nlabels covering 90% of nodes: "
          f"{len(payload['labels_covering_90pct'])} of {payload['labels']} — "
          f"this is what a prompt should list, not the whole set")

    run.finish({"hazards": len(payload["hazards"]),
                "prompt_fixable": sum(1 for h in payload["hazards"]
                                      if h["prompt_fixable"]),
                "artifact": str((run.dir / "schema_legibility.json").relative_to(ROOT))})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
