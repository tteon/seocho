"""A three-level breakdown of a whole run: stage -> dimension -> finding.

`score_ontology` grades one artefact across four dimensions. That is the right
shape for "is this ontology good enough" and the wrong shape for "where is this
pipeline losing", because it stops at the ontology and a run has three other
stages that can each be the reason an answer is wrong.

The levels exist so a reader can descend rather than scan:

  대분류  STAGE       ontology / indexing / retrieval / generation
                      the four places a run can fail, and the only level a
                      reader should have to look at first
  중분류  DIMENSION   what within that stage — taxonomy health, vocabulary
                      compliance, plan quality, grounding
  소분류  FINDING     the specific defect, with the element it is about, so it
                      names a class or a property rather than a percentage

Every number here comes from something already measured. Nothing is estimated,
and a dimension with no input is reported as `unmeasured` rather than scored —
an absent signal must never average into a healthy-looking total, which is how
a corpus gets indexed against a grade-B ontology while the dashboard is green.

Weights are declared per stage and normalised over the dimensions that actually
have data, so a partial run is scored on what it measured rather than punished
for what it skipped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

SCHEMA_VERSION = 1

#: 대분류 — the four stages, in the order a run passes through them.
STAGES = ("ontology", "indexing", "retrieval", "generation")

_GRADE_BANDS = ((0.90, "A"), (0.80, "B"), (0.70, "C"), (0.60, "D"))


def _grade(score: Optional[float]) -> str:
    if score is None:
        return "-"
    for threshold, letter in _GRADE_BANDS:
        if score >= threshold:
            return letter
    return "F"


@dataclass
class Finding:
    """소분류 — one defect, named against the element it concerns."""

    severity: str            # major | minor | info
    message: str
    element: str = ""        # the class, property, or query it is about

    def to_dict(self) -> Dict[str, Any]:
        return {"severity": self.severity, "message": self.message,
                "element": self.element}


@dataclass
class Dimension:
    """중분류 — one measurable property of a stage.

    `score is None` means unmeasured, which is different from zero and is kept
    distinct all the way to the top: an absent signal is excluded from the
    average rather than counted as a failure.
    """

    name: str
    weight: float
    score: Optional[float] = None
    unit: str = ""
    observed: Optional[float] = None   # the raw number the score came from
    findings: List[Finding] = field(default_factory=list)

    @property
    def measured(self) -> bool:
        return self.score is not None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "weight": self.weight,
            "score": self.score,
            "grade": _grade(self.score),
            "measured": self.measured,
            "unit": self.unit,
            "observed": self.observed,
            "findings": [f.to_dict() for f in self.findings],
        }


@dataclass
class Stage:
    """대분류 — one of the four places a run can fail."""

    name: str
    dimensions: List[Dimension] = field(default_factory=list)

    @property
    def score(self) -> Optional[float]:
        """Weighted mean over MEASURED dimensions only.

        Renormalising over what was measured is the whole point. Treating an
        unmeasured dimension as zero would make an un-instrumented stage look
        broken; treating it as one would make it look healthy. Neither is true,
        so it is excluded and the count is reported alongside.
        """
        measured = [d for d in self.dimensions if d.measured]
        total_weight = sum(d.weight for d in measured)
        if not measured or total_weight <= 0:
            return None
        return sum((d.score or 0.0) * d.weight for d in measured) / total_weight

    def to_dict(self) -> Dict[str, Any]:
        measured = [d for d in self.dimensions if d.measured]
        return {
            "name": self.name,
            "score": self.score,
            "grade": _grade(self.score),
            "measured_dimensions": len(measured),
            "total_dimensions": len(self.dimensions),
            "dimensions": [d.to_dict() for d in self.dimensions],
        }


@dataclass
class WorkflowScorecard:
    """The whole run, descendable from one number to a named element."""

    stages: List[Stage] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    @property
    def score(self) -> Optional[float]:
        """Unweighted mean over scored stages.

        Deliberately unweighted. Any weighting across ontology, indexing,
        retrieval and generation would be a claim about which stage matters
        most, and that claim is exactly what a breakdown exists to let the
        reader make for themselves.
        """
        scored = [s.score for s in self.stages if s.score is not None]
        return sum(scored) / len(scored) if scored else None

    def stage(self, name: str) -> Optional[Stage]:
        return next((s for s in self.stages if s.name == name), None)

    def worst(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Findings ranked for a reader who has one minute.

        Major before minor, then by the stage's own score, so the top of the
        list is a real defect in the weakest stage rather than a minor note in
        a healthy one.
        """
        rows: List[Dict[str, Any]] = []
        order = {"major": 0, "minor": 1, "info": 2}
        for stage in self.stages:
            for dimension in stage.dimensions:
                for finding in dimension.findings:
                    rows.append({
                        "stage": stage.name,
                        "dimension": dimension.name,
                        "severity": finding.severity,
                        "element": finding.element,
                        "message": finding.message,
                        "_rank": (order.get(finding.severity, 3),
                                  stage.score if stage.score is not None else 0.0),
                    })
        rows.sort(key=lambda r: r["_rank"])
        for row in rows:
            row.pop("_rank")
        return rows[:limit]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "score": self.score,
            "grade": _grade(self.score),
            "stages": [s.to_dict() for s in self.stages],
            "worst": self.worst(),
        }

    def render(self) -> str:
        """A top-down text view: stage, then dimension, then finding."""
        lines = [f"workflow  grade={_grade(self.score)} "
                 f"score={'-' if self.score is None else f'{self.score:.2f}'}"]
        for stage in self.stages:
            head = ("unmeasured" if stage.score is None
                    else f"{stage.score:.2f} {_grade(stage.score)}")
            lines.append(f"  {stage.name:12s} {head}"
                         f"   ({stage.measured_dimensions_str()})")
            for dimension in stage.dimensions:
                value = "  -  " if not dimension.measured else f"{dimension.score:.2f}"
                observed = ("" if dimension.observed is None
                            else f"  [{dimension.observed:g}{dimension.unit}]")
                lines.append(f"      {value}  {dimension.name}{observed}")
                for finding in dimension.findings:
                    target = f"{finding.element}: " if finding.element else ""
                    lines.append(f"          [{finding.severity}] {target}{finding.message}")
        return "\n".join(lines)


def _measured_dimensions_str(self: Stage) -> str:
    return f"{len([d for d in self.dimensions if d.measured])}/{len(self.dimensions)} measured"


Stage.measured_dimensions_str = _measured_dimensions_str  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Builders — each takes evidence that already exists and refuses to invent any
# ---------------------------------------------------------------------------


def _ratio(good: int, total: int) -> Optional[float]:
    return None if total <= 0 else good / total


def build_ontology_stage(scorecard: Any = None, ontology: Any = None) -> Stage:
    """대분류 ontology — folds `score_ontology` in as one stage of four.

    The existing scorecard's dimensions become this stage's dimensions
    unchanged, so nothing is re-derived and the two views cannot disagree. The
    OS-contract gaps (ADR-0181) join them as a fifth dimension, because an
    ontology that scores well and declares no identity keys still cannot
    deduplicate an entity across two documents.
    """
    stage = Stage(name="ontology")

    if scorecard is not None:
        data = scorecard.to_dict() if hasattr(scorecard, "to_dict") else dict(scorecard)
        by_dimension: Dict[str, List[Finding]] = {}
        for weak in data.get("weak_points") or []:
            by_dimension.setdefault(str(weak.get("dimension") or ""), []).append(
                Finding(severity=str(weak.get("severity") or "info"),
                        message=str(weak.get("message") or ""),
                        element=str(weak.get("target") or "").strip("<>"))
            )
        for dimension in data.get("dimensions") or []:
            name = str(dimension.get("name") or "")
            stage.dimensions.append(Dimension(
                name=name,
                weight=float(dimension.get("weight") or 0.0),
                score=(float(dimension["score"])
                       if isinstance(dimension.get("score"), (int, float)) else None),
                findings=by_dimension.get(name, []),
            ))

    if ontology is not None:
        # Keet's modularisation metrics (§11.3) as a dimension of their own.
        # The existing scorecard measures the ontology as one artefact; this
        # measures how it is DIVIDED, which is what decides whether a module can
        # change without changing the rest. Scored on encapsulation because that
        # is the metric Keet marks large-is-good and the one independence is
        # defined from -- cohesion and relative size run the other way, and
        # averaging metrics with opposite directions produces a number that
        # means nothing.
        from .ontology_modularity import analyse as _analyse
        from .ontology_modularity import findings as _findings

        report = _analyse(ontology)
        if report.modules:
            encapsulation = sum(m.encapsulation for m in report.modules) / len(report.modules)
            stage.dimensions.append(Dimension(
                name="modularity",
                weight=0.15,
                score=encapsulation,
                unit=" modules",
                observed=float(len(report.modules)),
                findings=[Finding(f["severity"], f["message"], element=f["element"])
                          for f in _findings(report)],
            ))

        annotations = getattr(ontology, "annotations", None) or {}
        nodes = getattr(ontology, "nodes", None) or {}
        required = {
            "purpose": bool((getattr(ontology, "description", "") or "").strip()),
            "competency_questions": bool(annotations.get("competency_questions")),
            "modelling_decisions": bool(annotations.get("modelling_decisions")),
            "identity": any(getattr(n, "identity_keys", None) for n in nodes.values()),
            "vocabularies": bool(annotations.get("vocabularies")),
        }
        present = sum(1 for ok in required.values() if ok)
        stage.dimensions.append(Dimension(
            name="os_contract",
            weight=0.10,
            score=_ratio(present, len(required)),
            unit=" of 5",
            observed=float(present),
            findings=[
                Finding("minor", "not declared; see ADR-0181", element=element)
                for element, ok in sorted(required.items()) if not ok
            ],
        ))
    return stage


def build_indexing_stage(extraction_rows: Sequence[Dict[str, Any]],
                         allowed_labels: Optional[Sequence[str]] = None,
                         vocabularies: Optional[Dict[str, Sequence[str]]] = None) -> Stage:
    """대분류 indexing — computed from the extraction output, not from a model.

    Three dimensions, each corresponding to a failure a real 322-document run
    produced: documents that yielded no graph, nodes labelled outside the
    ontology, and property values outside a declared vocabulary.
    """
    stage = Stage(name="indexing")
    rows = list(extraction_rows)
    if not rows:
        return stage

    produced = [r for r in rows if (r.get("nodes") or [])]
    stage.dimensions.append(Dimension(
        name="extraction_yield", weight=0.4,
        score=_ratio(len(produced), len(rows)),
        unit=" docs", observed=float(len(produced)),
        findings=([Finding("major",
                           f"{len(rows) - len(produced)} of {len(rows)} documents "
                           f"produced no graph")]
                  if len(produced) < len(rows) else []),
    ))

    if allowed_labels is not None:
        allowed = set(allowed_labels)
        nodes = [n for r in rows for n in (r.get("nodes") or [])]
        off = [str(n.get("label") or "") for n in nodes
               if str(n.get("label") or "") not in allowed]
        stage.dimensions.append(Dimension(
            name="label_compliance", weight=0.3,
            score=_ratio(len(nodes) - len(off), len(nodes)),
            unit=" nodes", observed=float(len(nodes)),
            findings=[Finding("major", "label is not declared by the ontology",
                              element=label)
                      for label in sorted(set(off))[:5]],
        ))

    if vocabularies:
        folded = {k: {str(v).strip().lower() for v in vals}
                  for k, vals in vocabularies.items()}
        checked = off_vocab = 0
        offenders: Dict[str, set] = {}
        for row in rows:
            for node in row.get("nodes") or []:
                label = str(node.get("label") or "")
                for prop, value in (node.get("properties") or {}).items():
                    allowed = folded.get(f"{label}.{prop}")
                    if allowed is None or value in (None, ""):
                        continue
                    checked += 1
                    if str(value).strip().lower() not in allowed:
                        off_vocab += 1
                        offenders.setdefault(f"{label}.{prop}", set()).add(str(value))
        stage.dimensions.append(Dimension(
            name="vocabulary_compliance", weight=0.3,
            score=_ratio(checked - off_vocab, checked),
            unit=" values", observed=float(checked),
            findings=[
                Finding("major",
                        f"{len(values)} value(s) outside the declared set, "
                        f"e.g. {', '.join(sorted(values)[:3])}",
                        element=key)
                for key, values in sorted(offenders.items())
            ],
        ))
    return stage


def build_retrieval_stage(plan_summaries: Sequence[Dict[str, Any]]) -> Stage:
    """대분류 retrieval — from EXPLAIN/PROFILE summaries (`query.plan_quality`).

    Sargability is the dimension worth grading rather than latency: two queries
    returning the same answer measured 25 db hits and 6.6M at SF1000 while
    sitting 4 ms apart at SF1, so wall-clock cannot see the difference until the
    graph has already grown.
    """
    stage = Stage(name="retrieval")
    summaries = [s for s in plan_summaries if s.get("available")]
    if not summaries:
        return stage

    sargable = [s for s in summaries if s.get("sargable")]
    scanning = [s for s in summaries if s.get("scans")]
    stage.dimensions.append(Dimension(
        name="sargability", weight=0.6,
        score=_ratio(len(sargable), len(summaries)),
        unit=" plans", observed=float(len(summaries)),
        findings=[Finding("major",
                          f"{len(scanning)} of {len(summaries)} plans contain a scan; "
                          "their cost grows with the graph")]
        if scanning else [],
    ))

    with_hits = [s for s in summaries if isinstance(s.get("db_hits"), (int, float))]
    if with_hits:
        hits = sorted(float(s["db_hits"]) for s in with_hits)
        worst = hits[-1]
        median = hits[len(hits) // 2]
        # The tail, not the median. A median db-hit count stays flat while one
        # query in fifty explodes, and that one is the outage.
        stage.dimensions.append(Dimension(
            name="db_hit_tail", weight=0.4,
            score=None if median <= 0 else max(0.0, min(1.0, 1.0 - (worst / (median * 100)))),
            unit=" db hits (max)", observed=worst,
            findings=[Finding("minor",
                              f"worst plan cost {worst:g} db hits against a median "
                              f"of {median:g}")]
            if median > 0 and worst > median * 10 else [],
        ))
    return stage


def build_generation_stage(answer_rows: Sequence[Dict[str, Any]]) -> Stage:
    """대분류 generation — grounding and refusal correctness.

    `refusal_correctness` exists because the measured failure is bidirectional:
    the graph form refused questions it could answer (0 of 4 on synthetic
    negation) while refusing correctly on genuinely unanswerable ones (20 of 20
    on ERB). One number for "refused" would hide both.
    """
    stage = Stage(name="generation")
    rows = list(answer_rows)
    if not rows:
        return stage

    graded = [r for r in rows if r.get("correct") is not None]
    if graded:
        correct = [r for r in graded if r.get("correct")]
        stage.dimensions.append(Dimension(
            name="answer_accuracy", weight=0.5,
            score=_ratio(len(correct), len(graded)),
            unit=" answers", observed=float(len(graded)),
            findings=[Finding("major",
                              f"{len(graded) - len(correct)} of {len(graded)} answers wrong")]
            if len(correct) < len(graded) else [],
        ))

    decidable = [r for r in rows if r.get("should_refuse") is not None]
    if decidable:
        right = [r for r in decidable
                 if bool(r.get("refused")) == bool(r.get("should_refuse"))]
        over = [r for r in decidable
                if r.get("refused") and not r.get("should_refuse")]
        under = [r for r in decidable
                 if not r.get("refused") and r.get("should_refuse")]
        findings = []
        if over:
            findings.append(Finding("major",
                                    f"{len(over)} answerable question(s) refused"))
        if under:
            findings.append(Finding("major",
                                    f"{len(under)} unanswerable question(s) answered anyway"))
        stage.dimensions.append(Dimension(
            name="refusal_correctness", weight=0.5,
            score=_ratio(len(right), len(decidable)),
            unit=" decisions", observed=float(len(decidable)),
            findings=findings,
        ))
    return stage


def build_workflow_scorecard(*stages: Stage) -> WorkflowScorecard:
    """Assemble in pipeline order, keeping unmeasured stages visible.

    A stage with no dimensions is still listed. Dropping it would make a run
    that never touched retrieval indistinguishable from one whose retrieval was
    fine, and telling those apart is the reason for a breakdown.
    """
    supplied = {s.name: s for s in stages}
    return WorkflowScorecard(
        stages=[supplied.get(name, Stage(name=name)) for name in STAGES]
    )
