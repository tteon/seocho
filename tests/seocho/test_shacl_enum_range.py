"""Regression for #128 — enum and range constraints declared on an ontology
must be enforced by validate_with_shacl (the indexing validation path), and
survive serialization. Previously to_shacl emitted only datatype/cardinality
and validate_with_shacl ignored enum/range entirely.
"""

from __future__ import annotations

from seocho.ontology import NodeDef, Ontology, P


def _onto() -> Ontology:
    return Ontology(
        name="t",
        nodes={
            "Rating": NodeDef(
                properties={
                    "stance": P(str, enum=["buy", "hold", "sell"]),
                    "score": P(float, value_range=(0.0, 1.0)),
                }
            )
        },
    )


def test_to_shacl_emits_in_and_range():
    shape = _onto().to_shacl()["shapes"][0]
    by_path = {ps["path"]: ps for ps in shape["properties"]}
    assert by_path["seocho:stance"]["in"] == ["buy", "hold", "sell"]
    assert by_path["seocho:score"]["minInclusive"] == 0.0
    assert by_path["seocho:score"]["maxInclusive"] == 1.0


def test_enum_violation_is_a_validation_error():
    data = {
        "nodes": [{"id": "r1", "label": "Rating", "properties": {"stance": "strong-buy"}}],
        "relationships": [],
    }
    errors = _onto().validate_with_shacl(data)
    assert any("not in allowed set" in e and "stance" in e for e in errors)


def test_enum_member_passes():
    data = {
        "nodes": [{"id": "r1", "label": "Rating", "properties": {"stance": "buy"}}],
        "relationships": [],
    }
    errors = _onto().validate_with_shacl(data)
    assert not any("stance" in e for e in errors)


def test_range_violation_is_a_validation_error():
    data = {
        "nodes": [{"id": "r1", "label": "Rating", "properties": {"score": 1.5}}],
        "relationships": [],
    }
    errors = _onto().validate_with_shacl(data)
    assert any("out of range" in e and "score" in e for e in errors)


def test_range_member_passes():
    data = {
        "nodes": [{"id": "r1", "label": "Rating", "properties": {"score": 0.5}}],
        "relationships": [],
    }
    errors = _onto().validate_with_shacl(data)
    assert not any("score" in e for e in errors)


def test_enum_and_range_survive_dict_roundtrip():
    restored = Ontology.from_dict(_onto().to_dict())
    stance = restored.nodes["Rating"].properties["stance"]
    score = restored.nodes["Rating"].properties["score"]
    assert stance.enum == ["buy", "hold", "sell"]
    assert score.value_range == (0.0, 1.0)
