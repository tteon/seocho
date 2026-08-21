import json

from seocho.ontology import NodeDef, Ontology, P, RelDef, build_rdf_ontology_bundle
from seocho.ontology.module_scorecard import ModuleQualityPolicy, decide_module_quality, score_module


def test_module_scorecard_exposes_interface_and_agent_gate():
    ontology = Ontology(
        name="m",
        nodes={
            "Person": NodeDef(properties={"name": P(str)}),
            "Company": NodeDef(properties={"name": P(str)}),
        },
        relationships={"CEO_OF": RelDef(source="Person", target="Company")},
    )
    score = score_module(
        ontology, module_id="people", class_names=["Person"], required_relations=["CEO_OF"]
    )
    decision = decide_module_quality(score, policy=ModuleQualityPolicy(max_coupling=0.1))
    assert score.coupling > 0 and score.interface_complete
    assert decision.disposition == "needs_reasoning"
    assert decision.additional_verification_calls > 0


def test_bundle_persists_profile_quality_metadata(tmp_path):
    ontology = Ontology(
        name="m",
        nodes={"Person": NodeDef(), "Company": NodeDef()},
        relationships={"CEO_OF": RelDef(source="Person", target="Company")},
    )
    bundle = build_rdf_ontology_bundle(
        ontology,
        tmp_path / "bundle",
        module_specs={
            "query": {
                "module_id": "people",
                "class_names": ["Person"],
                "required_relations": ["CEO_OF"],
                "quality_policy": {"max_coupling": 0.1},
            }
        },
    )
    query_profile = json.loads((bundle.agent_profiles_dir / "query.json").read_text())
    quality = query_profile["module_quality"]
    assert quality["scorecard"]["module_id"] == "people"
    assert quality["decision"]["disposition"] == "needs_reasoning"
