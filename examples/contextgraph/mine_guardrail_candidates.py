#!/usr/bin/env python3
"""Mine ontology-guardrail artifact drafts from context-graph runs.

This uses the current semantic artifact models as the handoff format:
OntologyCandidate + SHACL candidate + VocabularyCandidate. Graph observation is
best-effort; metrics-only mining still captures schema drift such as undeclared
relationship types emitted by extraction prompts.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples" / "contextgraph"))

try:
    from dotenv import dotenv_values
except Exception:  # pragma: no cover - optional local convenience
    dotenv_values = None  # type: ignore[assignment]

if dotenv_values is not None:
    for key, value in dotenv_values(ROOT / ".env").items():
        if value is not None and key not in os.environ:
            os.environ[key] = value

from decision_modules.compose import ARMS, compose_modules
from seocho.semantic import (
    OntologyRelationship,
    SemanticArtifactDraftInput,
    VocabularyCandidate,
    VocabularyTerm,
)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return rows


def _graph_observation(database: str, workspace_prefix: str) -> dict[str, Any]:
    if not database:
        return {"available": False, "reason": "database not provided"}
    try:
        from seocho.store.graph import Neo4jGraphStore

        store = Neo4jGraphStore(
            os.environ["NEO4J_URI"],
            os.environ.get("NEO4J_USER", "neo4j"),
            os.environ.get("NEO4J_PASSWORD", ""),
        )
        label_rows = store.query(
            """
            MATCH (n)
            WHERE coalesce(n._workspace_id, '') STARTS WITH $workspace_prefix
            UNWIND labels(n) AS label
            RETURN label, count(*) AS count
            ORDER BY count DESC, label ASC
            """,
            params={"workspace_prefix": workspace_prefix},
            database=database,
        )
        rel_rows = store.query(
            """
            MATCH ()-[r]->()
            WHERE coalesce(r._workspace_id, '') STARTS WITH $workspace_prefix
            RETURN type(r) AS type, count(*) AS count
            ORDER BY count DESC, type ASC
            """,
            params={"workspace_prefix": workspace_prefix},
            database=database,
        )
        hash_rows = store.query(
            """
            MATCH (n)
            WHERE coalesce(n._workspace_id, '') STARTS WITH $workspace_prefix
            RETURN collect(DISTINCT coalesce(n._ontology_context_hash, '')) AS hashes
            """,
            params={"workspace_prefix": workspace_prefix},
            database=database,
        )
        store.close()
        return {
            "available": True,
            "labels": label_rows,
            "relationships": rel_rows,
            "ontology_context_hashes": (hash_rows[0].get("hashes") if hash_rows else []),
        }
    except Exception as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}


def _metric_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    illegal_types: Counter[str] = Counter()
    arm_counts: Counter[str] = Counter()
    totals = Counter()
    for row in rows:
        arm_counts[str(row.get("arm") or "unknown")] += 1
        for key in (
            "validation_errors",
            "unknown_relationship_errors",
            "illegal_relationships",
            "repaired_relationships",
            "dropped_relationships",
            "add_timeouts",
            "add_errors",
        ):
            try:
                totals[key] += int(row.get(key) or 0)
            except ValueError:
                pass
        for rel_type in row.get("illegal_relationship_types") or []:
            illegal_types[str(rel_type)] += 1
    return {
        "records": len(rows),
        "arms": dict(sorted(arm_counts.items())),
        "totals": dict(sorted(totals.items())),
        "illegal_relationship_types": dict(sorted(illegal_types.items())),
    }


def _vocabulary_terms(
    *,
    graph_summary: dict[str, Any],
    metrics: dict[str, Any],
) -> list[VocabularyTerm]:
    terms: list[VocabularyTerm] = [
        VocabularyTerm(
            pref_label="ontology guardrail",
            alt_labels=["schema firewall", "semantic artifact guardrail"],
            broader=["context graph governance"],
            definition=(
                "A promoted semantic artifact that constrains extraction labels, "
                "relationships, and canonical decision-process vocabulary."
            ),
            sources=["contextgraph.guardrail.experiment"],
        ),
        VocabularyTerm(
            pref_label="who when where how",
            alt_labels=["process context", "decision process frame"],
            broader=["context graph governance"],
            definition=(
                "Decision-process extraction frame covering actor, time, place, "
                "method, proposal, position, and outcome."
            ),
            sources=["process_context", "process_position"],
        ),
    ]
    for rel_type, count in metrics.get("illegal_relationship_types", {}).items():
        terms.append(
            VocabularyTerm(
                pref_label=f"blocked relation {rel_type}",
                alt_labels=[rel_type],
                broader=["schema drift"],
                related=["strict strip", "repair-invalid-rels"],
                definition=(
                    "Undeclared relationship emitted by an extraction prompt; "
                    "guardrail treatment is canonical mapping or drop."
                ),
                examples=[f"observed_in_metric_records={count}"],
                sources=["build_metrics"],
            )
        )
    for row in graph_summary.get("relationships") or []:
        rel_type = str(row.get("type") or "").strip()
        if rel_type:
            terms.append(
                VocabularyTerm(
                    pref_label=f"observed relation {rel_type}",
                    alt_labels=[rel_type],
                    broader=["observed graph schema"],
                    examples=[f"count={row.get('count')}"],
                    sources=["graph_observation"],
                )
            )
    return terms


def _guardrail_relationships(illegal_types: dict[str, int]) -> list[OntologyRelationship]:
    relationships: list[OntologyRelationship] = []
    if illegal_types:
        relationships.append(
            OntologyRelationship(
                type="CANONICALIZES",
                source="Method",
                target="DecisionEvent",
                description=(
                    "Governance relation used offline to document that observed "
                    "schema-drift expressions were mapped to declared event schema."
                ),
                aliases=["maps drift", "normalizes extraction"],
                related=sorted(illegal_types),
            )
        )
    return relationships


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", default="process_position", choices=sorted(ARMS))
    ap.add_argument("--metrics-jsonl", action="append", default=[])
    ap.add_argument("--database", default="")
    ap.add_argument("--workspace-prefix", required=True)
    ap.add_argument("--name", default="contextgraph-guardrail-candidate")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    metric_rows: list[dict[str, Any]] = []
    for path in args.metrics_jsonl:
        metric_rows.extend(_load_jsonl(Path(path)))
    metrics = _metric_summary(metric_rows)
    graph_summary = _graph_observation(args.database, args.workspace_prefix)

    ontology = compose_modules(ARMS[args.arm])
    draft: SemanticArtifactDraftInput = ontology.to_semantic_artifact_draft(
        name=args.name,
        source_summary={
            "source": "contextgraph_guardrail_mining",
            "arm": args.arm,
            "database": args.database,
            "workspace_prefix": args.workspace_prefix,
            "metrics": metrics,
            "graph_observation": graph_summary,
            "guardrail_contract": {
                "allowed_relationships": sorted(ontology.relationships),
                "drift_policy": "strict-strip plus artifact-governed canonical mapping",
                "runtime_policy_target": "approved_only after artifact review",
            },
            "expected_effects": [
                "lower illegal_relationships and dropped_relationships",
                "higher E3/E4 graph or hybrid answer quality",
                "stable ontology_context_hash across indexed workspaces",
            ],
        },
    )
    if draft.vocabulary_candidate is None:
        draft.vocabulary_candidate = VocabularyCandidate()
    existing = {term.pref_label for term in draft.vocabulary_candidate.terms}
    for term in _vocabulary_terms(graph_summary=graph_summary, metrics=metrics):
        if term.pref_label not in existing:
            draft.vocabulary_candidate.terms.append(term)
            existing.add(term.pref_label)

    for rel in _guardrail_relationships(metrics.get("illegal_relationship_types", {})):
        draft.ontology_candidate.relationships.append(rel)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(draft.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "out": str(out),
        "arm": args.arm,
        "metrics_records": metrics["records"],
        "illegal_relationship_types": metrics["illegal_relationship_types"],
        "graph_available": graph_summary.get("available", False),
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
