from seocho.ontology import NodeDef, Ontology, P, RelDef
from seocho.ontology_artifacts import (
    ontology_to_approved_artifacts,
    ontology_to_semantic_prompt_context,
)
from seocho.ontology_serialization import (
    ontology_from_jsonld_dict,
    ontology_to_jsonld,
)


def _build_ontology() -> Ontology:
    return Ontology(
        name="contracts",
        package_id="contracts.core",
        version="2.1.0",
        description="Canonical contract ontology",
        nodes={
            "Company": NodeDef(
                properties={"name": P(str, unique=True)},
                aliases=["Issuer"],
            ),
            "Product": NodeDef(properties={"name": P(str, unique=True)}),
        },
        relationships={
            "SELLS": RelDef(
                source="Company",
                target="Product",
                description="Company sells a product",
            )
        },
    )


def test_serialization_helper_round_trips_jsonld_dict() -> None:
    ontology = _build_ontology()

    doc = ontology_to_jsonld(ontology)
    restored = ontology_from_jsonld_dict(Ontology, doc)

    assert restored == ontology
    assert restored.package_id == "contracts.core"


def test_artifact_helpers_produce_runtime_contract_payloads() -> None:
    ontology = _build_ontology()

    artifacts = ontology_to_approved_artifacts(ontology)
    prompt_context = ontology_to_semantic_prompt_context(
        ontology,
        instructions=["Prefer canonical ontology terms."],
    )

    assert artifacts.ontology_candidate is not None
    assert artifacts.shacl_candidate is not None
    assert prompt_context.instructions[0] == "Prefer canonical ontology terms."
    assert "contracts.core" in prompt_context.instructions[1]
