"""Contracts for the official ``jeremycp3/GraphRAG-Bench`` dataset.

This module intentionally models only what the upstream question files prove.
The benchmark provides a question, answer, rationale, question type, and two
topic levels.  It does *not* provide a gold Cypher query, graph triples, or a
question-to-textbook-span mapping.  Those SEOCHO-specific labels are explicit
``unannotated`` fields instead of invented evidence. The upstream dataset
prohibits redistribution and modification, so the on-disk adapter output is a
reference-only ledger: it never copies question, answer, rationale, or corpus
content out of the local upstream snapshot.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..metrics import get_metrics


SCHEMA_VERSION = "seocho.graphrag_bench_case.v1"
MANIFEST_SCHEMA_VERSION = "seocho.graphrag_bench_manifest.v1"
UPSTREAM_REPOSITORY = "https://github.com/jeremycp3/GraphRAG-Bench"
QUESTION_TYPES = ("FB", "MC", "MS", "OE", "TF")


@dataclass(frozen=True)
class GraphRAGBenchCase:
    """One official question plus deliberately incomplete SEOCHO extensions."""

    case_id: str
    question_type: str
    question: str
    answer: Any
    rationale: str | None
    level_1_topic: str
    level_2_topic: str
    upstream: dict[str, str]

    def to_reference_dict(self) -> dict[str, Any]:
        """Return content-free metadata for traces and manifests.

        Question content is read directly from the local upstream snapshot at
        execution time. This avoids making a second, modified distribution of
        an academic-only dataset while preserving exact replay pointers.
        """
        return {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.case_id,
            "question_type": self.question_type,
            "upstream": dict(self.upstream),
            "rationale_status": "present" if self.rationale else "missing_upstream",
            "corpus_binding": {
                "status": "unbound",
                "reason": "upstream question rows do not identify textbook spans or document ids",
            },
            "text2cypher": {
                "status": "unannotated",
                "ambiguity_class": None,
                "intent_slots": [],
                "ontology_profile": None,
                "gold_cypher": None,
                "query_constraints": [],
            },
            "governance_variants": [],
        }


def _required_text(row: Mapping[str, Any], key: str, *, source: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{source}: required non-empty string {key!r} is missing")
    return value.strip()


def _optional_text(row: Mapping[str, Any], key: str) -> str | None:
    """Return an upstream optional string without turning absence into content."""
    value = row.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def parse_question_row(
    row: Mapping[str, Any],
    *,
    question_type: str,
    row_index: int,
    source_file: str,
    source_sha256: str,
) -> GraphRAGBenchCase:
    """Parse one upstream row without guessing unavailable graph labels."""
    if question_type not in QUESTION_TYPES:
        raise ValueError(f"unknown GraphRAG-Bench question type: {question_type}")
    if "Answer" not in row:
        raise ValueError(f"{source_file}:{row_index}: Answer is missing")
    answer = row["Answer"]
    if answer is None or (isinstance(answer, str) and not answer.strip()):
        raise ValueError(f"{source_file}:{row_index}: Answer is empty")
    source = f"{source_file}:{row_index}"
    return GraphRAGBenchCase(
        case_id=f"graphrag-bench:{question_type}:{row_index:06d}",
        question_type=question_type,
        question=_required_text(row, "Question", source=source),
        answer=answer,
        rationale=_optional_text(row, "Rationale"),
        level_1_topic=_required_text(row, "Level-1 Topic", source=source),
        level_2_topic=_required_text(row, "Level-2 Topic", source=source),
        upstream={
            "repository": UPSTREAM_REPOSITORY,
            "source_file": source_file,
            "source_sha256": source_sha256,
        },
    )


def sha256_file(path: Path) -> str:
    """Return the content digest used to pin a local upstream snapshot."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_question_directory(
    question_dir: str | Path, *, limit_per_type: int | None = None
) -> tuple[list[GraphRAGBenchCase], dict[str, Any]]:
    """Load the official five-file layout and return cases plus a manifest.

    ``limit_per_type`` is intended only for a balanced smoke subset.  The
    manifest preserves source file digests even when rows are sampled.
    """
    root = Path(question_dir)
    if limit_per_type is not None and limit_per_type <= 0:
        raise ValueError("limit_per_type must be positive when provided")
    cases: list[GraphRAGBenchCase] = []
    files: dict[str, dict[str, Any]] = {}
    started = time.perf_counter()
    try:
        for question_type in QUESTION_TYPES:
            path = root / f"{question_type}.jsonl"
            if not path.is_file():
                raise FileNotFoundError(f"missing official question file: {path}")
            digest = sha256_file(path)
            parsed: list[GraphRAGBenchCase] = []
            for row_index, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{row_index}: invalid JSON") from exc
                if not isinstance(raw, Mapping):
                    raise ValueError(f"{path}:{row_index}: expected an object")
                parsed.append(
                    parse_question_row(
                        raw,
                        question_type=question_type,
                        row_index=row_index,
                        source_file=path.name,
                        source_sha256=digest,
                    )
                )
            selected = parsed[:limit_per_type] if limit_per_type else parsed
            cases.extend(selected)
            files[question_type] = {
                "path": path.name,
                "sha256": digest,
                "rows_total": len(parsed),
                "rows_selected": len(selected),
                "rationale_missing_total": sum(
                    case.rationale is None for case in parsed
                ),
                "rationale_missing_selected": sum(
                    case.rationale is None for case in selected
                ),
            }
    except Exception:
        get_metrics().add(
            "seocho.benchmark.dataset.prepare.count",
            attributes={"benchmark": "graphrag_bench", "outcome": "error"},
        )
        raise
    elapsed = time.perf_counter() - started
    metrics = get_metrics()
    metrics.record(
        "seocho.benchmark.dataset.prepare.duration",
        elapsed,
        {"benchmark": "graphrag_bench", "outcome": "ok"},
    )
    metrics.add(
        "seocho.benchmark.dataset.prepare.count",
        attributes={"benchmark": "graphrag_bench", "outcome": "ok"},
    )
    for question_type, details in files.items():
        metrics.add(
            "seocho.benchmark.dataset.case.count",
            details["rows_selected"],
            {"benchmark": "graphrag_bench", "question_type": question_type},
        )
    return cases, {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "benchmark": "GraphRAG-Bench",
        "upstream_repository": UPSTREAM_REPOSITORY,
        "question_directory": root.name,
        "files": files,
        "case_count": len(cases),
        "text2cypher_annotation": {"annotated": 0, "unannotated": len(cases)},
        "corpus_binding": {"bound": 0, "unbound": len(cases)},
    }


def write_jsonl(cases: Iterable[GraphRAGBenchCase], path: str | Path) -> int:
    """Write a content-free, local reference ledger, one case per JSON line."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with destination.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(
                json.dumps(case.to_reference_dict(), ensure_ascii=False, sort_keys=True)
                + "\n"
            )
            count += 1
    return count
