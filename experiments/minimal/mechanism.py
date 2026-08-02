#!/usr/bin/env python3
"""Why does more vocabulary lower agreement? Two measurements that were owed.

Section 1.4 found that giving the extractor no schema produces the highest
agreement between models, and that the gap is larger than sampling noise. The
mechanism offered for it — more classes means more ways to slice one sentence —
is interpretation. These are the two measurements that were named as the reason
to pay for the run and then not computed.

Does declaring a type change how findable a thing is?
    An earlier attempt at this compared typed against untyped entities inside a
    single graph and read the gap as an effect of typing. That was wrong and is
    withdrawn: what decides whether an entity gets a declared type is what kind
    of thing it is, so the comparison contrasted companies with one-off figures.
    The honest form pairs by case across conditions — the same document, once
    with no schema and once with FIBO — and asks which condition's entities
    recur across models.

Do synonyms collapse two surface forms onto one node?
    FIBO declares LLC as a name for limited liability company. If the synonym
    condition works, the two spellings should land on one node where the plain
    FIBO condition leaves two. Measured directly on the alias pairs FIBO
    declares, restricted to the ones the corpus actually uses.

Reads the graphs already built. Parallel over conditions. No model call.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
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
OUT_ROOT = ROOT / "outputs/minimal"
MODELS = ("deepseek", "gptoss", "minimax27")
INFRA = {"Document", "Chunk", "Version", "DocumentVersion", "Section",
         "__Memory__", "Memory"}


def auth() -> tuple[str, str]:
    return (os.environ.get("NEO4J_USER", "neo4j"),
            os.environ.get("NEO4J_PASSWORD", ""))


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def read_condition(driver, arm: str, tag: str,
                   cases: list[str]) -> dict[str, dict[str, list[dict]]]:
    """case -> model -> nodes, for one condition. One thread per model."""
    def read(model: str) -> tuple[str, dict[str, list[dict]]]:
        database = f"arm{tag}{arm.lower()}{model}" if tag else f"arm{arm.lower()}{model}"
        found: dict[str, list[dict]] = {}
        with driver.session(database=database) as session:
            for case in cases:
                workspace = (f"arm{tag}-{arm.lower()}-{model}-{case}" if tag
                             else f"arm{arm.lower()}-{model}-{case}")
                rows = session.run(
                    "MATCH (n {_workspace_id:$w}) WHERE n.name IS NOT NULL "
                    "RETURN labels(n) AS labels, n.name AS name",
                    w=workspace).data()
                found[case] = [r for r in rows
                               if not (set(r["labels"]) & INFRA)]
        return model, found

    per_case: dict[str, dict[str, list[dict]]] = defaultdict(dict)
    for result in parallel.io_map(read, list(MODELS)):
        if result is None:
            continue
        model, found = result
        for case, rows in found.items():
            per_case[case][model] = rows
    return per_case


def findability(per_case: dict[str, dict[str, list[dict]]]) -> dict[str, Any]:
    """Share of entity names that at least two models produced, per case.

    Reported per case as well as pooled, because pooling lets one large case
    dominate and the per-case spread is what the pairing across conditions
    needs.
    """
    by_case = {}
    pooled_seen: int = 0
    pooled_total: int = 0
    for case, per_model in per_case.items():
        seen: dict[str, int] = defaultdict(int)
        for model, rows in per_model.items():
            for name in {normalize(r["name"]) for r in rows if r["name"]}:
                seen[name] += 1
        total = len(seen)
        shared = sum(1 for v in seen.values() if v >= 2)
        by_case[case] = {"names": total, "shared": shared,
                         "rate": round(shared / total, 4) if total else 0.0}
        pooled_total += total
        pooled_seen += shared
    return {"per_case": by_case,
            "pooled_names": pooled_total, "pooled_shared": pooled_seen,
            "pooled_rate": round(pooled_seen / pooled_total, 4)
            if pooled_total else 0.0}


def alias_pairs_in_scope(class_limit: int) -> list[dict[str, str]]:
    """FIBO alias pairs for the classes the FIBO conditions ship.

    Abbreviations and multi-word phrases only: a single common word carries
    several senses and its counts cannot be trusted, which is the same rule the
    register measurement applies.
    """
    fibo = arms_mod.parse_fibo()
    documents, _ = arms_mod.load_corpus_text()
    scoped = arms_mod.scope_to_corpus(fibo, documents, class_limit, 20)
    pairs = []
    for entry in scoped:
        for kind in ("synonym", "abbreviation"):
            for alias in entry["annotations"].get(kind, []):
                gram = arms_mod.normalize(alias)
                if not gram or gram == arms_mod.normalize(entry["label"]):
                    continue
                if len(gram) == 1 and not any(c.isupper() for c in alias):
                    continue
                pairs.append({"class": entry["node"], "label": entry["label"],
                              "alias": alias, "kind": kind})
    return pairs


def alias_collapse(plain: dict[str, dict[str, list[dict]]],
                   withsyn: dict[str, dict[str, list[dict]]],
                   pairs: list[dict[str, str]]) -> dict[str, Any]:
    """Does the synonym condition put two spellings on one node?

    For every declared alias pair, count the cases where both spellings appear
    as separate nodes (split) against the cases where only one does (collapsed
    or simply absent). The comparison that matters is split-rate between the two
    conditions on the same cases.
    """
    def split_rate(per_case) -> dict[str, Any]:
        both = only_one = neither = 0
        examples = []
        for case, per_model in per_case.items():
            names = set()
            for rows in per_model.values():
                names |= {normalize(r["name"]) for r in rows if r["name"]}
            for pair in pairs:
                label = normalize(pair["label"])
                alias = normalize(pair["alias"])
                has_label, has_alias = label in names, alias in names
                if has_label and has_alias:
                    both += 1
                    if len(examples) < 12:
                        examples.append(f"{case}: {pair['label']} + {pair['alias']}")
                elif has_label or has_alias:
                    only_one += 1
                else:
                    neither += 1
        present = both + only_one
        return {"both_spellings_present": both, "one_spelling_present": only_one,
                "neither_present": neither,
                "split_rate": round(both / present, 4) if present else 0.0,
                "examples": examples}

    return {"plain_fibo": split_rate(plain), "with_synonyms": split_rate(withsyn),
            "alias_pairs_tested": len(pairs)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default="")
    ap.add_argument("--class-limit", type=int, default=70)
    args = ap.parse_args()

    import observe
    from neo4j import GraphDatabase

    partial_dir = (ROOT / "outputs/evaluation/mdm_fedcat" /
                   f"log2026-reextract-{args.tag or 'v1'}")
    cases = sorted({json.loads(p.read_text())["case_id"]
                    for p in partial_dir.glob("*.json")})
    if not cases:
        raise SystemExit(f"no partials under {partial_dir}")

    run = observe.Run(OUT_ROOT, "mechanism", {"decisive": {
        "conditions": ["A", "C", "D"], "models": list(MODELS),
        "tag": args.tag or "v1", "cases": cases,
        "class_limit": args.class_limit, "seed": 42}})

    driver = GraphDatabase.driver(URI, auth=auth())
    try:
        graphs = {}
        for arm in ("A", "C", "D"):
            with run.stage(f"read.{arm}", arm=arm, cases=len(cases)) as out:
                graphs[arm] = read_condition(driver, arm, args.tag, cases)
                out["cases_read"] = len(graphs[arm])
                out["nodes"] = sum(len(rows) for per_model in graphs[arm].values()
                                   for rows in per_model.values())

        with run.stage("findability") as out:
            scores = {arm: findability(graphs[arm]) for arm in ("A", "C", "D")}
            paired = []
            for case in cases:
                if case in scores["A"]["per_case"] and case in scores["C"]["per_case"]:
                    paired.append(scores["C"]["per_case"][case]["rate"]
                                  - scores["A"]["per_case"][case]["rate"])
            wins = sum(1 for d in paired if d > 0)
            out["A_pooled"] = scores["A"]["pooled_rate"]
            out["C_pooled"] = scores["C"]["pooled_rate"]
            out["D_pooled"] = scores["D"]["pooled_rate"]
            out["cases_where_FIBO_beats_none"] = f"{wins}/{len(paired)}"
            out["mean_paired_difference"] = (round(sum(paired) / len(paired), 4)
                                             if paired else 0.0)

        with run.stage("alias_collapse", class_limit=args.class_limit) as out:
            pairs = alias_pairs_in_scope(args.class_limit)
            collapse = alias_collapse(graphs["C"], graphs["D"], pairs)
            out["alias_pairs_tested"] = collapse["alias_pairs_tested"]
            out["plain_split_rate"] = collapse["plain_fibo"]["split_rate"]
            out["synonym_split_rate"] = collapse["with_synonyms"]["split_rate"]
            out["plain_both_present"] = collapse["plain_fibo"]["both_spellings_present"]
            out["synonym_both_present"] = collapse["with_synonyms"]["both_spellings_present"]
    finally:
        driver.close()

    payload = {
        "contract": "log2026.mechanism.v1",
        "question": ("Does declaring a type change how findable a thing is "
                     "across views, and do declared synonyms collapse two "
                     "spellings onto one node?"),
        "method": ("Findability is the share of entity names that at least two "
                   "of three models produced, computed per case and paired "
                   "across conditions so the same document is compared with and "
                   "without a schema. Alias collapse counts, for every alias "
                   "pair FIBO declares on a shipped class, whether both "
                   "spellings appear as separate nodes."),
        "claim_boundary": ("Findability is name recurrence across models, not "
                           "retrieval success. Alias collapse can only be seen "
                           "for pairs whose spellings the corpus actually uses, "
                           "so a low count means the test is weak there rather "
                           "than that the mechanism failed."),
        "supersedes": ("the withdrawn within-graph comparison of typed against "
                       "untyped entities, which contrasted coarse entities with "
                       "fine ones rather than schemas with each other"),
        "cases": len(cases), "models": list(MODELS),
        "findability": {arm: {k: v for k, v in scores[arm].items()
                              if k != "per_case"} for arm in scores},
        "paired_cases_where_fibo_wins": f"{wins}/{len(paired)}",
        "mean_paired_difference": (round(sum(paired) / len(paired), 4)
                                   if paired else 0.0),
        "alias_collapse": collapse,
    }
    (run.dir / "mechanism.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    print()
    print("does declaring a type make a thing more findable across models?")
    for arm, label in (("A", "no schema"), ("C", "real FIBO"),
                       ("D", "FIBO + synonyms")):
        cell = scores[arm]
        print(f"  {label:18s} {cell['pooled_rate']:.3f}  "
              f"({cell['pooled_shared']:,} of {cell['pooled_names']:,} names)")
    print(f"  paired by case, FIBO beats no schema in {wins} of {len(paired)}; "
          f"mean difference {sum(paired) / len(paired):+.4f}"
          if paired else "  no paired cases")
    print()
    print("do declared synonyms collapse two spellings onto one node?")
    print(f"  alias pairs tested                 {collapse['alias_pairs_tested']}")
    for key, label in (("plain_fibo", "real FIBO"),
                       ("with_synonyms", "FIBO + synonyms")):
        cell = collapse[key]
        print(f"  {label:18s} both spellings present {cell['both_spellings_present']}, "
              f"one only {cell['one_spelling_present']}, "
              f"split rate {cell['split_rate']:.3f}")

    run.finish({"findability_pooled": {a: scores[a]["pooled_rate"] for a in scores},
                "paired_fibo_wins": f"{wins}/{len(paired)}",
                "alias_split_plain": collapse["plain_fibo"]["split_rate"],
                "alias_split_synonym": collapse["with_synonyms"]["split_rate"],
                "artifact": str((run.dir / "mechanism.json").relative_to(ROOT))})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
