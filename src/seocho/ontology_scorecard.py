"""Graded, multi-dimensional quality scorecard for an ontology artifact (TBox).

This is the *measurement instrument* for ontology engineering, and it is
deliberately distinct from the two adjacent surfaces:

- :func:`seocho.ontology_governance.build_ontology_governance_report` is a
  binary *promotion gate* ("is this ontology safe to ship?"). It bundles
  check + context + artifact draft + SHACL + synthetic-sample validation.
- :meth:`seocho.ontology.Ontology.score_extraction` scores an *extracted graph*
  (ABox instances) against the ontology — instance-level quality, not the
  schema's own quality.

The scorecard answers a different question: **how good is this ontology, and
where is it weak?** It produces a 0.0-1.0 score per dimension, a letter grade,
and an explicit, sorted list of *weak points* (concrete targets to fix). That
makes it the foundation for the iterative refinement loop (axiom adjustment,
taxonomy design) and for deciding whether a new ontology version is actually an
improvement.

Pure model walk: offline, zero hot-path, no LLM, no corpus required for the
structural tiers (ADR-0043 / ADR-0114). Existing governance functions
(:func:`lint_ontology`, :func:`competency_question_report`) are *composed*, not
duplicated; the genuinely new contribution is the taxonomy-health tier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from statistics import mean
from typing import Any, Dict, List, Optional, Sequence, Union

from .ontology import Ontology
from .ontology_governance import (
    competency_question_coverage,
    competency_question_report,
    lint_ontology,
)

# Default dimension weights. functional_coverage / corpus_coverage are only
# counted when their inputs are supplied (see ``score_ontology``); absent
# dimensions are dropped and the remaining weights renormalised.
DEFAULT_WEIGHTS: Dict[str, float] = {
    "structural_integrity": 0.30,
    "taxonomy_health": 0.25,
    "definitional_completeness": 0.20,
    "constraint_richness": 0.15,
    "functional_coverage": 0.10,
    "corpus_coverage": 0.0,
}

# Purpose-specific weight profiles. The FinDER guardrail ablation (ADR-0115)
# showed the intrinsic ``balanced`` grade can DIVERGE from downstream guardrail
# value: a flat-but-rich ontology graded below a sparse one yet was the better
# extraction guardrail. The fix is to weight by intended use. ``guardrail``
# de-emphasises taxonomy shape and leans on constraint richness + how well the
# ontology covers what the target corpus actually needs (corpus_coverage);
# ``taxonomy`` (reasoning/subsumption use) does the opposite.
WEIGHT_PROFILES: Dict[str, Dict[str, float]] = {
    "balanced": dict(DEFAULT_WEIGHTS),
    "guardrail": {
        "structural_integrity": 0.15,
        "taxonomy_health": 0.10,
        "definitional_completeness": 0.20,
        "constraint_richness": 0.25,
        "functional_coverage": 0.05,
        "corpus_coverage": 0.25,
    },
    "taxonomy": {
        "structural_integrity": 0.25,
        "taxonomy_health": 0.35,
        "definitional_completeness": 0.20,
        "constraint_richness": 0.10,
        "functional_coverage": 0.10,
        "corpus_coverage": 0.0,
    },
}


@dataclass(slots=True)
class CorpusProfile:
    """A summary of the entity types a target corpus actually needs, computed
    upstream (so the scorecard stays offline). The canonical way to build one is
    an OPEN, ontology-free extraction over the corpus (``build_corpus_profile``):
    the resulting label frequencies are *independent of any candidate ontology*
    and represent what that corpus demands. Scoring a candidate ontology's
    coverage of this profile is what the FinDER ablation showed actually
    predicts downstream guardrail value."""

    label_frequencies: Dict[str, int] = field(default_factory=dict)
    doc_count: int = 0
    source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"label_frequencies": dict(self.label_frequencies),
                "doc_count": self.doc_count, "source": self.source}


def build_corpus_profile(graphs: Sequence[Dict[str, Any]], *, source: str = "") -> CorpusProfile:
    """Build a :class:`CorpusProfile` from extracted graphs. Pass graphs from an
    OPEN (ontology-free) extraction so the label set reflects the corpus's needs,
    not a guardrail's vocabulary. Each graph is ``{"nodes": [{"label": ...}]}``."""
    freqs: Dict[str, int] = {}
    for g in graphs:
        for node in (g or {}).get("nodes", []):
            if not isinstance(node, dict):
                continue
            label = str(node.get("label", "")).strip()
            if label:
                freqs[label] = freqs.get(label, 0) + 1
    return CorpusProfile(label_frequencies=freqs, doc_count=len(graphs), source=source)


@dataclass(slots=True)
class DimensionScore:
    """One scored dimension of an ontology's quality.

    ``score`` is in [0.0, 1.0]. ``findings`` are human-readable, actionable
    observations. ``stats`` carries the raw metrics the score was derived from
    so callers can build their own thresholds or dashboards.
    """

    name: str
    score: float
    weight: float
    findings: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "score": round(self.score, 4),
            "weight": self.weight,
            "findings": list(self.findings),
            "stats": dict(self.stats),
        }


@dataclass(slots=True)
class WeakPoint:
    """A concrete, fixable defect surfaced by the scorecard.

    ``severity`` is ``"blocking"`` (a structural error that should block
    promotion), ``"major"`` or ``"minor"``. ``target`` names the schema element
    (class label, relationship type, or ``"<ontology>"`` for global findings).
    """

    severity: str
    dimension: str
    target: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity,
            "dimension": self.dimension,
            "target": self.target,
            "message": self.message,
        }


@dataclass(slots=True)
class OntologyScorecard:
    ontology_name: str
    package_id: str
    version: str
    overall_score: float
    grade: str
    blocking: bool
    dimensions: List[DimensionScore]
    weak_points: List[WeakPoint]
    stats: Dict[str, Any]

    def dimension(self, name: str) -> Optional[DimensionScore]:
        for dim in self.dimensions:
            if dim.name == name:
                return dim
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ontology_name": self.ontology_name,
            "package_id": self.package_id,
            "version": self.version,
            "overall_score": round(self.overall_score, 4),
            "grade": self.grade,
            "blocking": self.blocking,
            "dimensions": [d.to_dict() for d in self.dimensions],
            "weak_points": [w.to_dict() for w in self.weak_points],
            "stats": dict(self.stats),
        }


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


def _letter_grade(score: float, *, blocking: bool) -> str:
    """Map an overall score to a letter grade. A blocking structural error caps
    the grade at ``D`` — a quantitatively pretty ontology that won't even pass
    the hygiene linter must not read as shippable."""
    if blocking:
        return "D" if score >= 0.6 else "F"
    if score >= 0.9:
        return "A"
    if score >= 0.8:
        return "B"
    if score >= 0.7:
        return "C"
    if score >= 0.6:
        return "D"
    return "F"


# ---------------------------------------------------------------------------
# Dimension scorers (each pure; each returns a DimensionScore + WeakPoints)
# ---------------------------------------------------------------------------


def _score_structural_integrity(
    ontology: Ontology, weight: float
) -> tuple[DimensionScore, List[WeakPoint]]:
    """Reuse the FIBO/ISO-704 hygiene linter. ERRORS are blocking and dominate;
    WARNINGS are soft quality deductions."""
    lint = lint_ontology(ontology)
    errors = lint["errors"]
    warnings = lint["warnings"]

    # Errors are structural defects: a single one should sink the dimension.
    # Warnings erode it gently (0.04 each, capped).
    score = 1.0
    score -= min(1.0, 0.34 * len(errors))
    score -= min(0.4, 0.04 * len(warnings))
    score = max(0.0, score)

    weak: List[WeakPoint] = []
    for f in errors:
        weak.append(
            WeakPoint("blocking", "structural_integrity", f.get("target", "<ontology>"), f["message"])
        )
    for f in warnings:
        weak.append(
            WeakPoint("minor", "structural_integrity", f.get("target", "<ontology>"), f["message"])
        )

    findings: List[str] = []
    if errors:
        findings.append(f"{len(errors)} structural error(s) — blocks promotion until fixed.")
    if warnings:
        findings.append(f"{len(warnings)} hygiene warning(s) (naming, missing definitions, dangling endpoints).")
    if not errors and not warnings:
        findings.append("Clean: no hygiene errors or warnings.")

    dim = DimensionScore(
        name="structural_integrity",
        score=score,
        weight=weight,
        findings=findings,
        stats={"error_count": len(errors), "warning_count": len(warnings)},
    )
    return dim, weak


def _broader_depths(ontology: Ontology) -> Dict[str, int]:
    """Depth of each class in the ``broader`` (subClassOf) forest. Root = 0.
    Cycle-safe (a node already on the current path contributes no further
    depth); lint flags cycles separately."""
    node_labels = set(ontology.nodes)
    broader_map = {
        label: [p for p in (getattr(nd, "broader", []) or []) if p in node_labels]
        for label, nd in ontology.nodes.items()
    }
    memo: Dict[str, int] = {}

    def depth(label: str, on_path: frozenset) -> int:
        if label in memo:
            return memo[label]
        parents = broader_map.get(label, [])
        safe_parents = [p for p in parents if p not in on_path]
        if not safe_parents:
            result = 0
        else:
            result = 1 + max(depth(p, on_path | {label}) for p in safe_parents)
            memo[label] = result
        return result

    return {label: depth(label, frozenset()) for label in node_labels}


def _abstract_classes(ontology: Ontology) -> set:
    """Classes that are pure classificatory superclasses: they are the
    ``broader`` parent of at least one other class AND declare no properties of
    their own. Following OWL/OntoClean practice, such classes are never directly
    instantiated, so they are legitimately exempt from the identity requirement
    and from competency-question element coverage."""
    node_labels = set(ontology.nodes)
    is_parent: set = set()
    for nd in ontology.nodes.values():
        for parent in (getattr(nd, "broader", []) or []):
            if parent in node_labels:
                is_parent.add(parent)
    return {
        label for label in is_parent
        if not ontology.nodes[label].properties
    }


def _relationship_endpoints(ontology: Ontology) -> set:
    """All class labels that appear as a (non-``Any``) relationship endpoint."""
    endpoints: set = set()
    for rd in ontology.relationships.values():
        for role in ("source", "target"):
            value = str(getattr(rd, role, "Any") or "Any")
            if value != "Any":
                endpoints.add(value)
    return endpoints


def _score_taxonomy_health(
    ontology: Ontology, weight: float, ontoclean_tags: Optional[Dict[str, Any]] = None
) -> tuple[DimensionScore, List[WeakPoint]]:
    """NEW tier: is the class structure a connected, navigable taxonomy, or a
    flat bag of disconnected labels? Surfaces the modeling smells an ontology
    engineer cares about — orphans, flatness, degenerate single-child chains.

    When ``ontoclean_tags`` (precomputed OntoClean meta-properties) are supplied,
    is-a edges are additionally checked against the OntoClean subsumption
    constraints (rigidity, identity, unity, dependence) and any violation is a
    major weak point that penalises the dimension. No LLM is ever called here —
    tags must be computed upstream (see ``ontology_ontoclean.infer_metaproperties``)."""
    node_labels = list(ontology.nodes)
    n = len(node_labels)
    weak: List[WeakPoint] = []
    findings: List[str] = []

    if n == 0:
        dim = DimensionScore("taxonomy_health", 0.0, weight, ["Ontology defines no classes."], {"class_count": 0})
        weak.append(WeakPoint("blocking", "taxonomy_health", "<ontology>", "Ontology defines no classes."))
        return dim, weak

    broader_map = {
        label: [p for p in (getattr(nd, "broader", []) or []) if p in set(node_labels)]
        for label, nd in ontology.nodes.items()
    }
    has_parent = {label for label, parents in broader_map.items() if parents}
    is_parent = {p for parents in broader_map.values() for p in parents}
    endpoints = _relationship_endpoints(ontology)

    # An orphan is a class connected to NOTHING: no parent, no child, and not a
    # relationship endpoint. It is a floating concept the rest of the model can
    # never reach — the clearest taxonomy defect.
    orphans = [
        label
        for label in node_labels
        if label not in has_parent and label not in is_parent and label not in endpoints
    ]
    orphan_ratio = len(orphans) / n

    depths = _broader_depths(ontology)
    max_depth = max(depths.values()) if depths else 0

    # Single-child parents: a parent class with exactly one declared child is a
    # degenerate branch (usually an unfinished taxonomy or a needless layer).
    child_counts: Dict[str, int] = {}
    for parents in broader_map.values():
        for p in parents:
            child_counts[p] = child_counts.get(p, 0) + 1
    single_child_parents = [p for p, c in child_counts.items() if c == 1]

    score = 1.0
    # Orphans dominate: a disconnected concept is the canonical taxonomy defect.
    score -= orphan_ratio
    if orphans:
        findings.append(f"{len(orphans)}/{n} class(es) are disconnected (no parent, child, or relationship).")
        for label in orphans:
            weak.append(
                WeakPoint(
                    "major", "taxonomy_health", label,
                    f"Class '{label}' is disconnected — give it a 'broader' parent or a relationship, or remove it.",
                )
            )

    # Flatness: a sizable class set with no subclassing at all is a vocabulary,
    # not a taxonomy. The user explicitly wants taxonomy design, so flag it.
    if n >= 6 and not has_parent:
        score -= 0.2
        findings.append(f"Flat: {n} classes with zero subclass (broader) edges — no taxonomy structure to reason over.")
        weak.append(
            WeakPoint(
                "major", "taxonomy_health", "<ontology>",
                f"{n} classes but no 'broader' hierarchy — consider an is-a taxonomy to enable subsumption.",
            )
        )

    if single_child_parents:
        score -= min(0.15, 0.05 * len(single_child_parents))
        findings.append(f"{len(single_child_parents)} parent class(es) have a single child (degenerate branch).")
        for p in single_child_parents:
            weak.append(
                WeakPoint(
                    "minor", "taxonomy_health", p,
                    f"Class '{p}' has exactly one subclass — a single-child branch is usually under-modeled or redundant.",
                )
            )

    # OntoClean subsumption constraints (only when meta-property tags are
    # supplied upstream; this function never calls an LLM).
    ontoclean_violations = 0
    ontoclean_hard = 0
    if ontoclean_tags:
        from .ontology_ontoclean import check_ontoclean

        oc = check_ontoclean(ontology, ontoclean_tags)
        ontoclean_violations = len(oc.violations)
        for v in oc.violations:
            sev = "major" if v.severity == "violation" else "minor"
            if v.severity == "violation":
                ontoclean_hard += 1
            weak.append(WeakPoint(sev, "taxonomy_health", f"{v.child}<:{v.parent}", v.message))
        if ontoclean_hard:
            # Each formally-wrong is-a edge is a hard taxonomy defect.
            score -= min(0.5, 0.2 * ontoclean_hard)
            findings.append(f"{ontoclean_hard} OntoClean subsumption violation(s) on is-a edges.")

    score = max(0.0, score)
    if not findings:
        findings.append(f"Connected taxonomy: depth {max_depth}, {len(has_parent)}/{n} classes placed under a parent.")

    dim = DimensionScore(
        name="taxonomy_health",
        score=score,
        weight=weight,
        findings=findings,
        stats={
            "class_count": n,
            "rooted_count": len(has_parent),
            "orphan_count": len(orphans),
            "orphans": sorted(orphans),
            "max_depth": max_depth,
            "single_child_parents": sorted(single_child_parents),
            "ontoclean_violations": ontoclean_violations,
            "ontoclean_hard_violations": ontoclean_hard,
        },
    )
    return dim, weak


def _score_definitional_completeness(
    ontology: Ontology, weight: float
) -> tuple[DimensionScore, List[WeakPoint]]:
    """The 'is anything under-specified?' tier — the signal the user described as
    'unclear things'. Measures coverage of definitions, identities, and
    constrained properties."""
    weak: List[WeakPoint] = []
    findings: List[str] = []

    classes = ontology.nodes
    rels = ontology.relationships
    n_classes = len(classes)
    abstract = _abstract_classes(ontology)
    # Concrete (directly instantiable) classes are the denominator for identity:
    # abstract superclasses are never instantiated, so identity does not apply.
    concrete = [l for l in classes if l not in abstract]

    # 1. classes with a definition
    classes_with_def = [l for l, nd in classes.items() if str(getattr(nd, "description", "") or "").strip()]
    # 2. relationships with a definition
    rels_with_def = [r for r, rd in rels.items() if str(getattr(rd, "description", "") or "").strip()]
    # 3. concrete classes with a resolvable identity (effective_identity_keys non-empty)
    concrete_with_identity = [l for l in concrete if classes[l].effective_identity_keys]
    # 4. properties that are not bare typed slots (have a description OR a constraint)
    total_props = 0
    specified_props = 0
    for nd in classes.values():
        for pname, p in nd.properties.items():
            total_props += 1
            if str(getattr(p, "description", "") or "").strip() or p.unique or p.required or p.index:
                specified_props += 1

    def_ratio = len(classes_with_def) / n_classes if n_classes else 1.0
    rel_def_ratio = len(rels_with_def) / len(rels) if rels else 1.0
    identity_ratio = len(concrete_with_identity) / len(concrete) if concrete else 1.0
    prop_spec_ratio = specified_props / total_props if total_props else 1.0

    score = mean([def_ratio, rel_def_ratio, identity_ratio, prop_spec_ratio])

    classes_without_identity = [l for l in concrete if not classes[l].effective_identity_keys]
    for label in classes_without_identity:
        weak.append(
            WeakPoint(
                "major", "definitional_completeness", label,
                f"Class '{label}' has no identity (no identity_keys and no unique property) — instances cannot be deduplicated across documents.",
            )
        )
    if identity_ratio < 1.0:
        findings.append(f"{len(classes_without_identity)}/{len(concrete)} concrete class(es) lack a resolvable identity key.")
    if def_ratio < 1.0:
        findings.append(f"{n_classes - len(classes_with_def)}/{n_classes} class(es) have no definition.")
    if prop_spec_ratio < 1.0:
        findings.append(f"{total_props - specified_props}/{total_props} propertie(s) are bare typed slots (no description, no constraint).")
    if not findings:
        findings.append("Fully specified: every class is defined, identified, and its properties are constrained or documented.")

    dim = DimensionScore(
        name="definitional_completeness",
        score=score,
        weight=weight,
        findings=findings,
        stats={
            "class_definition_ratio": round(def_ratio, 4),
            "relationship_definition_ratio": round(rel_def_ratio, 4),
            "identity_ratio": round(identity_ratio, 4),
            "property_specification_ratio": round(prop_spec_ratio, 4),
            "classes_without_identity": sorted(classes_without_identity),
            "abstract_classes": sorted(abstract),
        },
    )
    return dim, weak


def _score_constraint_richness(
    ontology: Ontology, weight: float
) -> tuple[DimensionScore, List[WeakPoint]]:
    """Does the ontology actually *constrain* extraction, or is it a loose
    vocabulary? Measures typed relationship endpoints, declared cardinality, and
    per-class constraints. A schema that constrains nothing cannot be trusted to
    guide extraction — directly relevant to 'does it actually work?'."""
    weak: List[WeakPoint] = []
    findings: List[str] = []

    rels = ontology.relationships
    classes = ontology.nodes

    typed_endpoints = 0
    tight_cardinality = 0
    for rtype, rd in rels.items():
        src = str(getattr(rd, "source", "Any") or "Any")
        tgt = str(getattr(rd, "target", "Any") or "Any")
        if src != "Any" and tgt != "Any":
            typed_endpoints += 1
        else:
            weak.append(
                WeakPoint(
                    "minor", "constraint_richness", rtype,
                    f"Relationship '{rtype}' has an untyped endpoint (source/target = Any) — it constrains no traversal.",
                )
            )
        if str(getattr(rd, "cardinality", "MANY_TO_MANY")) != "MANY_TO_MANY":
            tight_cardinality += 1

    classes_with_constraint = [
        l for l, nd in classes.items()
        if nd.unique_properties or nd.required_properties or nd.identity_keys
    ]

    endpoint_ratio = typed_endpoints / len(rels) if rels else 1.0
    cardinality_ratio = tight_cardinality / len(rels) if rels else 1.0
    class_constraint_ratio = len(classes_with_constraint) / len(classes) if classes else 1.0

    # Endpoints and per-class constraints matter most; cardinality is a bonus
    # (MANY_TO_MANY is often legitimately correct, so it is weighted lightly).
    score = 0.45 * endpoint_ratio + 0.4 * class_constraint_ratio + 0.15 * cardinality_ratio

    if endpoint_ratio < 1.0:
        findings.append(f"{len(rels) - typed_endpoints}/{len(rels)} relationship(s) have an untyped (Any) endpoint.")
    if class_constraint_ratio < 1.0:
        findings.append(f"{len(classes) - len(classes_with_constraint)}/{len(classes)} class(es) carry no constraint (unique/required/identity).")
    if rels and cardinality_ratio == 0.0:
        findings.append("No relationship declares a tighter-than-MANY_TO_MANY cardinality.")
    if not findings:
        findings.append("Well-constrained: typed endpoints and per-class constraints throughout.")

    dim = DimensionScore(
        name="constraint_richness",
        score=score,
        weight=weight,
        findings=findings,
        stats={
            "typed_endpoint_ratio": round(endpoint_ratio, 4),
            "tight_cardinality_ratio": round(cardinality_ratio, 4),
            "class_constraint_ratio": round(class_constraint_ratio, 4),
        },
    )
    return dim, weak


def _score_functional_coverage(
    ontology: Ontology,
    competency_questions: Sequence[Any],
    weight: float,
) -> tuple[DimensionScore, List[WeakPoint]]:
    """Functional tier (Grüninger & Fox / Kendall & McGuinness): can the
    ontology's vocabulary actually express the questions it is meant to answer?
    Composes :func:`competency_question_report` (dict CQs with ``requires``) or
    :func:`competency_question_coverage` (plain-string CQs)."""
    weak: List[WeakPoint] = []
    findings: List[str] = []
    abstract = _abstract_classes(ontology)

    def _adjusted_coverage(coverage: Dict[str, Any]) -> tuple[float, List[str]]:
        """Element-coverage ratio with abstract superclasses removed from both
        the uncovered set and the denominator — they need not be named directly
        by a competency question."""
        uncovered = [e for e in coverage["uncovered_elements"] if e not in abstract]
        total = coverage["total_elements"] - len([a for a in abstract])
        if total <= 0:
            return 1.0, uncovered
        return (total - len(uncovered)) / total, uncovered

    dict_cqs = [cq for cq in competency_questions if isinstance(cq, dict)]
    if dict_cqs:
        report = competency_question_report(ontology, dict_cqs)
        expressible_ratio = report["expressible_ratio"]
        coverage_ratio, _ = _adjusted_coverage(report["coverage"])
        score = mean([expressible_ratio, coverage_ratio])
        for cq in report["questions"]:
            if not cq["expressible"]:
                weak.append(
                    WeakPoint(
                        "major", "functional_coverage", str(cq.get("id") or cq.get("requires")),
                        f"Competency question is structurally impossible — missing: {cq['missing_elements']}.",
                    )
                )
        if report["schema_impossible_count"]:
            findings.append(f"{report['schema_impossible_count']}/{report['question_count']} competency question(s) are structurally impossible.")
        stats = {
            "expressible_ratio": round(expressible_ratio, 4),
            "element_coverage_ratio": round(coverage_ratio, 4),
            "question_count": report["question_count"],
        }
    else:
        texts = [str(cq) for cq in competency_questions]
        coverage = competency_question_coverage(ontology, texts)
        coverage_ratio, uncovered_concrete = _adjusted_coverage(coverage)
        score = coverage_ratio
        coverage = {**coverage, "uncovered_elements": uncovered_concrete}
        for label in coverage["uncovered_elements"]:
            weak.append(
                WeakPoint(
                    "minor", "functional_coverage", label,
                    f"Schema element '{label}' is exercised by no competency question (candidate dead schema or missing CQ).",
                )
            )
        stats = {
            "element_coverage_ratio": round(coverage_ratio, 4),
            "uncovered_elements": coverage["uncovered_elements"],
            "question_count": coverage["question_count"],
        }
        if coverage["uncovered_elements"]:
            findings.append(f"{len(coverage['uncovered_elements'])} schema element(s) exercised by no competency question.")

    if not findings:
        findings.append("Every competency question is expressible and every element is exercised.")

    dim = DimensionScore("functional_coverage", score, weight, findings, stats)
    return dim, weak


def _score_corpus_coverage(
    ontology: Ontology, profile: CorpusProfile, weight: float
) -> tuple[DimensionScore, List[WeakPoint]]:
    """Corpus-aware tier: does the ontology's vocabulary cover the entity types
    the TARGET CORPUS actually needs? Score = frequency-weighted fraction of the
    corpus's observed labels that the ontology declares (as a label or alias,
    case-insensitively). This is the metric the FinDER ablation showed predicts
    downstream guardrail value, where intrinsic structure did not: a sparse
    ontology scores LOW here because the corpus mentions entities it cannot
    represent. The biggest uncovered labels become weak points — the precise
    classes to add."""
    weak: List[WeakPoint] = []

    # membership set: labels + aliases, casefolded (also a spaced form)
    members = set()
    for label, nd in ontology.nodes.items():
        members.add(label.casefold())
        members.add(re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", label).replace("_", " ").casefold())
        for alias in (getattr(nd, "aliases", []) or []):
            members.add(str(alias).strip().casefold())

    freqs = profile.label_frequencies
    total = sum(freqs.values())
    if total == 0:
        dim = DimensionScore("corpus_coverage", 1.0, weight,
                             ["Empty corpus profile — corpus coverage not assessed."],
                             {"covered_mass": 0, "total_mass": 0})
        return dim, weak

    covered_mass = 0
    uncovered: List[tuple] = []
    for label, count in freqs.items():
        forms = {label.casefold(),
                 re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", label).replace("_", " ").casefold()}
        if forms & members:
            covered_mass += count
        else:
            uncovered.append((label, count))

    score = covered_mass / total
    uncovered.sort(key=lambda kv: -kv[1])
    for label, count in uncovered[:10]:
        share = count / total
        sev = "major" if share >= 0.05 else "minor"
        weak.append(WeakPoint(
            sev, "corpus_coverage", label,
            f"Corpus mentions '{label}' {count}× ({share:.0%} of entities) but the ontology has no "
            f"matching class — add it (or an alias) so the guardrail can represent it.",
        ))

    findings = [f"Covers {score:.0%} of corpus entity mentions "
                f"({len(freqs) - len(uncovered)}/{len(freqs)} distinct labels)."]
    if uncovered:
        findings.append(f"Top uncovered: {', '.join(l for l, _ in uncovered[:5])}.")

    dim = DimensionScore(
        "corpus_coverage", score, weight, findings,
        {"covered_mass": covered_mass, "total_mass": total,
         "distinct_labels_covered": len(freqs) - len(uncovered),
         "distinct_labels_total": len(freqs),
         "top_uncovered": [{"label": l, "count": c} for l, c in uncovered[:10]],
         "corpus_doc_count": profile.doc_count},
    )
    return dim, weak


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def score_ontology(
    ontology: Ontology,
    *,
    competency_questions: Optional[Sequence[Union[str, Dict[str, Any]]]] = None,
    ontoclean_tags: Optional[Dict[str, Any]] = None,
    corpus_profile: Optional[CorpusProfile] = None,
    profile: str = "balanced",
    weights: Optional[Dict[str, float]] = None,
) -> OntologyScorecard:
    """Compute a graded, multi-dimensional quality scorecard for an ontology.

    Parameters
    ----------
    ontology:
        The ontology (TBox) to evaluate.
    competency_questions:
        Optional competency questions. Each item may be a plain string (matched
        against the coverage lint) or a dict with a ``requires`` list of element
        labels (matched against per-CQ expressibility). When omitted, the
        ``functional_coverage`` dimension is skipped (not penalised) and a weak
        point notes that functional validation could not be performed.
    ontoclean_tags:
        Optional precomputed OntoClean meta-properties (``{label:
        MetaProperties}``, e.g. from
        ``ontology_ontoclean.infer_metaproperties``). When supplied, is-a edges
        are checked against the OntoClean subsumption constraints and violations
        fold into ``taxonomy_health``. No LLM is called here.
    corpus_profile:
        Optional :class:`CorpusProfile` (from :func:`build_corpus_profile` over
        an OPEN extraction of the target corpus). When supplied, adds the
        ``corpus_coverage`` dimension — how well the ontology's vocabulary covers
        the entity types the corpus actually needs. This is what predicts
        downstream guardrail value (ADR-0115/0116).
    profile:
        Named weight profile — ``"balanced"`` (default), ``"guardrail"`` (weights
        constraint_richness + corpus_coverage for extraction-guardrail use), or
        ``"taxonomy"`` (weights taxonomy_health for reasoning use). See
        :data:`WEIGHT_PROFILES`. Ignored when ``weights`` is given.
    weights:
        Explicit override of the resolved profile weights. Dimensions absent from
        the run (e.g. functional_coverage with no CQs, corpus_coverage with no
        corpus) are dropped and the remaining weights renormalised.

    Returns
    -------
    OntologyScorecard
        Overall score (weighted mean of present dimensions), letter grade, a
        ``blocking`` flag (set when the hygiene linter reports a structural
        error), per-dimension breakdowns, and a severity-sorted list of weak
        points to drive the refinement loop.
    """
    if weights is not None:
        w = dict(weights)
    else:
        w = dict(WEIGHT_PROFILES.get(profile, DEFAULT_WEIGHTS))
    dimensions: List[DimensionScore] = []
    weak_points: List[WeakPoint] = []

    structural, weak = _score_structural_integrity(ontology, w.get("structural_integrity", 0.0))
    dimensions.append(structural)
    weak_points.extend(weak)

    taxonomy, weak = _score_taxonomy_health(ontology, w.get("taxonomy_health", 0.0), ontoclean_tags)
    dimensions.append(taxonomy)
    weak_points.extend(weak)

    definitional, weak = _score_definitional_completeness(ontology, w.get("definitional_completeness", 0.0))
    dimensions.append(definitional)
    weak_points.extend(weak)

    constraint, weak = _score_constraint_richness(ontology, w.get("constraint_richness", 0.0))
    dimensions.append(constraint)
    weak_points.extend(weak)

    if competency_questions:
        functional, weak = _score_functional_coverage(
            ontology, competency_questions, w.get("functional_coverage", 0.0)
        )
        dimensions.append(functional)
        weak_points.extend(weak)
    else:
        weak_points.append(
            WeakPoint(
                "minor", "functional_coverage", "<ontology>",
                "No competency questions supplied — functional (task-fit) validation was skipped.",
            )
        )

    if corpus_profile is not None:
        corpus, weak = _score_corpus_coverage(ontology, corpus_profile, w.get("corpus_coverage", 0.0))
        dimensions.append(corpus)
        weak_points.extend(weak)
    else:
        weak_points.append(
            WeakPoint(
                "minor", "corpus_coverage", "<ontology>",
                "No corpus profile supplied — corpus-coverage (does the vocabulary fit the target "
                "documents?) was skipped. This is the signal that predicts guardrail value.",
            )
        )

    # Blocking iff the hygiene linter found a structural error.
    blocking = any(wp.severity == "blocking" for wp in weak_points)

    total_weight = sum(d.weight for d in dimensions)
    if total_weight > 0:
        overall = sum(d.score * d.weight for d in dimensions) / total_weight
    else:
        overall = mean([d.score for d in dimensions]) if dimensions else 0.0

    grade = _letter_grade(overall, blocking=blocking)

    severity_rank = {"blocking": 0, "major": 1, "minor": 2}
    weak_points.sort(key=lambda wp: (severity_rank.get(wp.severity, 3), wp.dimension, wp.target))

    return OntologyScorecard(
        ontology_name=ontology.name,
        package_id=ontology.package_id,
        version=ontology.version,
        overall_score=overall,
        grade=grade,
        blocking=blocking,
        dimensions=dimensions,
        weak_points=weak_points,
        stats={
            "node_count": len(ontology.nodes),
            "relationship_count": len(ontology.relationships),
            "schema_fingerprint": ontology.schema_fingerprint(),
            "competency_questions_supplied": bool(competency_questions),
            "corpus_profile_supplied": corpus_profile is not None,
            "weight_profile": "custom" if weights is not None else profile,
        },
    )
