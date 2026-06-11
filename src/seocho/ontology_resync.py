"""Fix-and-resync orchestrator (GRL principles 3 & 4; ADR-0103 "Plugin" level).

GRL "AI-Assisted Ontology Engineering" (KGC 2026) names two disciplines that
separate the methodology from ad-hoc LLM use:

  3. **Always validate after change** — every ontology edit is followed by a
     lint / SHACL / competency-question re-run.
  4. **Sync downstream artefacts** — every edit regenerates the dependent
     artefacts (SHACL, JSON-LD, CQ coverage) so they cannot drift.

SEOCHO already has the individual pure functions (``to_shacl``, ``to_jsonld``,
``governance_gate``, ``competency_question_report``, ``conformance_score``,
``diff_ontologies``). What was missing is the *orchestration* that runs them in
one continuous flow on an edit — the "Plugin" maturity level in GRL's
Skill -> Plugin -> Harness model. This module is that orchestration, and nothing
more: glue over functions that are already correct.

Pure / offline by construction (every composed function is offline; the reasoner
is OFF by default per CLAUDE.md §6.3). It returns a plain dict so the caller can
emit the vendor-neutral JSONL trace that is the lineage record (§9) — we keep the
orchestrator itself free of any tracing-vendor import.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .ontology import Ontology
from .ontology_governance import (
    competency_question_report,
    conformance_score,
    diff_ontologies,
    governance_gate,
)


def resync_ontology(
    ontology: Ontology,
    *,
    workspace_id: str = "",
    competency_questions: Optional[Sequence[Dict[str, Any]]] = None,
    prior: Optional[Ontology] = None,
    run_reasoner: bool = False,
    conformance_threshold: float = 0.8,
) -> Dict[str, Any]:
    """Regenerate downstream artefacts + re-validate an ontology in one flow.

    Composes (all offline, all pre-existing):

      - ``to_shacl()``                 — regenerate SHACL shapes
      - ``to_jsonld()``                — regenerate the JSON-LD context
      - ``governance_gate()``          — structural + lint (+ optional reasoner)
      - ``competency_question_report`` — re-run CQ coverage / per-CQ expressibility
      - ``conformance_score()``        — the scored release gate (GRL Artefact 7)
      - ``diff_ontologies(prior, …)``  — version bump + migration warnings vs prior

    ``ok`` is True only when the governance gate passes AND the conformance score
    clears its threshold — i.e. the edit is safe to release. The returned dict is
    JSON-serialisable; wrap this call in ``@track`` / write it to a JSONL trace at
    the call site to record lineage (we keep the orchestrator vendor-neutral).

    Args:
        workspace_id: propagated into the report for §6.1 partitioning.
        competency_questions: authored CQ set (see
            ``examples/finder/datasets/competency_questions.yaml``); when given,
            CQ coverage feeds both the report and the conformance score.
        prior: the previous ontology version, for the migration diff.
        run_reasoner: opt-in Pellet consistency (offline only); default off.
    """
    shacl = ontology.to_shacl()
    shacl_shapes = [s for s in shacl.get("shapes", []) if isinstance(s, dict)]
    jsonld = ontology.to_jsonld()

    gate = governance_gate(ontology, run_reasoner=run_reasoner)
    cq = (
        competency_question_report(ontology, competency_questions)
        if competency_questions
        else None
    )
    conformance = conformance_score(
        ontology,
        competency_questions=competency_questions,
        run_reasoner=run_reasoner,
        threshold=conformance_threshold,
    )
    diff = diff_ontologies(prior, ontology).to_dict() if prior is not None else None

    notes: List[str] = []
    if cq and cq["schema_impossible_count"]:
        notes.append(
            f"{cq['schema_impossible_count']}/{cq['question_count']} competency "
            "questions are structurally impossible for this ontology."
        )
    if not conformance["passed"]:
        notes.append(
            f"conformance {conformance['score']} < threshold "
            f"{conformance['threshold']} or a hard gate failed."
        )
    if diff and diff.get("requires_migration"):
        notes.append("downstream data migration required (see diff.migration_warnings).")

    return {
        "workspace_id": workspace_id,
        "ontology": {
            "name": ontology.name,
            "package_id": ontology.package_id,
            "version": ontology.version,
            "node_count": len(ontology.nodes),
            "relationship_count": len(ontology.relationships),
        },
        "ok": bool(gate["ok"] and conformance["passed"]),
        "shacl": {
            "node_shape_count": len(shacl_shapes),
            "property_shape_count": sum(
                len(s.get("properties", [])) for s in shacl_shapes
            ),
        },
        "jsonld_present": bool(jsonld),
        "gate": gate,
        "competency": cq,
        "conformance": conformance,
        "diff": diff,
        "notes": notes,
    }
