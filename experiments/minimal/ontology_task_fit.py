#!/usr/bin/env python3
"""Is FIBO the right vocabulary for these questions, and where is it not?

Before asking whether an ontology helped, ask whether it covers the task. If a
category's questions are about board composition and no loaded module declares
governance concepts, a poor result there is a scope mismatch, not an ontology
failure. Those two have been conflated so far.

This measures, per FinDER category, how much of the questions' subject matter
the FIBO vocabulary can name, and which modules supply that coverage. The output
is a per-category module recommendation, which is also the answer to a question
never asked at the start: which modules should have been loaded.

Method and its limits: term overlap between question text and class labels,
aliases, and property names. That is a proxy for conceptual coverage and it
under-counts, because a concept can be present under a name the question does
not use. It is a lower bound and a direction indicator, not a score.

Read-only, no model calls.
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
OUT = ROOT / "outputs/evaluation/mdm_fedcat/log2026-ontology-task-fit-v1"

# Question words that carry no subject matter. Kept explicit so the filter is
# auditable rather than a hidden library list.
STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "by",
    "at", "from", "as", "is", "are", "was", "were", "be", "been", "being",
    "what", "which", "how", "why", "when", "where", "who", "whom", "does", "do",
    "did", "can", "could", "should", "would", "may", "might", "will", "shall",
    "this", "that", "these", "those", "it", "its", "their", "his", "her", "our",
    "we", "us", "you", "they", "them", "there", "here", "not", "no", "any",
    "all", "some", "each", "per", "than", "then", "if", "so", "such", "more",
    "most", "other", "into", "over", "under", "about", "between", "during",
    "company", "companys", "report", "reported", "reporting", "year", "years",
    "amount", "total", "based", "given", "used", "using", "impact", "impacts",
    "affect", "affects", "may", "significantly", "term", "long", "short",
}


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", str(text).lower())
            if len(w) > 2 and w not in STOP}


def camel_words(label: str) -> set[str]:
    """LegalEntity -> {legal, entity}; HAS_SUBSIDIARY -> {has, subsidiary}."""
    parts = re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+|\d+", str(label))
    return {p.lower() for p in parts if len(p) > 2 and p.lower() not in STOP}


def load_vocabulary() -> dict[str, set[str]]:
    """Module -> the words its classes, aliases, and properties introduce."""
    import yaml

    vocab: dict[str, set[str]] = {}
    for path in sorted(MODULE_DIR.glob("*.yaml")):
        spec = yaml.safe_load(path.read_text()) or {}
        terms: set[str] = set()
        for cls, body in (spec.get("nodes") or {}).items():
            terms |= camel_words(cls)
            body = body or {}
            for alias in body.get("aliases") or []:
                terms |= camel_words(alias)
            for prop in (body.get("properties") or {}):
                terms |= camel_words(prop)
        for rel, body in (spec.get("relationships") or {}).items():
            terms |= camel_words(rel)
        vocab[path.stem] = terms
    return vocab


def load_questions() -> list[dict]:
    spec = importlib.util.spec_from_file_location(
        "finder_index", ROOT / "examples/mdm/11_index_providers.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.load_cases_full(seed=42)


def main() -> int:
    vocab = load_vocabulary()
    local_terms = set().union(*vocab.values())
    # Real FIBO, including the synonym layer the local modules do not have.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import fibo_vocabulary
    real = fibo_vocabulary.build()
    sources = {"local_modules": local_terms, "real_fibo": real.terms}
    print(f"local modules: {len(local_terms)} terms | "
          f"real FIBO: {len(real.terms)} terms, {len(real.phrases)} phrases")
    cases = load_questions()

    by_category: dict[str, list[dict]] = collections.defaultdict(list)
    for case in cases:
        by_category[str(case["category"])].append(case)

    results = {}
    comparison: dict[str, dict[str, float]] = {}
    for source_name, everything in sources.items():
      results[source_name] = {}
      for category, rows in sorted(by_category.items()):
          term_counts: collections.Counter = collections.Counter()
          covered_counts: collections.Counter = collections.Counter()
          per_question = []
          for row in rows:
              terms = words(row["query"])
              if not terms:
                  continue
              hit = terms & everything
              term_counts.update(terms)
              covered_counts.update(hit)
              per_question.append(len(hit) / len(terms))

          # Which modules would supply this category's covered vocabulary.
          module_contribution = {
              module: len(covered_counts.keys() & terms)
              for module, terms in vocab.items()
          }
          ranked = sorted(module_contribution.items(), key=lambda kv: -kv[1])
          uncovered = [w for w, _ in term_counts.most_common() if w not in everything]

          results[source_name][category] = {
              "questions": len(per_question),
              "distinct_terms": len(term_counts),
              "covered_terms": len(covered_counts),
              "vocabulary_coverage": round(len(covered_counts) / len(term_counts), 6)
              if term_counts else 0.0,
              "mean_question_coverage": round(sum(per_question) / len(per_question), 6)
              if per_question else 0.0,
              "modules_ranked": [{"module": m, "terms": n} for m, n in ranked if n],
              "top_uncovered_terms": uncovered[:15],
          }
          cell = results[source_name][category]
          comparison.setdefault(category, {})[source_name] = cell["mean_question_coverage"]
          print(f"  [{source_name:14s}] {category:20s} "
                f"vocab {cell['vocabulary_coverage']:.3f}  "
                f"per-question {cell['mean_question_coverage']:.3f}")

    payload = {
        "contract": "log2026.ontology_task_fit.v1",
        "question": "Does the FIBO vocabulary cover what each FinDER category asks about?",
        "method": ("term overlap between question text and class labels, aliases, and "
                   "property names from the local FIBO modules"),
        "claim_boundary": ("A lower bound. A concept present under a name the question "
                           "does not use counts as uncovered, so this indicates "
                           "direction and gaps, not a coverage score."),
        "modules": {m: len(t) for m, t in sorted(vocab.items())},
        "vocabulary_terms_total": len(everything),
        "by_category": results,
        "per_question_coverage_comparison": comparison,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ontology_task_fit.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    lines = ["# Ontology-task fit by FinDER category", "",
             f"Local hand-written modules: {len(local_terms)} terms. "
             f"Real FIBO: {len(real.terms)} terms including its synonym layer.", "",
             "| Category | Questions | Local per-question | Real FIBO per-question | Gain |",
             "|---|---:|---:|---:|---:|"]
    for category in sorted(comparison):
        loc = comparison[category].get("local_modules", 0.0)
        rea = comparison[category].get("real_fibo", 0.0)
        n = results["real_fibo"][category]["questions"]
        lines.append(f"| {category} | {n:,} | {loc:.3f} | {rea:.3f} | {rea - loc:+.3f} |")
    lines += ["", "## What real FIBO still cannot name", ""]
    for category, cell in results["real_fibo"].items():
        lines.append(f"- **{category}**: {', '.join(cell['top_uncovered_terms'][:10])}")
    lines += ["", payload["claim_boundary"], ""]
    (OUT / "ontology_task_fit.md").write_text("\n".join(lines))
    print()
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
