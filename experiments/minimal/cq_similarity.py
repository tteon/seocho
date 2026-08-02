#!/usr/bin/env python3
"""Was FIBO designed to answer the questions this corpus asks?

Vocabulary coverage says FIBO can *name* what the questions are about. It does
not say FIBO was built to *answer* them. An ontology designed for regulatory
reporting and one designed for analyst commentary can share vocabulary and still
support different questions.

So this compares question to question. Competency questions are generated
mechanically from FIBO's own declared classes and properties, in the shape
competency questions take, and then compared to the real FinDER questions by
meaning.

Generation is mechanical on purpose. Hand-picking which competency questions to
write would let the author choose the ones that match, which would measure the
author rather than the ontology.

Embeddings are local BGE; no API is called.
"""
from __future__ import annotations

import collections
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_DIR = ROOT / "examples/finder/datasets/fibo_modules"
OUT = ROOT / "outputs/evaluation/mdm_fedcat/log2026-cq-similarity-v1"
MODEL = "BAAI/bge-small-en-v1.5"

# The shapes competency questions take in ontology engineering: retrieve a
# property of a class, relate two classes, constrain by time.
TEMPLATES = (
    "What is the {prop} of a {cls}?",
    "Which {cls} has a given {prop}?",
    "What is the {prop} of a {cls} for a given period?",
)
RELATION_TEMPLATE = "Which {target} is {rel} a {source}?"


def spaced(label: str) -> str:
    parts = re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+|\d+", str(label))
    return " ".join(p.lower() for p in parts) or str(label).lower()


def build_competency_questions() -> list[dict]:
    import yaml

    questions = []
    for path in sorted(MODULE_DIR.glob("*.yaml")):
        spec = yaml.safe_load(path.read_text()) or {}
        for cls, body in (spec.get("nodes") or {}).items():
            body = body or {}
            for prop in (body.get("properties") or {}):
                for template in TEMPLATES:
                    questions.append({
                        "module": path.stem, "source": f"{cls}.{prop}",
                        "question": template.format(cls=spaced(cls), prop=spaced(prop)),
                    })
        for rel, body in (spec.get("relationships") or {}).items():
            body = body or {}
            if body.get("source") and body.get("target"):
                questions.append({
                    "module": path.stem, "source": rel,
                    "question": RELATION_TEMPLATE.format(
                        rel=spaced(rel), source=spaced(body["source"]),
                        target=spaced(body["target"])),
                })
    # Deduplicate on the question text; templates collide across modules.
    seen, unique = set(), []
    for q in questions:
        if q["question"] not in seen:
            seen.add(q["question"])
            unique.append(q)
    return unique


def load_finder() -> list[dict]:
    spec = importlib.util.spec_from_file_location(
        "finder_index", ROOT / "examples/mdm/11_index_providers.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.load_cases_full(seed=42)


def main() -> int:
    from sentence_transformers import SentenceTransformer
    import numpy as np

    cqs = build_competency_questions()
    cases = load_finder()
    print(f"competency questions generated: {len(cqs)}")
    print(f"FinDER questions: {len(cases)}")

    model = SentenceTransformer(MODEL)
    cq_vecs = model.encode([q["question"] for q in cqs], normalize_embeddings=True,
                           batch_size=128, show_progress_bar=False)

    by_category: dict[str, list[dict]] = collections.defaultdict(list)
    for case in cases:
        by_category[str(case["category"])].append(case)

    # A control: how similar are FinDER questions to each other within a
    # category? Without it, a similarity number has no scale.
    results = {}
    for category, rows in sorted(by_category.items()):
        texts = [str(r["query"]) for r in rows]
        vecs = model.encode(texts, normalize_embeddings=True, batch_size=128,
                            show_progress_bar=False)
        sim = vecs @ cq_vecs.T                      # questions x competency questions
        best = sim.max(axis=1)
        best_idx = sim.argmax(axis=1)
        within = vecs @ vecs.T
        np.fill_diagonal(within, -1.0)
        control = float(within.max(axis=1).mean())

        matched = collections.Counter(cqs[i]["module"] for i in best_idx)
        results[category] = {
            "questions": len(texts),
            "mean_best_similarity": round(float(best.mean()), 6),
            "median_best_similarity": round(float(np.median(best)), 6),
            "share_above_0_5": round(float((best > 0.5).mean()), 6),
            "share_above_0_6": round(float((best > 0.6).mean()), 6),
            "control_within_category": round(control, 6),
            "nearest_cq_modules": dict(matched.most_common(4)),
            "examples": [
                {"finder": texts[i][:110],
                 "nearest_cq": cqs[best_idx[i]]["question"],
                 "similarity": round(float(best[i]), 4)}
                for i in list(np.argsort(-best)[:2]) + list(np.argsort(best)[:2])
            ],
        }
        print(f"  {category:20s} best-sim mean {best.mean():.3f}  "
              f">0.5 {(best > 0.5).mean():.2f}  control {control:.3f}")

    payload = {
        "contract": "log2026.cq_similarity.v1",
        "question": ("Are FIBO's competency questions the same kind of question "
                     "FinDER asks?"),
        "method": (f"competency questions generated mechanically from FIBO classes, "
                   f"properties and relationships; local {MODEL} embeddings; cosine "
                   f"similarity; no API calls"),
        "control": ("within-category nearest-neighbour similarity among FinDER "
                    "questions themselves, so the competency-question numbers have "
                    "a scale to be read against"),
        "claim_boundary": ("Similarity of phrasing and topic, not of answerability. "
                           "A high score means FIBO asks this kind of question, not "
                           "that the graph can answer it."),
        "competency_questions": len(cqs),
        "by_category": results,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "cq_similarity.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    lines = ["# Are FIBO's competency questions the kind FinDER asks?", "",
             f"{len(cqs)} competency questions generated mechanically from FIBO "
             f"classes, properties and relationships. Local {MODEL}.", "",
             "| Category | Questions | Best sim (mean) | >0.5 | >0.6 | Control (FinDER to itself) |",
             "|---|---:|---:|---:|---:|---:|"]
    for category, cell in results.items():
        lines.append(f"| {category} | {cell['questions']:,} | "
                     f"{cell['mean_best_similarity']:.3f} | "
                     f"{cell['share_above_0_5']:.2f} | {cell['share_above_0_6']:.2f} | "
                     f"{cell['control_within_category']:.3f} |")
    lines += ["", "## Closest and furthest, per category", ""]
    for category, cell in results.items():
        lines.append(f"**{category}**")
        for ex in cell["examples"][:2]:
            lines.append(f"- `{ex['similarity']:.3f}`  {ex['finder']}")
            lines.append(f"     nearest: *{ex['nearest_cq']}*")
        lines.append("")
    lines += [payload["claim_boundary"], ""]
    (OUT / "cq_similarity.md").write_text("\n".join(lines))
    print()
    print("\n".join(lines[:24]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
