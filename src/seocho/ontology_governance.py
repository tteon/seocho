from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import unquote, urlparse

from .ontology import Ontology

# Naming conventions: classes UpperCamelCase; LPG relationship types UPPER_SNAKE
# (SEOCHO convention, not FIBO's lowerCamelCase object-property style).
_PASCAL_CASE = re.compile(r"^[A-Z][A-Za-z0-9]*$")
_UPPER_SNAKE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def lint_ontology(ontology: Ontology) -> Dict[str, Any]:
    """FIBO/ISO-704-style hygiene linter (offline, pure model walk — 0 hot-path).

    Returns ``{ok, errors, warnings, findings}`` where each finding is
    ``{severity, check, target, message}``. ERRORS are structural defects that
    should block promotion (duplicate label across class/rel, dangling/cyclic
    ``broader`` subclass edge); WARNINGS are quality issues (missing definition,
    naming convention, alias collision). Covers the cheap, high-value subset of
    the FIBO hygiene tests (docs/ontology/ONTOLOGY_GUIDE.md) that need no reasoner.
    """
    findings: List[Dict[str, str]] = []

    def add(severity: str, check: str, target: str, message: str) -> None:
        findings.append({"severity": severity, "check": check, "target": target, "message": message})

    node_labels = set(ontology.nodes)
    rel_types = set(ontology.relationships)

    # 1. every class/relationship has a definition + naming conventions
    for label, nd in ontology.nodes.items():
        if not str(getattr(nd, "description", "") or "").strip():
            add("warning", "missing_definition", label,
                f"Class '{label}' has no definition (ISO 704 / FIBO require label + definition).")
        if not _PASCAL_CASE.match(label):
            add("warning", "naming", label, f"Class label '{label}' is not UpperCamelCase.")
    for rtype, rd in ontology.relationships.items():
        if not str(getattr(rd, "description", "") or "").strip():
            add("warning", "missing_definition", rtype, f"Relationship '{rtype}' has no definition.")
        if not _UPPER_SNAKE.match(rtype):
            add("warning", "naming", rtype, f"Relationship type '{rtype}' is not UPPER_SNAKE.")

    # 2. unique labels (FIBO enforces globally unique labels)
    for name in sorted(node_labels & rel_types):
        add("error", "duplicate_label", name,
            f"'{name}' is used as BOTH a class and a relationship type — labels must be unique.")
    for label, nd in ontology.nodes.items():
        for alias in (getattr(nd, "aliases", []) or []):
            if alias in node_labels and alias != label:
                add("warning", "alias_collision", label,
                    f"Class '{label}' alias '{alias}' collides with another class label.")

    # 3. subclass (broader) hygiene: targets exist + no cycles
    broader_map = {label: list(getattr(nd, "broader", []) or []) for label, nd in ontology.nodes.items()}
    for label, parents in broader_map.items():
        for parent in parents:
            if parent not in node_labels:
                add("error", "broader_target", label,
                    f"Class '{label}' broader '{parent}' is not a defined class.")
    _WHITE, _GRAY, _BLACK = 0, 1, 2
    color = {label: _WHITE for label in node_labels}

    def _has_cycle(node: str) -> bool:
        color[node] = _GRAY
        for parent in broader_map.get(node, []):
            if parent not in color:
                continue
            if color[parent] == _GRAY:
                return True
            if color[parent] == _WHITE and _has_cycle(parent):
                return True
        color[node] = _BLACK
        return False

    for label in sorted(node_labels):
        if color[label] == _WHITE and _has_cycle(label):
            add("error", "broader_cycle", label,
                "Subclass (broader) hierarchy contains a cycle.")
            break

    # 4. relationship endpoint hygiene: a non-wildcard source/target must be a
    #    class defined in THIS composition. A dangling endpoint is legitimate
    #    mid-composition (the class lives in a sibling module not yet merged in),
    #    so this is a WARNING, not an error — but it must be surfaced: a rel
    #    whose endpoint never resolves yields no Cypher constraint and no
    #    traversal (e.g. `acc:GOVERNS -> FinancialMetric` when `acc` is composed
    #    without `ind`). GRL principle 3 ("always validate after change").
    for rtype, rd in ontology.relationships.items():
        for role in ("source", "target"):
            endpoint = str(getattr(rd, role, "Any") or "Any")
            if endpoint != "Any" and endpoint not in node_labels:
                add("warning", "relationship_endpoint", rtype,
                    f"Relationship '{rtype}' {role} '{endpoint}' is not a class "
                    f"defined in this ontology (dangling endpoint — resolves only "
                    f"if a module providing '{endpoint}' is composed in).")

    errors = [f for f in findings if f["severity"] == "error"]
    warnings = [f for f in findings if f["severity"] == "warning"]
    return {"ok": not errors, "errors": errors, "warnings": warnings, "findings": findings}


def competency_question_coverage(
    ontology: Ontology,
    competency_questions: Sequence[str],
) -> Dict[str, Any]:
    """Coverage lint for competency questions (gap-closure item #7, framework
    DEFERRED — this is the metadata/coverage half, not an evaluation runner).

    Kendall & McGuinness treat competency questions as the CORE evaluation
    method: every modelled element should be exercised by at least one CQ, and
    every CQ should touch the ontology. This offline check flags:

    - ``uncovered_elements`` — node/relationship labels (or their aliases) not
      mentioned by any CQ (candidate dead schema, or a missing CQ).
    - ``empty_questions`` — CQs that reference no ontology element at all
      (out-of-scope question, or missing schema).

    Matching is case-insensitive on the label, its aliases, and a spaced form of
    CamelCase/UPPER_SNAKE (``FinancialMetric`` -> ``financial metric``). Pure
    model walk; never on the hot path. Store the CQs themselves as artifact
    metadata (``source_summary["competency_questions"]``); this function reports
    coverage over them.
    """
    def _variants(label: str, aliases: Sequence[str]) -> List[str]:
        forms = {label.casefold()}
        for alias in aliases or []:
            text = str(alias).strip().casefold()
            if text:
                forms.add(text)
        # spaced form: split CamelCase and UPPER_SNAKE into words
        spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", label).replace("_", " ")
        forms.add(spaced.casefold())
        return [f for f in forms if f]

    elements: Dict[str, List[str]] = {}
    for label, nd in ontology.nodes.items():
        elements[label] = _variants(label, getattr(nd, "aliases", []))
    for rtype, rd in ontology.relationships.items():
        elements[rtype] = _variants(rtype, getattr(rd, "aliases", []))

    questions_norm = [str(q or "").casefold() for q in competency_questions]

    uncovered: List[str] = []
    for label, forms in elements.items():
        if not any(any(form in q for form in forms) for q in questions_norm):
            uncovered.append(label)

    empty_questions: List[str] = []
    for raw, norm in zip(competency_questions, questions_norm):
        if not any(any(form in norm for form in forms) for forms in elements.values()):
            empty_questions.append(str(raw))

    total = len(elements)
    covered = total - len(uncovered)
    return {
        "total_elements": total,
        "covered_elements": covered,
        "uncovered_elements": sorted(uncovered),
        "coverage_ratio": (covered / total) if total else 1.0,
        "question_count": len(competency_questions),
        "empty_questions": empty_questions,
    }


def load_competency_questions(path: str | Path) -> List[Dict[str, Any]]:
    """Load the authored competency-question set (YAML; ``competency_questions``
    list). Each entry carries at least ``id``, ``question`` and ``requires``
    (FIBO element labels). Pure file read — offline, never on the hot path.
    """
    import yaml  # local import: governance is an offline path

    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"Competency-question file not found: {source}")
    data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    questions = data.get("competency_questions", [])
    if not isinstance(questions, list):
        raise ValueError(f"{source}: 'competency_questions' must be a list.")
    return [dict(q) for q in questions if isinstance(q, dict)]


def competency_question_report(
    ontology: Ontology,
    competency_questions: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """Per-CQ structural diagnosis for ONE ontology arm + element coverage.

    This is the live wiring for :func:`competency_question_coverage` and the
    schema-side half of the CQ x arm matrix (GRL Artefact 1 / §19): for each CQ,
    is the arm's vocabulary able to express it at all?

    A CQ is ``expressible`` when every label in its ``requires`` list is a node
    label, a relationship type, or an alias of one **in this ontology**;
    otherwise the CQ is structurally impossible for this arm and
    ``missing_elements`` records why (e.g. an S3 segment CQ in the ``small``
    arm, which lacks ``HAS_SEGMENT``). The arbiter route a CQ *should* take is
    carried through unchanged as ``expected_route`` for the execution-side
    comparison. Pure model walk; never on the hot path.
    """
    # membership set: labels + rel types + aliases, casefolded
    members = set()
    for label, nd in ontology.nodes.items():
        members.add(label.casefold())
        for alias in (getattr(nd, "aliases", []) or []):
            members.add(str(alias).casefold())
    for rtype, rd in ontology.relationships.items():
        members.add(rtype.casefold())
        for alias in (getattr(rd, "aliases", []) or []):
            members.add(str(alias).casefold())

    questions_out: List[Dict[str, Any]] = []
    coverage_texts: List[str] = []
    expressible_count = 0
    for cq in competency_questions:
        requires = [str(r) for r in (cq.get("requires") or [])]
        missing = [r for r in requires if r.casefold() not in members]
        expressible = not missing
        if expressible:
            expressible_count += 1
        questions_out.append({
            "id": cq.get("id"),
            "slice": cq.get("slice"),
            "kind": cq.get("kind"),
            "hops": cq.get("hops"),
            "expected_route": cq.get("expected_route"),
            "requires": requires,
            "missing_elements": missing,
            "expressible": expressible,
            "verdict": "expressible" if expressible else "schema_impossible",
        })
        # element labels must appear in the text the coverage matcher scans
        coverage_texts.append(f"{cq.get('question', '')} {' '.join(requires)}")

    coverage = competency_question_coverage(ontology, coverage_texts)
    total = len(questions_out)
    return {
        "question_count": total,
        "expressible_count": expressible_count,
        "schema_impossible_count": total - expressible_count,
        "expressible_ratio": (expressible_count / total) if total else 1.0,
        "questions": questions_out,
        "coverage": coverage,
    }


def load_ontology_file(path: str | Path) -> Ontology:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"Ontology file not found: {source}")
    return Ontology.load(source)


@dataclass(slots=True)
class OntologyCheckResult:
    ontology_name: str
    package_id: str
    ontology_version: str
    ok: bool
    errors: List[str]
    warnings: List[str]
    stats: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ontology_name": self.ontology_name,
            "package_id": self.package_id,
            "ontology_version": self.ontology_version,
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "stats": dict(self.stats),
        }


@dataclass(slots=True)
class OntologyDiffResult:
    left_name: str
    right_name: str
    changes: Dict[str, Any]
    package_id: str
    recommended_bump: str
    requires_migration: bool
    migration_warnings: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "left_name": self.left_name,
            "right_name": self.right_name,
            "changes": self.changes,
            "package_id": self.package_id,
            "recommended_bump": self.recommended_bump,
            "requires_migration": self.requires_migration,
            "migration_warnings": list(self.migration_warnings),
        }


@dataclass(slots=True)
class Owlready2InspectionResult:
    source: str
    available: bool
    error: Optional[str]
    stats: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "available": self.available,
            "error": self.error,
            "stats": dict(self.stats),
        }


@dataclass(slots=True)
class GovernanceValidationResult:
    name: str
    available: bool
    ok: bool
    error: Optional[str]
    errors: List[str]
    stats: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "available": self.available,
            "ok": self.ok,
            "error": self.error,
            "errors": list(self.errors),
            "stats": dict(self.stats),
        }


def validate_rdf_with_pyshacl(
    data_graph: str | Path,
    shacl_graph: str | Path,
    *,
    data_format: str = "turtle",
    shacl_format: str = "turtle",
    inference: str = "rdfs",
) -> GovernanceValidationResult:
    """Run pySHACL as an optional offline governance gate.

    SEOCHO's request-time validation uses the lightweight ontology model and
    derived SHACL summaries. Full RDF/SHACL validation belongs in promotion,
    migration, and CI workflows where optional dependencies and richer reports
    are acceptable.
    """

    try:
        from pyshacl import validate
    except Exception as exc:  # pragma: no cover - exercised by unit patching
        return GovernanceValidationResult(
            name="pyshacl",
            available=False,
            ok=False,
            error=f"pyshacl unavailable: {exc}",
            errors=[],
            stats={},
        )

    try:
        conforms, _results_graph, results_text = validate(
            str(data_graph),
            shacl_graph=str(shacl_graph),
            data_graph_format=data_format,
            shacl_graph_format=shacl_format,
            inference=inference,
        )
    except Exception as exc:
        return GovernanceValidationResult(
            name="pyshacl",
            available=True,
            ok=False,
            error=str(exc),
            errors=[str(exc)],
            stats={"inference": inference},
        )

    text = str(results_text or "").strip()
    return GovernanceValidationResult(
        name="pyshacl",
        available=True,
        ok=bool(conforms),
        error=None,
        errors=[] if conforms else ([text] if text else ["pySHACL validation failed."]),
        stats={
            "conforms": bool(conforms),
            "data_format": data_format,
            "shacl_format": shacl_format,
            "inference": inference,
            "result_text": text,
        },
    )


@dataclass(slots=True)
class OntologyGovernanceReport:
    source: str
    ok: bool
    ontology_check: OntologyCheckResult
    context_descriptor: Dict[str, Any]
    artifact_draft: Dict[str, Any]
    shacl_export: Dict[str, Any]
    sample_data_validation: GovernanceValidationResult
    owlready2_inspection: Optional[Owlready2InspectionResult]
    notes: List[str]
    competency: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "ok": self.ok,
            "ontology_check": self.ontology_check.to_dict(),
            "context_descriptor": dict(self.context_descriptor),
            "artifact_draft": dict(self.artifact_draft),
            "shacl_export": dict(self.shacl_export),
            "sample_data_validation": self.sample_data_validation.to_dict(),
            "owlready2_inspection": (
                self.owlready2_inspection.to_dict()
                if self.owlready2_inspection is not None
                else None
            ),
            "notes": list(self.notes),
            "competency": dict(self.competency) if self.competency is not None else None,
        }


def check_ontology(ontology: Ontology) -> OntologyCheckResult:
    raw_findings = ontology.validate()
    errors: List[str] = []
    warnings: List[str] = []

    for item in raw_findings:
        if "consider adding one" in item:
            warnings.append(item)
        else:
            errors.append(item)

    if ontology.graph_model not in {"lpg", "rdf", "hybrid"}:
        errors.append(
            f"Unsupported graph_model '{ontology.graph_model}'. Expected one of: lpg, rdf, hybrid."
        )

    if not ontology.nodes:
        errors.append("Ontology defines no node types.")

    if not ontology.version_is_valid():
        warnings.append("Ontology version should use semantic versioning: MAJOR.MINOR.PATCH.")

    # Hygiene linter (FIBO/ISO-704 subset). Surface lint WARNINGS here without
    # flipping `ok` (back-compat); lint ERRORS are enforced at the promotion gate
    # (approve_semantic_artifact) via lint_ontology() directly.
    lint = lint_ontology(ontology)
    for f in lint["warnings"]:
        warnings.append(f"[hygiene:{f['check']}] {f['message']}")

    stats = {
        "package_id": ontology.package_id,
        "graph_model": ontology.graph_model,
        "namespace": ontology.namespace,
        "node_count": len(ontology.nodes),
        "relationship_count": len(ontology.relationships),
        "unique_property_count": sum(
            len(node_def.unique_properties) for node_def in ontology.nodes.values()
        ),
        "indexed_property_count": sum(
            len(node_def.indexed_properties) for node_def in ontology.nodes.values()
        ),
        "schema_fingerprint": ontology.schema_fingerprint(),
        "version_valid": ontology.version_is_valid(),
        "hygiene_error_count": len(lint["errors"]),
        "hygiene_warning_count": len(lint["warnings"]),
    }
    return OntologyCheckResult(
        ontology_name=ontology.name,
        package_id=ontology.package_id,
        ontology_version=ontology.version,
        ok=not errors,
        errors=errors,
        warnings=warnings,
        stats=stats,
    )


def export_ontology_payload(
    ontology: Ontology,
    *,
    output_format: str,
) -> Dict[str, Any] | List[Any]:
    normalized = output_format.lower()
    if normalized == "jsonld":
        return ontology.to_jsonld()
    if normalized == "yaml":
        return ontology.to_dict()
    if normalized == "dict":
        return ontology.to_dict()
    if normalized == "shacl":
        return ontology.to_shacl()
    raise ValueError(f"Unsupported ontology export format: {output_format}")


def build_ontology_governance_report(
    source: str | Path,
    *,
    artifact_name: Optional[str] = None,
    include_owl_inspection: bool = True,
    competency_questions: Optional[Sequence[Dict[str, Any]]] = None,
) -> OntologyGovernanceReport:
    from .ontology_context import compile_ontology_context

    ontology = load_ontology_file(source)
    source_path = str(source)
    ontology_check = check_ontology(ontology)
    compiled_context = compile_ontology_context(ontology)
    artifact_draft_obj = ontology.to_semantic_artifact_draft(name=artifact_name)
    artifact_draft = (
        artifact_draft_obj.to_dict()
        if hasattr(artifact_draft_obj, "to_dict")
        else dict(artifact_draft_obj)
    )
    shacl_export = _build_shacl_export(ontology)
    sample_data = _build_sample_instance_graph(ontology)
    sample_errors = ontology.validate_with_shacl(sample_data)
    sample_validation = GovernanceValidationResult(
        name="synthetic_sample_data",
        available=True,
        ok=len(sample_errors) == 0,
        error=None,
        errors=sample_errors,
        stats={
            "node_count": len(sample_data.get("nodes", [])),
            "relationship_count": len(sample_data.get("relationships", [])),
        },
    )

    owlready2_inspection: Optional[Owlready2InspectionResult] = None
    notes: List[str] = []
    if include_owl_inspection:
        owlready2_inspection = inspect_owl_ontology(source_path)
        if owlready2_inspection.available and owlready2_inspection.error is None:
            notes.append("owlready2 inspection available for offline ontology governance.")
        elif not owlready2_inspection.available:
            notes.append("owlready2 is unavailable; heavy reasoning remains disabled for this environment.")
        elif owlready2_inspection.error:
            notes.append("owlready2 inspection failed; review the ontology source before promotion.")

    if shacl_export["stats"]["node_shape_count"] == 0:
        notes.append("Generated SHACL export contains no node shapes.")
    if shacl_export["stats"]["property_shape_count"] == 0:
        notes.append("Generated SHACL export contains no property constraints.")

    ok = ontology_check.ok and sample_validation.ok
    if shacl_export.get("unsupported_rules"):
        ok = False
        notes.append("Generated SHACL export contains unsupported rules.")

    competency: Optional[Dict[str, Any]] = None
    if competency_questions:
        competency = competency_question_report(ontology, competency_questions)
        if competency["schema_impossible_count"]:
            notes.append(
                f"{competency['schema_impossible_count']}/{competency['question_count']} "
                "competency questions are structurally impossible for this ontology arm."
            )

    return OntologyGovernanceReport(
        source=source_path,
        ok=ok,
        ontology_check=ontology_check,
        context_descriptor=compiled_context.descriptor.to_dict(),
        artifact_draft=artifact_draft,
        shacl_export=shacl_export,
        sample_data_validation=sample_validation,
        owlready2_inspection=owlready2_inspection,
        notes=notes,
        competency=competency,
    )


def diff_ontologies(left: Ontology, right: Ontology) -> OntologyDiffResult:
    left_dict = left.to_dict()
    right_dict = right.to_dict()

    left_nodes = left_dict.get("nodes", {})
    right_nodes = right_dict.get("nodes", {})
    left_rels = left_dict.get("relationships", {})
    right_rels = right_dict.get("relationships", {})

    def _changed_keys(left_map: Dict[str, Any], right_map: Dict[str, Any]) -> List[str]:
        shared = set(left_map) & set(right_map)
        changed: List[str] = []
        for key in sorted(shared):
            if json.dumps(left_map[key], sort_keys=True) != json.dumps(right_map[key], sort_keys=True):
                changed.append(key)
        return changed

    package_id = right.package_id or left.package_id

    changes = {
        "metadata": {
            "changed": [
                key
                for key in ("graph_type", "package_id", "version", "description", "graph_model", "namespace")
                if left_dict.get(key) != right_dict.get(key)
            ],
        },
        "nodes": {
            "added": sorted(set(right_nodes) - set(left_nodes)),
            "removed": sorted(set(left_nodes) - set(right_nodes)),
            "changed": _changed_keys(left_nodes, right_nodes),
        },
        "relationships": {
            "added": sorted(set(right_rels) - set(left_rels)),
            "removed": sorted(set(left_rels) - set(right_rels)),
            "changed": _changed_keys(left_rels, right_rels),
        },
    }

    breaking = any(
        [
            bool(changes["nodes"]["removed"]),
            bool(changes["relationships"]["removed"]),
            bool(changes["nodes"]["changed"]),
            bool(changes["relationships"]["changed"]),
            "graph_model" in changes["metadata"]["changed"],
            "namespace" in changes["metadata"]["changed"],
            "package_id" in changes["metadata"]["changed"],
        ]
    )
    additive = any(
        [
            bool(changes["nodes"]["added"]),
            bool(changes["relationships"]["added"]),
        ]
    )
    metadata_only = bool(changes["metadata"]["changed"]) and not any(
        [
            changes["nodes"]["added"],
            changes["nodes"]["removed"],
            changes["nodes"]["changed"],
            changes["relationships"]["added"],
            changes["relationships"]["removed"],
            changes["relationships"]["changed"],
        ]
    )

    if breaking:
        recommended_bump = "major"
    elif additive:
        recommended_bump = "minor"
    elif metadata_only:
        recommended_bump = "patch"
    else:
        recommended_bump = "none"

    migration_warnings = _build_migration_warnings(
        left=left,
        right=right,
        changes=changes,
        recommended_bump=recommended_bump,
    )

    return OntologyDiffResult(
        left_name=f"{left.name}@{left.version}",
        right_name=f"{right.name}@{right.version}",
        changes=changes,
        package_id=package_id,
        recommended_bump=recommended_bump,
        requires_migration=breaking,
        migration_warnings=migration_warnings,
    )


def _parse_semver(value: str) -> Optional[Tuple[int, int, int]]:
    raw = value.strip()
    parts = raw.split(".")
    if len(parts) != 3:
        return None
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None


def _build_migration_warnings(
    *,
    left: Ontology,
    right: Ontology,
    changes: Dict[str, Any],
    recommended_bump: str,
) -> List[str]:
    warnings: List[str] = []

    if left.package_id != right.package_id:
        warnings.append(
            f"package_id changed from '{left.package_id}' to '{right.package_id}'; treat this as a package migration boundary."
        )

    left_semver = _parse_semver(left.version)
    right_semver = _parse_semver(right.version)
    if left_semver is None or right_semver is None:
        warnings.append(
            f"Version comparison skipped because semver parsing failed ({left.version!r} -> {right.version!r})."
        )
        return warnings

    if recommended_bump == "none" and left_semver != right_semver:
        warnings.append("Version changed but no schema-level ontology changes were detected.")
        return warnings

    if recommended_bump == "patch":
        if right_semver <= left_semver:
            warnings.append(
                "Patch-level metadata changes were detected but the ontology version did not increase."
            )
    elif recommended_bump == "minor":
        if right_semver[0] == left_semver[0] and right_semver[1] <= left_semver[1]:
            warnings.append(
                "Additive ontology changes were detected; expected at least a minor version bump."
            )
    elif recommended_bump == "major":
        if right_semver[0] <= left_semver[0]:
            warnings.append(
                "Breaking ontology changes were detected; expected a major version bump."
            )
        if changes["nodes"]["removed"] or changes["relationships"]["removed"]:
            warnings.append(
                "Removed node/relationship types may require data migration and downstream query updates."
            )
        if changes["nodes"]["changed"] or changes["relationships"]["changed"]:
            warnings.append(
                "Changed existing node/relationship definitions may invalidate prompt assumptions, constraints, or denormalization behavior."
            )

    if "graph_model" in changes["metadata"]["changed"]:
        warnings.append("graph_model changed; treat this as a runtime/query migration, not just a schema patch.")
    if "namespace" in changes["metadata"]["changed"]:
        warnings.append("namespace changed; RDF identifiers and exported JSON-LD consumers may require remapping.")

    return warnings


def inspect_owl_ontology(source: str | Path) -> Owlready2InspectionResult:
    source_str = str(source)
    try:
        from owlready2 import get_ontology
    except Exception as exc:  # pragma: no cover - exercised by unit patching
        return Owlready2InspectionResult(
            source=source_str,
            available=False,
            error=f"owlready2 unavailable: {exc}",
            stats={},
        )

    cleanup_path: Optional[Path] = None
    try:
        prepared_source, cleanup_path = _prepare_owlready2_source(source_str)
        onto = get_ontology(prepared_source).load()
        classes: Sequence[Any] = list(onto.classes())
        individuals: Sequence[Any] = list(onto.individuals())
        properties: Sequence[Any] = list(onto.properties())
        imports: Sequence[Any] = list(getattr(onto, "imported_ontologies", []))
        return Owlready2InspectionResult(
            source=source_str,
            available=True,
            error=None,
            stats={
                "class_count": len(classes),
                "individual_count": len(individuals),
                "property_count": len(properties),
                "import_count": len(imports),
                "sample_classes": [str(getattr(item, "name", item)) for item in classes[:10]],
                "sample_properties": [str(getattr(item, "name", item)) for item in properties[:10]],
            },
        )
    except Exception as exc:
        return Owlready2InspectionResult(
            source=source_str,
            available=True,
            error=str(exc),
            stats={},
        )
    finally:
        if cleanup_path is not None:
            cleanup_path.unlink(missing_ok=True)


def reason_consistency(ontology: Ontology) -> Dict[str, Any]:
    """OWL 2 DL consistency check via owlready2 + an external reasoner.

    **Offline / lazy / optional** (CLAUDE.md §6.3): never on the request hot
    path — only at the draft->approve governance gate. The owlready2 import,
    TTL->RDF/XML conversion, and reasoner invocation all happen here and nowhere
    in query/extraction. Degrades gracefully to ``available=False`` when
    owlready2 or a JVM is absent, so environments without Java still approve on
    a structural+lint pass.

    Returns a dict::

        {
          "consistent": True | False | None,   # None == not determined
          "unsatisfiable_classes": [...],       # classes equivalent to owl:Nothing
          "available": bool,                    # reasoner actually ran
          "reasoner": "pellet" | None,
          "error": str | None,
        }
    """
    result: Dict[str, Any] = {
        "consistent": None,
        "unsatisfiable_classes": [],
        "available": False,
        "reasoner": None,
        "error": None,
    }
    try:
        from owlready2 import World, sync_reasoner_pellet
        from owlready2.base import OwlReadyInconsistentOntologyError
    except Exception as exc:  # pragma: no cover - exercised by patching
        result["error"] = f"owlready2 unavailable: {exc}"
        return result

    tmp_ttl: Optional[Path] = None
    cleanup_rdf: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".ttl", delete=False) as fp:
            tmp_ttl = Path(fp.name)
        ontology.to_ttl(tmp_ttl)
        prepared_source, cleanup_rdf = _prepare_owlready2_source(str(tmp_ttl))
        world = World()
        onto = world.get_ontology(prepared_source).load()
        try:
            with onto:
                sync_reasoner_pellet(
                    world,
                    infer_property_values=False,
                    infer_data_property_values=False,
                )
            result["available"] = True
            result["reasoner"] = "pellet"
            unsat = [
                str(getattr(cls, "name", cls))
                for cls in world.inconsistent_classes()
            ]
            result["unsatisfiable_classes"] = unsat
            result["consistent"] = not unsat
        except OwlReadyInconsistentOntologyError:
            result["available"] = True
            result["reasoner"] = "pellet"
            result["consistent"] = False
    except Exception as exc:
        # Most commonly a missing JVM/Java install — treat as "reasoner not
        # available" rather than a structural verdict so the gate can still
        # approve on structural+lint pass.
        result["error"] = str(exc)
        result["available"] = False
    finally:
        if tmp_ttl is not None:
            tmp_ttl.unlink(missing_ok=True)
        if cleanup_rdf is not None:
            cleanup_rdf.unlink(missing_ok=True)
    return result


def governance_gate(
    ontology: Ontology,
    *,
    run_reasoner: bool = True,
) -> Dict[str, Any]:
    """Run the full offline promotion gate for an ontology.

    Composes the structural check (:func:`check_ontology` -> ``validate()``),
    the FIBO/ISO-704 hygiene lint (:func:`lint_ontology`), and — optionally —
    OWL 2 DL consistency (:func:`reason_consistency`). Pure / offline; intended
    for the draft->approve transition, never the request hot path.

    ``ok`` is False when there is a structural error, a lint **error** (not just
    a warning), or the reasoner ran and found the ontology inconsistent. A
    reasoner that did not run (``available=False``) never blocks approval.
    """
    check = check_ontology(ontology)
    lint = lint_ontology(ontology)
    consistency = (
        reason_consistency(ontology)
        if run_reasoner
        else {
            "consistent": None,
            "unsatisfiable_classes": [],
            "available": False,
            "reasoner": None,
            "error": "reasoner skipped (run_reasoner=False)",
        }
    )

    structural_ok = check.ok and bool(lint.get("ok", True))
    consistency_blocks = consistency.get("consistent") is False
    return {
        "ok": structural_ok and not consistency_blocks,
        "structural": {
            "ok": check.ok,
            "errors": check.errors,
            "warnings": check.warnings,
        },
        "lint": lint,
        "consistency": consistency,
    }


def conformance_score(
    ontology: Ontology,
    *,
    competency_questions: Optional[Sequence[Dict[str, Any]]] = None,
    run_reasoner: bool = False,
    threshold: float = 0.8,
) -> Dict[str, Any]:
    """Scalar conformance score + explicit hard gates (GRL Artefact 7).

    Collapses the offline governance signals into one 0..1 score and a pass/fail
    so an ontology arm can be blocked from the experiment until it conforms (the
    §16 user-first-release-gate analog for ontologies). Offline / pure; reasoner
    optional (off by default to stay cheap and respect the §6.3 boundary).

    Hard gates (any failing => ``passed`` is False regardless of score):
      - structural check ok
      - zero lint ERRORS
      - reasoner did not prove inconsistency (unknown / unavailable never blocks)

    Soft components, averaged into ``score``:
      - structural_ok, lint_ok (0/1 each)
      - cq_expressible_ratio (1.0 when no CQs are supplied)
      - shacl_message_coverage (fraction of constrained property shapes that
        carry a plain-English ``sh:message``, GRL Artefact 3)
    """
    gate = governance_gate(ontology, run_reasoner=run_reasoner)
    structural_ok = bool(gate["structural"]["ok"])
    lint_error_count = len(gate["lint"]["errors"])
    consistency_val = gate["consistency"].get("consistent")
    consistency_state = (
        "inconsistent" if consistency_val is False
        else "consistent" if consistency_val is True
        else "unknown"
    )

    cq_ratio: Optional[float] = None
    if competency_questions:
        cq_ratio = competency_question_report(ontology, competency_questions)["expressible_ratio"]

    shacl = ontology.to_shacl()
    prop_shapes = [
        prop
        for shape in shacl.get("shapes", []) if isinstance(shape, dict)
        for prop in shape.get("properties", []) if isinstance(prop, dict)
    ]
    constrained = [p for p in prop_shapes
                   if p.get("minCount") is not None or p.get("maxCount") is not None]
    with_msg = [p for p in constrained if p.get("message")]
    msg_cov = (len(with_msg) / len(constrained)) if constrained else 1.0

    components = [
        1.0 if structural_ok else 0.0,
        1.0 if lint_error_count == 0 else 0.0,
        cq_ratio if cq_ratio is not None else 1.0,
        msg_cov,
    ]
    score = sum(components) / len(components)
    hard_ok = structural_ok and lint_error_count == 0 and consistency_val is not False
    return {
        "score": round(score, 4),
        "threshold": threshold,
        "passed": bool(hard_ok and score >= threshold),
        "components": {
            "structural_ok": structural_ok,
            "lint_error_count": lint_error_count,
            "consistency": consistency_state,
            "cq_expressible_ratio": cq_ratio,
            "shacl_message_coverage": round(msg_cov, 4),
        },
    }


def _build_shacl_export(ontology: Ontology) -> Dict[str, Any]:
    shacl_document = ontology.to_shacl()
    return {
        "document": shacl_document,
        "turtle": _render_shacl_turtle(shacl_document),
        "unsupported_rules": [],
        "stats": {
            "node_shape_count": len(shacl_document.get("shapes", [])),
            "property_shape_count": sum(
                len(shape.get("properties", []))
                for shape in shacl_document.get("shapes", [])
                if isinstance(shape, dict)
            ),
        },
    }


def _render_shacl_turtle(shacl_document: Dict[str, Any]) -> str:
    lines = [
        "@prefix sh: <http://www.w3.org/ns/shacl#> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "@prefix seocho: <https://seocho.dev/ontology/> .",
        "",
    ]
    for shape in shacl_document.get("shapes", []):
        if not isinstance(shape, dict):
            continue
        target_class = str(shape.get("targetClass", "")).strip()
        if not target_class:
            continue
        shape_name = target_class.split(":", 1)[-1] + "Shape"
        lines.append(f"seocho:{shape_name} a sh:NodeShape ;")
        lines.append(f"  sh:targetClass {target_class} ;")
        properties = [
            prop
            for prop in shape.get("properties", [])
            if isinstance(prop, dict)
        ]
        if not properties:
            lines[-1] = lines[-1].rstrip(" ;") + " ."
            lines.append("")
            continue
        for index, prop in enumerate(properties):
            block_suffix = " ;" if index < len(properties) - 1 else " ."
            lines.append("  sh:property [")
            terms = [f"sh:path {prop.get('path')}"]
            if prop.get("datatype"):
                terms.append(f"sh:datatype {prop.get('datatype')}")
            if prop.get("minCount") is not None:
                terms.append(f"sh:minCount {int(prop.get('minCount', 0))}")
            if prop.get("maxCount") is not None:
                terms.append(f"sh:maxCount {int(prop.get('maxCount', 0))}")
            if prop.get("message"):
                escaped = str(prop["message"]).replace('"', '\\"')
                terms.append(f'sh:message "{escaped}"')
            for term_index, term in enumerate(terms):
                suffix = " ;" if term_index < len(terms) - 1 else ""
                lines.append(f"    {term}{suffix}")
            lines.append(f"  ]{block_suffix}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _build_sample_instance_graph(ontology: Ontology) -> Dict[str, Any]:
    nodes: List[Dict[str, Any]] = []
    relationships: List[Dict[str, Any]] = []
    node_ids: Dict[str, str] = {}

    for label, node_def in ontology.nodes.items():
        node_id = f"sample_{label.lower()}"
        node_ids[label] = node_id
        properties: Dict[str, Any] = {}
        for prop_name, prop_def in node_def.properties.items():
            properties[prop_name] = _sample_property_value(label, prop_name, prop_def.property_type.value)
        nodes.append(
            {
                "id": node_id,
                "label": label,
                "properties": properties,
            }
        )

    for rel_type, rel_def in ontology.relationships.items():
        source_id = node_ids.get(rel_def.source)
        target_id = node_ids.get(rel_def.target)
        if not source_id or not target_id:
            continue
        relationships.append(
            {
                "source": source_id,
                "target": target_id,
                "type": rel_type,
                "properties": {},
            }
        )

    return {"nodes": nodes, "relationships": relationships}


def _sample_property_value(label: str, property_name: str, property_type: str) -> Any:
    normalized = str(property_type).strip().upper()
    if normalized == "INTEGER":
        return 1
    if normalized == "FLOAT":
        return 1.0
    if normalized == "BOOLEAN":
        return True
    if normalized == "DATE":
        return "2026-01-01"
    if normalized == "DATETIME":
        return "2026-01-01T00:00:00Z"
    return f"{label}_{property_name}"


def _prepare_owlready2_source(source: str) -> Tuple[str, Optional[Path]]:
    local_path = _coerce_local_path(source)
    if local_path is None or local_path.suffix.lower() != ".ttl":
        return source, None

    try:
        import rdflib
    except Exception as exc:
        raise RuntimeError(f"rdflib unavailable for TTL -> RDF/XML conversion: {exc}") from exc

    graph = rdflib.Graph()
    graph.parse(str(local_path), format="turtle")
    with tempfile.NamedTemporaryFile(suffix=".rdf", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    graph.serialize(destination=str(tmp_path), format="xml")
    return tmp_path.as_uri(), tmp_path


def _coerce_local_path(source: str) -> Optional[Path]:
    raw = str(source).strip()
    if not raw:
        return None
    if raw.startswith("file://"):
        parsed = urlparse(raw)
        return Path(unquote(parsed.path))
    candidate = Path(raw)
    if candidate.exists():
        return candidate
    return None
