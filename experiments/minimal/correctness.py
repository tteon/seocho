#!/usr/bin/env python3
"""Does the graph hold the right answer, and do two models hold the same one?

Everything measured so far asks whether two models NAME a fact the same way.
That is silent on two things a reader will ask immediately.

Is the graph right?
    The dataset ships a gold answer for every case. Nothing has ever checked
    whether the extracted graph contains it. Without that, "no schema wins on
    agreement" cannot be read as "no schema is better" — a schema could lower
    agreement and raise accuracy, and the result would look like a loss.

Do the models capture the same content?
    Agreement on names is not agreement on facts. Two extractions could disagree
    on every identifier and hold identical figures, or agree on every name and
    hold different ones. Comparing the values, and comparing node content by
    meaning rather than by string, separates the two.

Both are measured against the same cases and conditions as the agreement result,
so the three can be read together. Numbers are compared with their scale word
applied, so 59.4 and 59.4 million are different. Embeddings are local; no API
is called.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
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

import parallel  # noqa: E402

URI = "bolt://localhost:7687"
OUT_ROOT = ROOT / "outputs/minimal"
MODELS = ("deepseek", "gptoss", "minimax27")
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
INFRA = {"Document", "Chunk", "Version", "DocumentVersion", "Section",
         "__Memory__", "Memory"}

_NUMBER = re.compile(r"-?\d[\d,]*\.?\d*")
_SCALE = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}


def auth() -> tuple[str, str]:
    return (os.environ.get("NEO4J_USER", "neo4j"),
            os.environ.get("NEO4J_PASSWORD", ""))


def numbers_in(text: str) -> set[float]:
    """Every figure in a piece of text, with any scale word applied.

    A percentage is left as written rather than divided, because the gold
    answers write percentages as percentages and dividing would make a correct
    extraction look wrong.
    """
    raw = str(text or "")
    lowered = raw.lower()
    factor = 1.0
    for word, value in _SCALE.items():
        if re.search(rf"\b{word}s?\b", lowered):
            factor = value
            break
    found = set()
    for match in _NUMBER.finditer(raw):
        try:
            value = float(match.group(0).replace(",", ""))
        except ValueError:
            continue
        if value == 0:
            continue
        found.add(value)
        if factor != 1.0:
            found.add(value * factor)
    return found


def close(a: float, b: float, tolerance: float = 0.01) -> bool:
    if a == b:
        return True
    scale = max(abs(a), abs(b))
    return scale > 0 and abs(a - b) / scale <= tolerance


def load_cases() -> dict[str, dict[str, Any]]:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "finder_index", ROOT / "examples/mdm/11_index_providers.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {c["case_id"]: c for c in module.load_cases_full(seed=42)}


def serialize(row: dict[str, Any]) -> str:
    labels = [l for l in (row.get("labels") or []) if l not in INFRA]
    parts = [str(row.get("name", ""))]
    if labels:
        parts.append("(" + ", ".join(sorted(labels)) + ")")
    if str(row.get("value") or "").strip():
        parts.append("= " + str(row["value"]))
    if str(row.get("period") or "").strip():
        parts.append("for " + str(row["period"]))
    return " ".join(p for p in parts if p)


def read_condition(driver, arm: str, tag: str,
                   cases: list[str]) -> dict[str, dict[str, list[dict]]]:
    def read(model: str) -> tuple[str, dict[str, list[dict]]]:
        database = f"arm{tag}{arm.lower()}{model}" if tag else f"arm{arm.lower()}{model}"
        found: dict[str, list[dict]] = {}
        with driver.session(database=database) as session:
            for case in cases:
                workspace = (f"arm{tag}-{arm.lower()}-{model}-{case}" if tag
                             else f"arm{arm.lower()}-{model}-{case}")
                rows = session.run(
                    "MATCH (n {_workspace_id:$w}) "
                    "RETURN labels(n) AS labels, n.name AS name, "
                    "       coalesce(n.value, n.amount, '') AS value, "
                    "       coalesce(n.period, '') AS period", w=workspace).data()
                found[case] = [r for r in rows if not (set(r["labels"]) & INFRA)]
        return model, found

    per_case: dict[str, dict[str, list[dict]]] = defaultdict(dict)
    for result in parallel.io_map(read, list(MODELS)):
        if result is None:
            continue
        model, found = result
        for case, rows in found.items():
            per_case[case][model] = rows
    return per_case


def gold_coverage(per_case: dict[str, dict[str, list[dict]]],
                  cases: dict[str, dict[str, Any]], encoder) -> dict[str, Any]:
    """Two readings of 'the graph holds the answer', because one is not enough.

    numeric   the figures in the gold answer that appear somewhere in the graph.
              Strict and unambiguous, but silent on any answer that is prose.
    semantic  the best cosine similarity between the gold answer and any single
              serialized node. Covers prose answers, and is a similarity rather
              than a hit, so it is reported as a distribution.
    """
    import numpy as np

    numeric_rows, semantic_rows = [], []
    for case, per_model in per_case.items():
        gold = cases.get(case, {}).get("expected_answer", "")
        wanted = numbers_in(gold)
        for model, rows in per_model.items():
            available: set[float] = set()
            for row in rows:
                available |= numbers_in(row.get("value", ""))
                available |= numbers_in(row.get("name", ""))
            if wanted:
                hit = sum(1 for w in wanted
                          if any(close(w, a) for a in available))
                numeric_rows.append({"case": case, "model": model,
                                     "gold_numbers": len(wanted), "found": hit,
                                     "rate": round(hit / len(wanted), 4)})
            texts = [serialize(r) for r in rows if serialize(r).strip()]
            if gold.strip() and texts:
                vectors = encoder.encode([gold] + texts,
                                         normalize_embeddings=True,
                                         batch_size=128, show_progress_bar=False)
                sims = vectors[0] @ vectors[1:].T
                semantic_rows.append({"case": case, "model": model,
                                      "best": round(float(sims.max()), 4),
                                      "top5_mean": round(
                                          float(np.sort(sims)[-5:].mean()), 4)})
    numeric_rate = ([r["rate"] for r in numeric_rows] or [0.0])
    best = ([r["best"] for r in semantic_rows] or [0.0])
    return {
        "cases_with_numeric_gold": len({r["case"] for r in numeric_rows}),
        "numeric_recall_mean": round(float(np.mean(numeric_rate)), 4),
        "numeric_recall_median": round(float(np.median(numeric_rate)), 4),
        "numeric_fully_covered": sum(1 for r in numeric_rows if r["rate"] >= 0.999),
        "numeric_none_covered": sum(1 for r in numeric_rows if r["rate"] == 0.0),
        "numeric_observations": len(numeric_rows),
        "semantic_best_mean": round(float(np.mean(best)), 4),
        "semantic_best_median": round(float(np.median(best)), 4),
        "semantic_observations": len(semantic_rows),
    }


def content_agreement(per_case: dict[str, dict[str, list[dict]]],
                      encoder) -> dict[str, Any]:
    """Do two models hold the same content, ignoring what they called it?

    Two views of that. Value overlap is the strict one: the figures one model
    extracted that another also extracted, matched numerically and never by
    name. Semantic pairing is the loose one: for each node in one model, the
    best-matching node in the other by meaning, and the share of nodes whose
    best match clears a threshold.
    """
    import numpy as np

    value_rows, semantic_rows = [], []
    for case, per_model in per_case.items():
        models = sorted(per_model)
        values = {m: set().union(*[numbers_in(r.get("value", ""))
                                   for r in per_model[m]] or [set()])
                  for m in models}
        for left, right in combinations(models, 2):
            union = values[left] | values[right]
            if not union:
                continue
            shared = sum(1 for v in union
                         if any(close(v, a) for a in values[left])
                         and any(close(v, a) for a in values[right]))
            value_rows.append({"case": case, "pair": f"{left}|{right}",
                               "union": len(union), "shared": shared,
                               "jaccard": round(shared / len(union), 4)})
        texts = {m: [serialize(r) for r in per_model[m] if serialize(r).strip()]
                 for m in models}
        vectors = {}
        for model, items in texts.items():
            if items:
                vectors[model] = encoder.encode(items, normalize_embeddings=True,
                                                batch_size=128,
                                                show_progress_bar=False)
        for left, right in combinations(sorted(vectors), 2):
            sim = vectors[left] @ vectors[right].T
            best_left = sim.max(axis=1)
            semantic_rows.append({
                "case": case, "pair": f"{left}|{right}",
                "matched_above_0_9": round(float((best_left > 0.9).mean()), 4),
                "mean_best": round(float(best_left.mean()), 4)})

    jaccard = ([r["jaccard"] for r in value_rows] or [0.0])
    matched = ([r["matched_above_0_9"] for r in semantic_rows] or [0.0])
    return {
        "value_pairs": len(value_rows),
        "value_jaccard_mean": round(float(np.mean(jaccard)), 4),
        "value_jaccard_median": round(float(np.median(jaccard)), 4),
        "semantic_pairs": len(semantic_rows),
        "semantic_matched_mean": round(float(np.mean(matched)), 4),
        "semantic_mean_best": round(float(np.mean(
            [r["mean_best"] for r in semantic_rows] or [0.0])), 4),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default="")
    ap.add_argument("--arms", default="A,B,C,D")
    args = ap.parse_args()

    import observe
    from neo4j import GraphDatabase
    from sentence_transformers import SentenceTransformer

    arms = [a.strip().upper() for a in args.arms.split(",") if a.strip()]
    partial_dir = (ROOT / "outputs/evaluation/mdm_fedcat" /
                   f"log2026-reextract-{args.tag or 'v1'}")
    case_ids = sorted({json.loads(p.read_text())["case_id"]
                       for p in partial_dir.glob("*.json")})
    if not case_ids:
        raise SystemExit(f"no partials under {partial_dir}")

    run = observe.Run(OUT_ROOT, "correctness", {"decisive": {
        "conditions": arms, "models": list(MODELS), "tag": args.tag or "v1",
        "cases": case_ids, "embedding_model": EMBED_MODEL,
        "numeric_tolerance": 0.01, "seed": 42}})

    with run.stage("load") as out:
        cases = load_cases()
        encoder = SentenceTransformer(EMBED_MODEL)
        out["cases_available"] = len(cases)
        out["cases_used"] = len(case_ids)

    driver = GraphDatabase.driver(URI, auth=auth())
    results: dict[str, Any] = {}
    try:
        for arm in arms:
            with run.stage(f"read.{arm}", arm=arm) as out:
                graph = read_condition(driver, arm, args.tag, case_ids)
                out["cases"] = len(graph)
            with run.stage(f"gold.{arm}", arm=arm) as out:
                gold = gold_coverage(graph, cases, encoder)
                out.update(gold)
            with run.stage(f"content.{arm}", arm=arm) as out:
                content = content_agreement(graph, encoder)
                out.update(content)
            results[arm] = {"gold": gold, "content": content}
    finally:
        driver.close()

    payload = {
        "contract": "log2026.correctness.v1",
        "question": ("Does the extracted graph contain the dataset's gold "
                     "answer, and do two models capture the same content "
                     "regardless of what they called it?"),
        "method": ("Numeric recall is the share of figures in the gold answer "
                   "that appear anywhere in the graph, matched within 1% and "
                   "with scale words applied. Semantic best is the highest "
                   f"cosine similarity between the gold answer and any single "
                   f"serialized node, local {EMBED_MODEL}. Content agreement is "
                   "reported twice: numeric overlap of extracted values, which "
                   "never looks at names, and the share of nodes whose best "
                   "semantic match in the other model's graph exceeds 0.9."),
        "claim_boundary": ("Numeric recall asks whether the figure is present "
                           "somewhere in the graph, not whether it is attached "
                           "to the right entity or retrievable by a query. A "
                           "graph can score well here and still be unusable. "
                           "Semantic similarity is a proxy for sameness, not a "
                           "judgement of correctness."),
        "cases": len(case_ids), "models": list(MODELS),
        "by_condition": results,
    }
    (run.dir / "correctness.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    print()
    print(f"{'condition':10s} {'gold numbers found':>19s} {'never found':>12s} "
          f"{'gold semantic':>14s} {'value overlap':>14s} {'content match':>14s}")
    for arm in arms:
        g, c = results[arm]["gold"], results[arm]["content"]
        print(f"{arm:10s} {g['numeric_recall_mean']:19.3f} "
              f"{g['numeric_none_covered']:6d}/{g['numeric_observations']:<5d} "
              f"{g['semantic_best_mean']:14.3f} {c['value_jaccard_mean']:14.3f} "
              f"{c['semantic_matched_mean']:14.3f}")
    print("\ngold numbers found = share of the answer's figures present in the graph")
    print("value overlap      = figures two models both extracted, names ignored")
    print("content match      = share of nodes with a >0.9 semantic twin in the other model")

    run.finish({"by_condition": {a: {
        "numeric_recall": results[a]["gold"]["numeric_recall_mean"],
        "value_jaccard": results[a]["content"]["value_jaccard_mean"],
        "content_match": results[a]["content"]["semantic_matched_mean"]}
        for a in arms},
        "artifact": str((run.dir / "correctness.json").relative_to(ROOT))})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
