"""Tests for the OntoClean meta-property critic (seocho.ontology_ontoclean).

The constraint engine is pure and tested offline with hand-authored tags; the
LLM inference path is tested with a fake backend (no live MARA call)."""

from __future__ import annotations

from seocho.ontology import NodeDef, Ontology, P, RelDef
from seocho.ontology_ontoclean import (
    MetaProperties,
    check_ontoclean,
    dump_metaproperties,
    infer_metaproperties,
    load_metaproperties,
)
from seocho.ontology_scorecard import score_ontology


def _person_under_student() -> Ontology:
    """The canonical OntoClean rigidity violation: Person (rigid) modelled as a
    subclass of Student (an anti-rigid role)."""
    return Ontology(
        "roles",
        nodes={
            "Student": NodeDef(
                description="A person enrolled at a school.",
                properties={"id": P(str, unique=True)},
            ),
            "Person": NodeDef(
                description="A human being.",
                broader=["Student"],  # WRONG: rigid under anti-rigid
                properties={"ssn": P(str, unique=True)},
            ),
        },
    )


def _person_over_student() -> Ontology:
    """The fix: Student (role) is a subclass of Person (rigid)."""
    return Ontology(
        "roles",
        nodes={
            "Person": NodeDef(
                description="A human being.",
                properties={"ssn": P(str, unique=True)},
            ),
            "Student": NodeDef(
                description="A person enrolled at a school.",
                broader=["Person"],
                properties={"ssn": P(str, unique=True), "school": P(str)},
            ),
        },
    )


TAGS = {
    "Person": MetaProperties(rigid=True, carries_identity=True, supplies_identity=True),
    "Student": MetaProperties(rigid=False, carries_identity=True, dependent=True),
}


def test_rigidity_violation_detected():
    result = check_ontoclean(_person_under_student(), TAGS)
    assert not result.ok
    assert result.edges_checked == 1
    rigidity = [v for v in result.violations if v.constraint == "rigidity"]
    assert len(rigidity) == 1
    assert rigidity[0].parent == "Student" and rigidity[0].child == "Person"
    assert rigidity[0].severity == "violation"


def test_rigidity_fix_is_clean():
    result = check_ontoclean(_person_over_student(), TAGS)
    assert result.ok
    assert not [v for v in result.violations if v.severity == "violation"]


def test_unknown_tags_skip_constraint_no_false_positive():
    # both endpoints unknown rigidity → no rigidity violation
    tags = {"Person": MetaProperties(), "Student": MetaProperties()}
    result = check_ontoclean(_person_under_student(), tags)
    assert result.ok
    assert result.edges_checked == 1


def test_untagged_classes_reported():
    result = check_ontoclean(_person_under_student(), {"Person": MetaProperties(rigid=True)})
    assert "Student" in result.untagged_classes


def test_dependence_violation():
    onto = Ontology(
        "dep",
        nodes={
            "Spouse": NodeDef(description="A married person.", properties={"id": P(str, unique=True)}),
            "Citizen": NodeDef(description="A citizen.", broader=["Spouse"], properties={"id": P(str, unique=True)}),
        },
    )
    tags = {
        "Spouse": MetaProperties(dependent=True),
        "Citizen": MetaProperties(dependent=False),
    }
    result = check_ontoclean(onto, tags)
    assert any(v.constraint == "dependence" and v.severity == "violation" for v in result.violations)


def test_unity_mismatch_violation():
    onto = Ontology(
        "unity",
        nodes={
            "Water": NodeDef(description="An amount of water.", properties={"id": P(str, unique=True)}),
            "Lake": NodeDef(description="A body of water.", broader=["Water"], properties={"id": P(str, unique=True)}),
        },
    )
    tags = {"Water": MetaProperties(unity=False), "Lake": MetaProperties(unity=True)}
    result = check_ontoclean(onto, tags)
    assert any(v.constraint == "unity" and v.severity == "violation" for v in result.violations)


def test_scorecard_folds_ontoclean_violations_into_taxonomy_health():
    onto = _person_under_student()
    clean = score_ontology(onto)  # no tags → no OntoClean penalty
    flagged = score_ontology(onto, ontoclean_tags=TAGS)
    tax_clean = clean.dimension("taxonomy_health")
    tax_flagged = flagged.dimension("taxonomy_health")
    assert tax_flagged.stats["ontoclean_hard_violations"] == 1
    assert tax_flagged.score < tax_clean.score
    assert any(wp.dimension == "taxonomy_health" and "rigid" in wp.message.lower()
               for wp in flagged.weak_points)


def test_metaproperties_round_trip():
    tags = TAGS
    dumped = dump_metaproperties(tags)
    reloaded = load_metaproperties(dumped)
    assert reloaded["Person"].rigid is True
    assert reloaded["Student"].rigid is False


def test_infer_metaproperties_with_fake_backend():
    class _FakeResponse:
        def json(self):
            return {
                "classes": {
                    "Person": {"rigid": True, "carries_identity": True},
                    "Student": {"rigid": False, "dependent": True},
                }
            }

    class _FakeBackend:
        def __init__(self):
            self.calls = []

        def complete(self, **kwargs):
            self.calls.append(kwargs)
            return _FakeResponse()

    backend = _FakeBackend()
    tags = infer_metaproperties(_person_under_student(), backend=backend)
    assert tags["Person"].rigid is True
    assert tags["Student"].rigid is False
    # response_format requested for deterministic JSON
    assert backend.calls[0]["response_format"] == {"type": "json_object"}
    # and the critic flags the violation on the inferred tags
    assert not check_ontoclean(_person_under_student(), tags).ok
