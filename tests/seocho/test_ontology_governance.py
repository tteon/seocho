from __future__ import annotations

import sys
import types

import pytest

from pathlib import Path

from seocho.ontology import NodeDef, Ontology, P, RelDef
from seocho.ontology_governance import (
    build_ontology_governance_report,
    check_ontology,
    competency_question_report,
    diff_ontologies,
    export_ontology_payload,
    inspect_owl_ontology,
    lint_ontology,
    load_competency_questions,
    validate_rdf_with_pyshacl,
)

_CQ_PATH = (
    Path(__file__).resolve().parents[2]
    / "examples" / "finder" / "datasets" / "competency_questions.yaml"
)


def _arm(*, with_segments: bool) -> Ontology:
    """A minimal ontology resembling a FinDER sweep arm.

    Always has the metric core (be+ind shape); ``with_segments`` adds the fbc
    HAS_SEGMENT structure so S3/S4 segment CQs become expressible (medium arm)
    vs not (small arm).
    """
    nodes = {
        "LegalEntity": NodeDef(
            description="A registered business.",
            properties={"name": P(str, unique=True)},
            aliases=["Company"],
        ),
        "Revenue": NodeDef(description="Top-line revenue.", properties={"name": P(str, unique=True)}),
        "NetIncome": NodeDef(description="Bottom line.", properties={"name": P(str, unique=True)}),
        "OperatingIncome": NodeDef(description="Operating profit.", properties={"name": P(str, unique=True)}),
        "FinancialMetric": NodeDef(description="Abstract metric.", properties={"name": P(str, unique=True)}),
    }
    rels = {
        "REPORTED_METRIC": RelDef(source="LegalEntity", target="FinancialMetric", description="reported"),
    }
    if with_segments:
        nodes["BusinessSegment"] = NodeDef(description="Reportable segment.", properties={"name": P(str, unique=True)})
        nodes["ProductOrService"] = NodeDef(description="Product.", properties={"name": P(str, unique=True)})
        rels["HAS_SEGMENT"] = RelDef(source="LegalEntity", target="BusinessSegment", description="operates")
        rels["PROVIDES"] = RelDef(source="LegalEntity", target="ProductOrService", description="provides")
    return Ontology(name="arm", version="1.0.0", graph_model="lpg", nodes=nodes, relationships=rels)


def _ontology(*, version: str = "1.0.0") -> Ontology:
    return Ontology(
        name="finance",
        version=version,
        graph_model="lpg",
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True), "ticker": P(str, index=True)}),
            "Person": NodeDef(properties={"name": P(str, unique=True)}),
        },
        relationships={
            "WORKS_AT": RelDef(source="Person", target="Company", cardinality="MANY_TO_ONE"),
        },
    )


def test_check_ontology_warns_when_unique_key_missing() -> None:
    ontology = Ontology(
        name="warning_case",
        nodes={"LooseEntity": NodeDef(properties={"name": P(str)})},
        relationships={},
    )

    result = check_ontology(ontology)

    assert result.ok is True
    assert result.package_id == "warning_case"
    assert result.errors == []
    assert any("consider adding one" in item for item in result.warnings)
    assert result.stats["schema_fingerprint"]
    assert result.stats["version_valid"] is True


def test_check_ontology_warns_on_non_semver_version() -> None:
    ontology = _ontology(version="1.0")

    result = check_ontology(ontology)

    assert result.ok is True
    assert result.stats["version_valid"] is False
    assert any("semantic versioning" in item for item in result.warnings)


def test_diff_ontologies_detects_metadata_and_schema_changes() -> None:
    left = _ontology(version="1.0.0")
    right = Ontology(
        name="finance",
        version="1.1.0",
        graph_model="rdf",
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True), "ticker": P(str, index=True)}),
            "Desk": NodeDef(properties={"name": P(str, unique=True)}),
        },
        relationships={
            "ASSIGNED_TO": RelDef(source="Person", target="Desk", cardinality="MANY_TO_ONE"),
        },
    )

    diff = diff_ontologies(left, right)

    assert diff.package_id == "finance"
    assert diff.recommended_bump == "major"
    assert diff.requires_migration is True
    assert "version" in diff.changes["metadata"]["changed"]
    assert "graph_model" in diff.changes["metadata"]["changed"]
    assert "Desk" in diff.changes["nodes"]["added"]
    assert "Person" in diff.changes["nodes"]["removed"]
    assert "ASSIGNED_TO" in diff.changes["relationships"]["added"]
    assert "WORKS_AT" in diff.changes["relationships"]["removed"]
    assert any("major version bump" in item for item in diff.migration_warnings)


def test_export_ontology_payload_shacl_contains_shapes() -> None:
    payload = export_ontology_payload(_ontology(), output_format="shacl")
    assert isinstance(payload, dict)
    assert "shapes" in payload
    assert len(payload["shapes"]) == 2


def test_diff_ontologies_warns_on_package_boundary_change() -> None:
    left = Ontology(
        name="finance",
        package_id="package.finance",
        version="1.0.0",
        nodes={"Company": NodeDef(properties={"name": P(str, unique=True)})},
        relationships={},
    )
    right = Ontology(
        name="finance_v2",
        package_id="package.finance.v2",
        version="2.0.0",
        nodes={"Company": NodeDef(properties={"name": P(str, unique=True)})},
        relationships={},
    )

    diff = diff_ontologies(left, right)

    assert diff.package_id == "package.finance.v2"
    assert any("package migration boundary" in item for item in diff.migration_warnings)


def test_inspect_owl_ontology_uses_optional_owlready2(monkeypatch) -> None:
    class FakeLoadedOntology:
        def classes(self):
            return [types.SimpleNamespace(name="Company"), types.SimpleNamespace(name="Person")]

        def individuals(self):
            return [types.SimpleNamespace(name="Acme")]

        def properties(self):
            return [types.SimpleNamespace(name="worksAt")]

        @property
        def imported_ontologies(self):
            return [types.SimpleNamespace(name="base")]

    class FakeOntologyRef:
        def load(self):
            return FakeLoadedOntology()

    fake_module = types.SimpleNamespace(get_ontology=lambda source: FakeOntologyRef())
    monkeypatch.setitem(sys.modules, "owlready2", fake_module)

    result = inspect_owl_ontology("file:///tmp/fake.owl")

    assert result.available is True
    assert result.error is None
    assert result.stats["class_count"] == 2
    assert result.stats["property_count"] == 1


def test_validate_rdf_with_pyshacl_uses_optional_dependency(monkeypatch, tmp_path) -> None:
    data_path = tmp_path / "data.ttl"
    shapes_path = tmp_path / "shapes.ttl"
    data_path.write_text("@prefix ex: <https://example.com/> .", encoding="utf-8")
    shapes_path.write_text("@prefix sh: <http://www.w3.org/ns/shacl#> .", encoding="utf-8")

    def fake_validate(*args, **kwargs):  # noqa: ANN002, ANN003
        assert args == (str(data_path),)
        assert kwargs["shacl_graph"] == str(shapes_path)
        assert kwargs["inference"] == "rdfs"
        return True, object(), "Conforms"

    fake_module = types.SimpleNamespace(validate=fake_validate)
    monkeypatch.setitem(sys.modules, "pyshacl", fake_module)

    result = validate_rdf_with_pyshacl(data_path, shapes_path)

    assert result.available is True
    assert result.ok is True
    assert result.errors == []
    assert result.stats["conforms"] is True


def test_validate_rdf_with_pyshacl_degrades_when_unavailable(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "pyshacl", None)

    result = validate_rdf_with_pyshacl("data.ttl", "shapes.ttl")

    assert result.available is False
    assert result.ok is False
    assert "pyshacl unavailable" in str(result.error)


def test_governance_report_includes_context_hash_and_shacl_stats(tmp_path) -> None:
    pytest.importorskip("rdflib", reason="TTL governance report requires rdflib (install via [ontology] extra)")
    ttl_path = tmp_path / "finance.ttl"
    ttl_path.write_text(
        """
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix ex: <https://example.com/finance#> .

ex:FinanceOntology a owl:Ontology ;
    rdfs:label "Finance TTL" ;
    owl:versionInfo "1.2.0" .

ex:Company a owl:Class .
ex:FinancialMetric a owl:Class .

ex:reported a owl:ObjectProperty ;
    rdfs:domain ex:Company ;
    rdfs:range ex:FinancialMetric .

ex:name a owl:DatatypeProperty ;
    rdfs:domain ex:Company ;
    rdfs:range xsd:string .

ex:year a owl:DatatypeProperty ;
    rdfs:domain ex:FinancialMetric ;
    rdfs:range xsd:integer .
""".strip(),
        encoding="utf-8",
    )

    report = build_ontology_governance_report(
        ttl_path,
        include_owl_inspection=False,
    )

    assert report.ok is True
    assert report.context_descriptor["ontology_id"] == "FinanceOntology"
    assert report.context_descriptor["ontology_version"] == "1.2.0"
    assert report.context_descriptor["context_hash"]
    assert report.shacl_export["stats"]["node_shape_count"] == 2
    assert report.shacl_export["stats"]["property_shape_count"] >= 2
    assert report.sample_data_validation.ok is True
    assert report.owlready2_inspection is None


# --- relationship-endpoint hygiene (GRL principle 3: validate after change) ---

def test_lint_flags_dangling_relationship_endpoint_as_warning() -> None:
    # GOVERNS -> FinancialMetric with no FinancialMetric class (acc composed
    # without ind): dangling endpoint must surface, but must NOT block (warning).
    ontology = Ontology(
        name="acc_only",
        graph_model="lpg",
        nodes={"AccountingPolicy": NodeDef(description="policy", properties={"name": P(str, unique=True)})},
        relationships={"GOVERNS": RelDef(source="AccountingPolicy", target="FinancialMetric", description="governs")},
    )
    lint = lint_ontology(ontology)
    endpoint_findings = [f for f in lint["findings"] if f["check"] == "relationship_endpoint"]
    assert endpoint_findings, "dangling endpoint should be reported"
    assert all(f["severity"] == "warning" for f in endpoint_findings)
    assert lint["ok"] is True  # warning does not flip ok


def test_lint_passes_when_endpoints_resolve() -> None:
    ontology = _arm(with_segments=True)
    lint = lint_ontology(ontology)
    assert not [f for f in lint["findings"] if f["check"] == "relationship_endpoint"]


# --- competency-question structural diagnosis (CQ x arm matrix, schema side) ---

def test_load_competency_questions_authored_set() -> None:
    cqs = load_competency_questions(_CQ_PATH)
    assert len(cqs) >= 10  # GRL Artefact 1: 10-12 CQs
    slices = {cq["slice"] for cq in cqs}
    assert {"S1_FIN_COMP", "S3_CO_COMP", "S5_FN_MULTI", "S6_BASELINE_SINGLE"} <= slices
    for cq in cqs:  # every CQ must declare what it requires (no empty CQ)
        assert cq.get("requires"), f"{cq.get('id')} missing 'requires'"


def test_segment_cqs_are_schema_impossible_for_small_arm() -> None:
    cqs = load_competency_questions(_CQ_PATH)
    small = competency_question_report(_arm(with_segments=False), cqs)
    medium = competency_question_report(_arm(with_segments=True), cqs)

    def _verdict(report, cq_id):
        return next(q["verdict"] for q in report["questions"] if q["id"] == cq_id)

    # S3 segment CQ: impossible without HAS_SEGMENT (small) -> expressible (medium)
    assert _verdict(small, "S3-CQ1") == "schema_impossible"
    assert _verdict(medium, "S3-CQ1") == "expressible"
    # metric CQs are expressible in BOTH arms (be+ind core present in both)
    assert _verdict(small, "S1-CQ1") == "expressible"
    assert _verdict(small, "S6-CQ1") == "expressible"
    # adding the fbc structure can only raise expressibility, never lower it
    assert medium["expressible_count"] > small["expressible_count"]


def test_competency_report_records_missing_elements_and_route() -> None:
    cqs = load_competency_questions(_CQ_PATH)
    report = competency_question_report(_arm(with_segments=False), cqs)
    s3 = next(q for q in report["questions"] if q["id"] == "S3-CQ1")
    assert "HAS_SEGMENT" in s3["missing_elements"]
    assert s3["expected_route"] == "NARRATIVE"  # carried through for execution-side
    # the dead competency_question_coverage is now wired & returns a real ratio
    assert 0.0 <= report["coverage"]["coverage_ratio"] <= 1.0
    assert report["coverage"]["question_count"] == len(cqs)


def test_build_report_includes_competency_when_cqs_passed(tmp_path) -> None:
    ttl = tmp_path / "arm.ttl"
    # reuse the TTL fixture path indirectly: write a tiny ontology via to_dict->yaml
    import yaml as _yaml
    arm = _arm(with_segments=False)
    src = tmp_path / "arm.yaml"
    src.write_text(_yaml.safe_dump(arm.to_dict()), encoding="utf-8")
    cqs = load_competency_questions(_CQ_PATH)
    report = build_ontology_governance_report(
        src, include_owl_inspection=False, competency_questions=cqs
    )
    assert report.competency is not None
    assert report.competency["schema_impossible_count"] >= 1  # segment CQs
    assert report.to_dict()["competency"]["question_count"] == len(cqs)
