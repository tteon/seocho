#!/usr/bin/env python3
"""Read the four arms back out of the graph and answer the competency questions.

The arms differ only in the ontology handed to the extractor. This reads what
each one produced and reports, per arm, the questions the arms were built to
decide:

  CQ1  comparable-key rate — given a fact in one model's view, can another
       model's view be identified as describing the same fact? Reported under
       two key rules, because the choice of key is itself a finding: `id` is a
       string the model invented, `name` is what it called the thing.
  CQ2  disagreement rate among the facts that are comparable at all
  CQ3  what kind of disagreement, split into scale, sign, and other
  CQ6  share of nodes carrying a class the arm declared
  CQ9  share of nodes with the `period` property filled
  CQ10 alias collapse — do surface forms of one company converge on one node

Contamination is reported, not hidden. When the extraction call fails the
pipeline silently substitutes a capitalized-token heuristic
(seocho/index/pipeline.py:289-334, called at :734), and a case that fell back
was never an ontology-guided extraction. Those cases are counted, excluded from
the arm figures, and the excluded count is printed next to every number so a
reader can see what the figure is conditioned on (CLAUDE.md 20.2).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for path in (str(HERE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from dotenv import dotenv_values  # noqa: E402

for _key, _value in dotenv_values(ROOT / ".env").items():
    if _value is not None:
        os.environ.setdefault(_key, _value)

import arms as arms_mod  # noqa: E402
import parallel  # noqa: E402

URI = "bolt://localhost:7687"
PARTIAL_ROOT = ROOT / "outputs/evaluation/mdm_fedcat/log2026-reextract-v1"
OUT_ROOT = ROOT / "outputs/minimal"
MODELS = ("deepseek", "gptoss", "minimax27")
ARMS = ("A", "B", "C", "D")

INFRA = {"Document", "Chunk", "Version", "DocumentVersion", "Section",
         "__Memory__", "Memory"}
_NUMBER = re.compile(r"-?\d[\d,]*\.?\d*")
_SCALE = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12,
          "k": 1e3, "m": 1e6, "bn": 1e9, "b": 1e9}


def auth() -> tuple[str, str]:
    return (os.environ.get("NEO4J_USER", "neo4j"),
            os.environ.get("NEO4J_PASSWORD", ""))


def normalize_name(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def parse_amount(text: str) -> float | None:
    """A number with its scale word applied, so $59.4 and $59.4 million differ."""
    if text is None:
        return None
    raw = str(text)
    found = _NUMBER.search(raw)
    if not found:
        return None
    try:
        value = float(found.group(0).replace(",", ""))
    except ValueError:
        return None
    lowered = raw.lower()
    for word, factor in _SCALE.items():
        if re.search(rf"\b{word}\b", lowered):
            value *= factor
            break
    if "(" in raw and ")" in raw:      # accounting negatives
        value = -abs(value)
    return value


def classify(left: float, right: float) -> str:
    if left == right:
        return "same"
    if left == 0 or right == 0:
        return "other"
    if left * right < 0 and abs(abs(left) - abs(right)) < 1e-9:
        return "sign"
    ratio = max(abs(left), abs(right)) / min(abs(left), abs(right))
    for factor in (1e3, 1e6, 1e9, 1e12):
        if abs(ratio - factor) / factor < 0.01:
            return "scale"
    return "other"


def fetch(session, workspace: str) -> list[dict[str, Any]]:
    rows = session.run(
        "MATCH (n {_workspace_id:$w}) "
        "RETURN labels(n) AS labels, n.id AS id, n.name AS name, "
        "       coalesce(n.value, n.amount, '') AS value, "
        "       coalesce(n.period, '') AS period",
        w=workspace).data()
    return [r for r in rows if not (set(r["labels"]) & INFRA)]


def fell_back(session, workspace: str) -> bool:
    """The heuristic fallback writes (:Entity)-[:MENTIONS]->(:Entity).

    A Document also MENTIONS its entities, so both endpoints must be Entity for
    the pattern to indicate the fallback rather than ordinary provenance.
    """
    row = session.run(
        "MATCH (a:Entity {_workspace_id:$w})-[:MENTIONS]->(b:Entity {_workspace_id:$w}) "
        "RETURN count(*) AS c", w=workspace).single()
    return int(row["c"]) > 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--class-limit", type=int, default=70)
    args = ap.parse_args()

    import observe
    from neo4j import GraphDatabase

    run = observe.Run(OUT_ROOT, "arm-results", {"decisive": {
        "arms": list(ARMS), "models": list(MODELS),
        "key_rules": ["name", "slug"], "class_limit": args.class_limit,
        "seed": 42}})

    with run.stage("arms.rebuild", class_limit=args.class_limit) as out:
        built = {}
        fibo = arms_mod.parse_fibo()
        documents, _ = arms_mod.load_corpus_text()
        scoped = arms_mod.scope_to_corpus(fibo, documents, args.class_limit, 20)
        by_iri = {c["iri"]: c["node"] for c in scoped}
        frequency = arms_mod.document_frequency(
            documents,
            {arms_mod.normalize(b["label"])
             for b in fibo["object_properties"].values()
             if arms_mod.normalize(b["label"])})
        relations = arms_mod.scope_relations(fibo, by_iri, frequency, 40)
        for arm in (arms_mod.build_arm_a(), arms_mod.build_arm_b(),
                    arms_mod.build_fibo_arm(scoped, relations, synonyms=False),
                    arms_mod.build_fibo_arm(scoped, relations, synonyms=True)):
            built[arm["arm"]] = set(arm["ontology"].nodes)
        out["declared"] = {a: len(n) for a, n in built.items()}

    cases = sorted({json.loads(p.read_text())["case_id"]
                    for p in PARTIAL_ROOT.glob("*.json")})
    run.log(f"{len(cases)} cases, {len(ARMS)} arms, {len(MODELS)} models")

    driver = GraphDatabase.driver(URI, auth=auth())
    results: dict[str, Any] = {}

    try:
        for arm in ARMS:
            with run.stage(f"read.{arm}", arm=arm) as out:
                views: dict[str, dict[str, list[dict]]] = defaultdict(dict)
                contaminated: set[str] = set()
                missing: list[str] = []

                def read_model(model: str, _arm: str = arm) -> dict[str, Any]:
                    """One connection per model, cases read down that connection.

                    Threads, not processes: this waits on Bolt, and a driver
                    session is not picklable anyway.
                    """
                    database = f"arm{_arm.lower()}{model}"
                    found, empty, fallback = {}, [], set()
                    with driver.session(database=database) as session:
                        for case in cases:
                            workspace = f"arm{_arm.lower()}-{model}-{case}"
                            rows = fetch(session, workspace)
                            if not rows:
                                empty.append(f"{model}/{case}")
                                continue
                            if fell_back(session, workspace):
                                fallback.add(case)
                            found[case] = rows
                    return {"model": model, "rows": found, "empty": empty,
                            "fallback": fallback}

                for result in parallel.io_map(read_model, list(MODELS)):
                    if result is None:
                        continue
                    for case, rows in result["rows"].items():
                        views[case][result["model"]] = rows
                    missing += result["empty"]
                    contaminated |= result["fallback"]
                out["cases_read"] = len(views)
                out["workspaces_empty"] = len(missing)
                out["cases_with_heuristic_fallback"] = len(contaminated)

            clean = {c: v for c, v in views.items()
                     if c not in contaminated and len(v) >= 2}

            with run.stage(f"score.{arm}", arm=arm, cases=len(clean)) as out:
                stats: dict[str, Any] = {}
                for rule in ("name", "slug"):
                    total_keys = comparable = 0
                    disagree = 0
                    kinds: Counter = Counter()
                    for case, per_model in clean.items():
                        keyed: dict[str, dict[str, str]] = defaultdict(dict)
                        for model, rows in per_model.items():
                            for row in rows:
                                key = (normalize_name(row["name"]) if rule == "name"
                                       else str(row["id"] or ""))
                                if not key:
                                    continue
                                keyed[key][model] = row["value"]
                        for key, by_model in keyed.items():
                            total_keys += 1
                            if len(by_model) < 2:
                                continue
                            comparable += 1
                            values = [parse_amount(v) for v in by_model.values()]
                            values = [v for v in values if v is not None]
                            if len(values) < 2:
                                continue
                            verdicts = {classify(a, b)
                                        for a, b in combinations(values, 2)}
                            if verdicts - {"same"}:
                                disagree += 1
                                for verdict in verdicts - {"same"}:
                                    kinds[verdict] += 1
                    stats[rule] = {
                        "keys": total_keys, "comparable": comparable,
                        "comparable_rate": (round(comparable / total_keys, 4)
                                            if total_keys else 0.0),
                        "disagreeing": disagree,
                        "disagreement_rate": (round(disagree / comparable, 4)
                                              if comparable else 0.0),
                        "kinds": dict(kinds),
                    }
                out.update({f"{r}_comparable_rate": stats[r]["comparable_rate"]
                            for r in stats})

            with run.stage(f"properties.{arm}", arm=arm) as out:
                declared = built[arm]
                nodes = declared_nodes = period_nodes = 0
                for per_model in clean.values():
                    for rows in per_model.values():
                        for row in rows:
                            nodes += 1
                            if set(row["labels"]) & declared:
                                declared_nodes += 1
                            if str(row["period"]).strip():
                                period_nodes += 1
                out["nodes"] = nodes
                out["declared_share"] = (round(declared_nodes / nodes, 4)
                                         if nodes else 0.0)
                out["period_share"] = (round(period_nodes / nodes, 4)
                                       if nodes else 0.0)

            results[arm] = {
                "cases_total": len(views),
                "cases_contaminated": len(contaminated),
                "cases_scored": len(clean),
                "contaminated_cases": sorted(contaminated),
                "nodes": nodes,
                "declared_share": (round(declared_nodes / nodes, 4)
                                   if nodes else 0.0),
                "period_share": (round(period_nodes / nodes, 4) if nodes else 0.0),
                "by_key_rule": stats,
            }
    finally:
        driver.close()

    payload = {
        "contract": "log2026.arm_results.v1",
        "question": ("Does the ontology handed to the extractor change whether "
                     "two models describe the same fact the same way?"),
        "held_fixed": ["cases", "reference text", "prompt", "chunking", "seed"],
        "moving": "the ontology only",
        "claim_boundary": ("16 cases, 3 models, one run. Cases where extraction "
                           "fell back to the heuristic are excluded and counted "
                           "separately; every rate is conditioned on the scored "
                           "cases, not the attempted ones."),
        "by_arm": results,
    }
    (run.dir / "arm_results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    print()
    print(f"{'arm':4s} {'scored':>10s} {'nodes':>7s} {'CQ1 name':>9s} "
          f"{'CQ1 slug':>9s} {'CQ2':>7s} {'CQ6':>7s} {'CQ9':>7s}")
    for arm in ARMS:
        cell = results.get(arm)
        if not cell:
            continue
        print(f"{arm:4s} {cell['cases_scored']:3d}/{cell['cases_total']:<6d} "
              f"{cell['nodes']:7d} "
              f"{cell['by_key_rule']['name']['comparable_rate']:9.3f} "
              f"{cell['by_key_rule']['slug']['comparable_rate']:9.3f} "
              f"{cell['by_key_rule']['name']['disagreement_rate']:7.3f} "
              f"{cell['declared_share']:7.3f} {cell['period_share']:7.3f}")
    print("\nCQ1 comparable-key rate | CQ2 disagreement among comparable | "
          "CQ6 declared-type share | CQ9 period filled")
    for arm in ARMS:
        cell = results.get(arm)
        if cell and cell["cases_contaminated"]:
            print(f"  arm {arm}: {cell['cases_contaminated']} cases excluded "
                  f"for heuristic fallback")

    run.finish({"by_arm": {a: {"cases_scored": c["cases_scored"],
                               "name_rate": c["by_key_rule"]["name"]["comparable_rate"],
                               "declared_share": c["declared_share"]}
                           for a, c in results.items()},
                "artifact": str((run.dir / "arm_results.json").relative_to(ROOT))})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
