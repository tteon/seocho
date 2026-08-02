#!/usr/bin/env python3
"""Build the questions the query experiment needs, and say what each is for.

A query returning nothing means the description was poor or the graph has no
such fact, and those are opposite conclusions. Telling them apart needs
questions whose answer is known before the agent runs, which means building them
from the graph.

That is circular and the circularity has to be handled rather than hidden.
Questions derived from a graph ask what the graph can answer, so a system
scoring well on them has demonstrated that it can retrieve what is there — not
that what is there is what anyone wanted. The literature does this too and
inherits the same weakness.

So two sources, kept apart and reported apart:

    derived     built from facts the graph is known to hold or known to lack.
                The answer is known, so failure is attributable: an empty result
                on an answerable question is the description's fault, and a
                non-empty result on an unanswerable one is invention. This is
                what the mechanical failure analysis runs on, and it cannot
                speak to whether the questions are worth asking
    native      FinDER's own questions for the same cases. Nobody knows whether
                the graph can answer them, so an empty result is uninterpretable
                — but they are what a person actually asked, and a system that
                handles derived questions and fails these has learned the wrong
                thing

Derived questions come in five shapes, taken from the types a competency-question
set is supposed to cover, because a suite testing only lookups would miss where
schema description matters most:

    lookup       one fact by name
    comparison   two facts of one kind, which is larger
    aggregation  a total or a count over a category
    temporal     the same metric across two years
    absence      a fact the graph does not hold

Each derived question carries the answer, the category it lives in, and the
labels and properties a correct query would have to name — which is what makes
grounding and selection checkable later without a judge.

    python3 experiments/question_set.py --tag v2 --condition C
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import random
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for path in (str(ROOT / "experiments/minimal"), str(HERE), str(ROOT)):
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


def auth() -> tuple[str, str]:
    return (os.environ.get("NEO4J_USER", "neo4j"),
            os.environ.get("NEO4J_PASSWORD", ""))


def database_for(tag: str, category: str) -> str:
    return (f"{re.sub(r'[^a-z0-9]', '', tag.lower())}cat"
            f"{re.sub(r'[^a-z0-9]', '', category.lower())}")


def harvest(driver, database: str, category: str, condition: str) -> dict[str, Any]:
    """The material a question can be built from: facts, with what a query needs."""
    with driver.session(database=database) as session:
        numeric = session.run(
            "MATCH (n) WHERE n._condition = $c AND n.value_numeric IS NOT NULL "
            "AND n.name IS NOT NULL "
            "RETURN labels(n) AS labels, n.name AS name, n.value AS shown, "
            "       n.value_numeric AS value, n.period_year AS year, "
            "       n._case AS case, n._model AS model LIMIT 3000",
            c=condition).data()
        labels = [r["l"] for r in session.run(
            "MATCH (n) WHERE n._condition = $c UNWIND labels(n) AS l "
            "RETURN DISTINCT l AS l", c=condition)]
        names = [r["n"] for r in session.run(
            "MATCH (n) WHERE n._condition = $c AND n.name IS NOT NULL "
            "RETURN DISTINCT n.name AS n LIMIT 4000", c=condition)]
    return {"database": database, "category": category,
            "facts": [r for r in numeric if r["labels"]],
            "labels": labels, "names": set(names)}


def build(material: dict[str, Any], per_shape: int,
          rng: random.Random) -> list[dict[str, Any]]:
    """Five shapes per category, each carrying its own answer."""
    category = material["category"]
    database = material["database"]
    facts = material["facts"]
    questions: list[dict[str, Any]] = []
    if not facts:
        return questions

    def entry(shape: str, text: str, answer: Any, needs: dict[str, Any],
              answerable: bool = True) -> dict[str, Any]:
        return {"id": f"{category[:4].lower()}-{shape}-{len(questions)}",
                "category": category, "database": database, "shape": shape,
                "question": text, "answer": answer, "answerable": answerable,
                "requires": needs}

    by_name: dict[str, list[dict]] = defaultdict(list)
    for fact in facts:
        by_name[fact["name"]].append(fact)

    # lookup — one fact by the name the graph gave it
    for fact in rng.sample(facts, min(per_shape, len(facts))):
        questions.append(entry(
            "lookup", f"What is the recorded value of {fact['name']}?",
            fact["shown"],
            {"labels": fact["labels"], "properties": ["name", "value"]}))

    # comparison — two facts sharing a label, which is larger
    by_label: dict[str, list[dict]] = defaultdict(list)
    for fact in facts:
        for label in fact["labels"]:
            by_label[label].append(fact)
    pairs = [(l, fs) for l, fs in by_label.items() if len(fs) >= 2]
    for _ in range(min(per_shape, len(pairs))):
        label, group = rng.choice(pairs)
        left, right = rng.sample(group, 2)
        if left["value"] == right["value"]:
            continue
        larger = left if left["value"] > right["value"] else right
        questions.append(entry(
            "comparison",
            f"Which is larger, {left['name']} or {right['name']}?",
            larger["name"],
            {"labels": [label], "properties": ["name", "value_numeric"],
             "needs_numeric_comparison": True}))

    # aggregation — a count the graph can settle exactly
    for label, group in rng.sample(sorted(by_label.items()),
                                   min(per_shape, len(by_label))):
        questions.append(entry(
            "aggregation",
            f"How many {label} facts carry a recorded value in {category}?",
            len(group),
            {"labels": [label], "properties": ["value_numeric"],
             "needs_aggregation": True}))

    # temporal — the same name across two years
    dated: dict[str, dict[int, dict]] = defaultdict(dict)
    for fact in facts:
        if fact.get("year"):
            dated[re.sub(r"[_\s]*\d{4}$", "", fact["name"])][fact["year"]] = fact
    spans = [(k, v) for k, v in dated.items() if len(v) >= 2]
    for stem, years in rng.sample(spans, min(per_shape, len(spans))):
        first, second = sorted(years)[:2]
        questions.append(entry(
            "temporal",
            f"Did {stem} increase from {first} to {second}?",
            "yes" if years[second]["value"] > years[first]["value"] else "no",
            {"properties": ["period_year", "value_numeric"],
             "needs_numeric_comparison": True}))

    # absence — a plausible name the graph does not hold. Plausible matters:
    # a nonsense name tests nothing, because refusing it needs no schema.
    present = material["names"]
    plausible = [f"{stem} {year}" for stem in
                 ("Deferred Revenue", "Goodwill Impairment", "Lease Liability",
                  "Pension Obligation", "Restructuring Charge")
                 for year in (2019, 2020, 2021)]
    missing = [p for p in plausible if p not in present]
    for name in rng.sample(missing, min(per_shape, len(missing))):
        questions.append(entry(
            "absence", f"What is the recorded value of {name}?", None,
            {"properties": ["name"]}, answerable=False))
    return questions


def native_questions(tag: str, condition: str,
                     categories: list[str]) -> list[dict[str, Any]]:
    """FinDER's own questions for the cases that were loaded."""
    spec = importlib.util.spec_from_file_location(
        "finder_index", ROOT / "examples/mdm/11_index_providers.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cases = {c["case_id"]: c for c in module.load_cases_full(seed=42)}

    directory = ROOT / "snapshots" / tag
    loaded = set()
    for path in directory.glob(f"{condition}_*.jsonl"):
        parts = path.stem.split("_")
        if len(parts) >= 3:
            loaded.add("_".join(parts[2:]))
    rows = []
    for case_id in sorted(loaded):
        case = cases.get(case_id)
        if not case or case["category"] not in categories:
            continue
        rows.append({
            "id": f"native-{case_id}", "category": case["category"],
            "database": database_for(tag, case["category"]),
            "shape": "native", "question": case["query"],
            "answer": case["expected_answer"], "answerable": None,
            "requires": {},
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default="v2")
    ap.add_argument("--condition", default="C")
    ap.add_argument("--per-shape", type=int, default=6,
                    help="questions of each shape, per category")
    args = ap.parse_args()

    import observe
    from neo4j import GraphDatabase

    run = observe.Run(OUT_ROOT, "question-set", {"decisive": {
        "tag": args.tag, "condition": args.condition,
        "per_shape": args.per_shape,
        "shapes": ["lookup", "comparison", "aggregation", "temporal",
                   "absence"],
        "seed": 42}})

    driver = GraphDatabase.driver(URI, auth=auth())
    try:
        with run.stage("harvest", categories=len(CATEGORIES)) as out:
            rows = parallel.io_map(
                lambda c: harvest(driver, database_for(args.tag, c), c,
                                  args.condition), CATEGORIES)
            material = [r for r in rows if r]
            out["categories"] = len(material)
            out["facts"] = sum(len(r["facts"]) for r in material)
    finally:
        driver.close()

    with run.stage("derive", per_shape=args.per_shape) as out:
        derived: list[dict[str, Any]] = []
        for cell in material:
            derived += build(cell, args.per_shape, random.Random(
                f"42-{cell['category']}"))
        shapes: dict[str, int] = defaultdict(int)
        for row in derived:
            shapes[row["shape"]] += 1
        out.update(shapes)
        out["total"] = len(derived)
        out["answerable"] = sum(1 for r in derived if r["answerable"])
        out["unanswerable"] = sum(1 for r in derived if r["answerable"] is False)

    with run.stage("native") as out:
        native = native_questions(args.tag, args.condition, CATEGORIES)
        out["questions"] = len(native)

    payload = {
        "contract": "log2026.question_set.v1",
        "question": ("What questions can the query experiment be scored on, and "
                     "which of them have a known answer?"),
        "method": ("derived questions built from facts the graph holds, in five "
                   "shapes, each carrying its answer and the labels and "
                   "properties a correct query must name; plus FinDER's own "
                   "questions for the same cases, whose answerability against "
                   "the graph is unknown"),
        "claim_boundary": ("Derived questions are circular by construction: "
                           "they ask what the graph can answer, so success on "
                           "them shows retrieval works and says nothing about "
                           "whether the questions are worth asking. That is why "
                           "the native questions are carried alongside and "
                           "reported separately, and why no single score "
                           "combines the two."),
        "tag": args.tag, "condition": args.condition,
        "derived": {"total": len(derived), "by_shape": dict(shapes),
                    "answerable": sum(1 for r in derived if r["answerable"]),
                    "unanswerable": sum(1 for r in derived
                                        if r["answerable"] is False)},
        "native": {"total": len(native)},
        "questions": derived + native,
    }
    (run.dir / "question_set.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    print()
    print(f"derived  {len(derived):4d}  "
          f"{payload['derived']['answerable']} answerable, "
          f"{payload['derived']['unanswerable']} not")
    for shape, count in sorted(shapes.items()):
        print(f"  {shape:12s} {count:4d}")
    print(f"native   {len(native):4d}  answerability against the graph unknown")
    print("\nthe two are scored separately and never summed: derived questions "
          "ask what the graph can answer, native ones ask what a person did.")

    run.finish({"derived": len(derived), "native": len(native),
                "artifact": str((run.dir / "question_set.json").relative_to(ROOT))})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
