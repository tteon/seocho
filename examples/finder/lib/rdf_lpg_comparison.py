"""RDF vs LPG comparison helpers for Tutorial 3.

Three small scoring helpers, one per non-structural evaluation track:

- ``golden_standard_overlap`` — class/relationship overlap with a
  reference ontology (the FIBO core taken as ground truth).
- ``corpus_coverage`` — fraction of corpus-extracted entities that
  the ontology has a class for.
- ``task_track_aggregate`` — collapses per-question Cypher/SPARQL
  results into a single track score.

The fourth (user-based) track is qualitative and runs outside the
notebook; it just emits a CSV scaffold via
``write_user_eval_template``.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence


_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def _norm(s: str) -> str:
    return _NORMALIZE_RE.sub("", str(s).lower()).strip()


def golden_standard_overlap(
    constructed_classes: Iterable[str],
    reference_classes: Iterable[str],
) -> Dict[str, Any]:
    """Compute overlap between constructed and reference class sets.

    Both inputs are iterables of class/label names. Matching is case-
    and punctuation-insensitive.
    """
    constructed = {_norm(c) for c in constructed_classes if c}
    reference = {_norm(c) for c in reference_classes if c}
    intersection = constructed & reference
    union = constructed | reference
    return {
        "constructed_size": len(constructed),
        "reference_size": len(reference),
        "intersection": len(intersection),
        "jaccard": len(intersection) / len(union) if union else 0.0,
        "precision": len(intersection) / len(constructed) if constructed else 0.0,
        "recall": len(intersection) / len(reference) if reference else 0.0,
    }


def corpus_coverage(
    corpus_entities: Iterable[str],
    ontology_class_aliases: Dict[str, Iterable[str]],
) -> Dict[str, Any]:
    """How many corpus-mentioned entities can the ontology classify?

    ``ontology_class_aliases`` maps a class name to its alias list. An
    entity is covered if it normalizes to any class name or alias.
    """
    flat: Dict[str, str] = {}
    for cls, aliases in ontology_class_aliases.items():
        flat[_norm(cls)] = cls
        for a in aliases or []:
            flat[_norm(a)] = cls

    classified: List[Dict[str, str]] = []
    unclassified: List[str] = []
    seen = set()
    for ent in corpus_entities:
        key = _norm(ent)
        if key in seen:
            continue
        seen.add(key)
        if key in flat:
            classified.append({"entity": ent, "class": flat[key]})
        else:
            unclassified.append(ent)

    total = len(seen)
    return {
        "total_distinct_entities": total,
        "classified_count": len(classified),
        "coverage": len(classified) / total if total else 0.0,
        "classified": classified,
        "unclassified": unclassified,
    }


def task_track_aggregate(
    answers: Sequence[Dict[str, Any]],
) -> Dict[str, float]:
    """Aggregate per-question results into the application track.

    Each row in ``answers`` is ``{"answer", "expected", "executed_ok": bool}``.
    Returns answer correctness and query-execution rate.
    """
    if not answers:
        return {"contains_match_rate": 0.0, "exec_success_rate": 0.0, "n": 0}
    contains = 0
    executed = 0
    for a in answers:
        norm_a = _norm(a.get("answer", ""))
        norm_e = _norm(a.get("expected", ""))
        if norm_e and norm_e in norm_a:
            contains += 1
        if a.get("executed_ok"):
            executed += 1
    return {
        "contains_match_rate": contains / len(answers),
        "exec_success_rate": executed / len(answers),
        "n": len(answers),
    }


def write_user_eval_template(
    path: str | Path,
    *,
    questions: Sequence[Dict[str, Any]],
    reviewers: int = 5,
) -> Path:
    """Emit a Likert-form CSV scaffold for the user-based track.

    ``questions`` is a list of ``{"question", "lpg_answer", "rdf_answer"}``
    dicts. The CSV asks each reviewer to rate each path on a 1–5 Likert
    scale across three dimensions: groundedness, completeness, fluency.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "reviewer_id", "question", "path", "answer",
        "groundedness_1to5", "completeness_1to5", "fluency_1to5", "notes",
    ]
    with open(out, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in range(1, reviewers + 1):
            for q in questions:
                for path_label, answer_key in (("lpg", "lpg_answer"), ("rdf", "rdf_answer")):
                    writer.writerow({
                        "reviewer_id": r,
                        "question": q.get("question", ""),
                        "path": path_label,
                        "answer": q.get(answer_key, ""),
                        "groundedness_1to5": "",
                        "completeness_1to5": "",
                        "fluency_1to5": "",
                        "notes": "",
                    })
    return out
