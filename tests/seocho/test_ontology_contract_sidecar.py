"""The OS-contract sidecar (ADR-0181): what an ontology file cannot carry.

`from_ttl` consults OWL.Class/Ontology/versionIRI/versionInfo,
RDFS.Class/subClassOf/label/comment and SKOS.altLabel/definition — and nothing
else. `owl:hasKey` and `owl:oneOf` appear nowhere in this package, so even the
two needs OWL *could* express are unreachable. A Turtle class therefore arrives
with no properties at all, which is why the contract can declare them: an
identity key must name a property that exists.

The measured stakes, from a 322-document run and a 2x2 A/B on the same corpus:
a `P(str)` status produced eight spellings including case splits, and declaring
the vocabulary drove off-vocabulary values from 51 to 0 while SUPERSEDES edges
rose from 7 to 17.
"""

from __future__ import annotations

import pytest
import yaml

from seocho import NodeDef, Ontology, P

_TTL = """@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix ex: <http://example.org/> .
ex: a owl:Ontology ; owl:versionInfo "1.0.0" .
ex:Person a owl:Class ; rdfs:label "Person" .
ex:Decision a owl:Class ; rdfs:label "Decision" .
"""

_CONTRACT = {
    "seocho_contract": 1,
    "purpose": "Recover which decision is currently in force.",
    "competency_questions": [
        {"id": "cq1", "ask": "Which value is applied now?",
         "requires": ["Decision.status"]}
    ],
    "modelling_decisions": ["SUPERSEDES runs newer -> older."],
    "properties": {"Person": {"name": {"unique": True}}},
    "identity": {"Person": ["name"]},
    "vocabularies": {"Decision.status": ["proposed", "applied", "superseded"]},
}


@pytest.fixture
def ttl_with_contract(tmp_path):
    ttl = tmp_path / "decisions.ttl"
    ttl.write_text(_TTL, encoding="utf-8")
    (tmp_path / "decisions.seocho.yaml").write_text(
        yaml.safe_dump(_CONTRACT), encoding="utf-8")
    return ttl


def test_a_sidecar_beside_the_file_is_applied_automatically(ttl_with_contract):
    ontology = Ontology.load(ttl_with_contract)
    assert "currently in force" in ontology.description
    assert ontology.annotations["modelling_decisions"]
    assert ontology.nodes["Person"].identity_keys == ["name"]


def test_a_file_without_a_sidecar_loads_unchanged(tmp_path):
    """Optional: the absence of a contract is not an error."""
    ttl = tmp_path / "bare.ttl"
    ttl.write_text(_TTL, encoding="utf-8")
    ontology = Ontology.load(ttl)
    assert ontology.annotations == {}
    assert ontology.nodes["Person"].identity_keys == []


def test_the_contract_can_declare_properties_a_ttl_does_not_carry(ttl_with_contract):
    """from_ttl yields classes with zero properties, so identity needs this."""
    assert Ontology.from_ttl(ttl_with_contract).nodes["Person"].properties == {}
    ontology = Ontology.load(ttl_with_contract)
    assert ontology.nodes["Person"].properties["name"].unique is True


def test_an_identity_key_naming_a_missing_property_is_rejected(tmp_path):
    """Silently ignoring it would leave ids document-local with no signal."""
    ttl = tmp_path / "x.ttl"
    ttl.write_text(_TTL, encoding="utf-8")
    (tmp_path / "x.seocho.yaml").write_text(
        yaml.safe_dump({"seocho_contract": 1, "identity": {"Person": ["ssn"]}}),
        encoding="utf-8")
    with pytest.raises(ValueError, match="not.*properties of that class"):
        Ontology.load(ttl)


def test_identity_for_an_unknown_class_is_rejected(tmp_path):
    ttl = tmp_path / "x.ttl"
    ttl.write_text(_TTL, encoding="utf-8")
    (tmp_path / "x.seocho.yaml").write_text(
        yaml.safe_dump({"seocho_contract": 1, "identity": {"Ghost": ["name"]}}),
        encoding="utf-8")
    with pytest.raises(ValueError, match="not in the ontology"):
        Ontology.load(ttl)


def test_a_newer_contract_version_is_refused_not_half_applied(tmp_path):
    ttl = tmp_path / "x.ttl"
    ttl.write_text(_TTL, encoding="utf-8")
    (tmp_path / "x.seocho.yaml").write_text(
        yaml.safe_dump({"seocho_contract": 99}), encoding="utf-8")
    with pytest.raises(ValueError, match="newer than this SDK"):
        Ontology.load(ttl)


def test_unknown_keys_are_ignored_so_an_older_sdk_can_read_a_newer_contract(tmp_path):
    ttl = tmp_path / "x.ttl"
    ttl.write_text(_TTL, encoding="utf-8")
    (tmp_path / "x.seocho.yaml").write_text(
        yaml.safe_dump({"seocho_contract": 1, "purpose": "p",
                        "some_future_key": {"a": 1}}), encoding="utf-8")
    assert Ontology.load(ttl).description == "p"


def test_only_the_question_text_reaches_the_prompt(ttl_with_contract):
    """Rendering the dict put `{'id': 'cq1', 'ask': ...}` in front of the model."""
    ontology = Ontology.load(ttl_with_contract)
    context = ontology.to_extraction_context()
    assert "Which value is applied now?" in context["competency_questions"]
    assert "'id'" not in context["competency_questions"]


def test_a_bare_string_competency_question_still_works():
    ontology = Ontology(
        name="s", nodes={"A": NodeDef(properties={"n": P(str)})},
        annotations={"competency_questions": ["Who owns what?"]})
    assert "Who owns what?" in ontology.to_extraction_context()["competency_questions"]
