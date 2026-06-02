from __future__ import annotations

from seocho import NodeDef, Ontology, P, RelDef
from seocho.ontology_versioning import (
    build_ontology_upgrade_plan,
    is_valid_semver,
    ontology_schema_fingerprint,
    ontology_version_identity,
    parse_semver,
)


def _ontology(*, version: str = "1.0.0") -> Ontology:
    return Ontology(
        name="finance",
        package_id="finance-core",
        version=version,
        nodes={
            "Company": NodeDef(
                description="A legal entity that reports financial metrics.",
                properties={"name": P(str, unique=True)},
            ),
            "FinancialMetric": NodeDef(
                description="A reported metric value.",
                properties={"name": P(str, unique=True), "value": P(float)},
            ),
        },
        relationships={
            "REPORTED": RelDef(
                source="Company",
                target="FinancialMetric",
                description="Connects an issuer to a reported metric.",
            ),
        },
    )


def test_semver_validation_accepts_core_and_suffixes() -> None:
    assert parse_semver("1.2.3") == (1, 2, 3)
    assert parse_semver("1.2.3-alpha.1") == (1, 2, 3)
    assert parse_semver("1.2.3+build.5") == (1, 2, 3)
    assert is_valid_semver("1.2.3") is True
    assert is_valid_semver("1.2") is False


def test_schema_fingerprint_ignores_version_only_changes() -> None:
    left = _ontology(version="1.0.0")
    right = _ontology(version="1.0.1")

    assert ontology_schema_fingerprint(left) == ontology_schema_fingerprint(right)
    assert ontology_version_identity(left).version_valid is True


def test_upgrade_plan_marks_additive_change_as_minor_reindex() -> None:
    left = _ontology(version="1.0.0")
    right = _ontology(version="1.1.0")
    right.nodes["Filing"] = NodeDef(
        description="A regulatory filing document.",
        properties={"accession": P(str, unique=True)},
    )

    plan = build_ontology_upgrade_plan(left, right)

    assert plan.recommended_bump == "minor"
    assert plan.version_satisfies_recommendation is True
    assert plan.requires_migration is False
    assert plan.reindex_required is True
    assert plan.shacl_revalidation_required is True
    assert plan.query_cache_invalidation is True
    assert "Filing" in plan.changes["nodes"]["added"]
    assert any("extraction prompts" in item for item in plan.indexing_effects)
    assert any("new labels" in item for item in plan.query_effects)


def test_upgrade_plan_marks_removed_type_as_breaking_major() -> None:
    left = _ontology(version="1.0.0")
    right = Ontology(
        name="finance",
        package_id="finance-core",
        version="2.0.0",
        nodes={
            "Company": NodeDef(
                description="A legal entity.",
                properties={"name": P(str, unique=True)},
            ),
        },
        relationships={},
    )

    plan = build_ontology_upgrade_plan(left, right)

    assert plan.recommended_bump == "major"
    assert plan.version_satisfies_recommendation is True
    assert plan.requires_migration is True
    assert plan.reindex_required is True
    assert "FinancialMetric" in plan.changes["nodes"]["removed"]
    assert "REPORTED" in plan.changes["relationships"]["removed"]
    assert any("cleanup of indexed graph data" in item for item in plan.indexing_effects)
    assert any("blocked or rewritten" in item for item in plan.query_effects)


def test_upgrade_plan_warns_when_version_does_not_match_change_scope() -> None:
    left = _ontology(version="1.0.0")
    right = _ontology(version="1.0.1")
    right.nodes["Filing"] = NodeDef(
        description="A regulatory filing document.",
        properties={"accession": P(str, unique=True)},
    )

    plan = left.upgrade_plan(right)

    assert plan.recommended_bump == "minor"
    assert plan.version_valid is True
    assert plan.version_satisfies_recommendation is False
    assert any("recommended minor bump" in item for item in plan.warnings)


def test_upgrade_plan_warns_for_invalid_versions() -> None:
    left = _ontology(version="1.0")
    right = _ontology(version="1.1")

    plan = build_ontology_upgrade_plan(left, right)

    assert plan.version_valid is False
    assert plan.version_satisfies_recommendation is False
    assert any("semantic versioning" in item for item in plan.warnings)
