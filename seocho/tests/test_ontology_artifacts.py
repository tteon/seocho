from seocho import ApprovedArtifacts, SemanticPromptContext
from seocho.client import Seocho
from seocho.ontology import NodeDef, Ontology, P, RelDef


def _build_ontology() -> Ontology:
    return Ontology(
        name="finance_graph",
        package_id="finance.core",
        version="1.2.0",
        description="Finance ontology",
        graph_model="lpg",
        namespace="https://example.com/finance/",
        nodes={
            "Company": NodeDef(
                description="Issuer or operating company",
                aliases=["Issuer"],
                properties={
                    "name": P(str, unique=True, description="Legal name", aliases=["issuer_name"]),
                    "ticker": P(str, index=True),
                },
                same_as="schema:Organization",
            ),
            "Metric": NodeDef(
                description="Reported financial metric",
                properties={"name": P(str, unique=True)},
            ),
        },
        relationships={
            "REPORTS": RelDef(
                source="Company",
                target="Metric",
                cardinality="ONE_TO_MANY",
                description="Company reports a metric",
                aliases=["HAS_METRIC"],
            ),
        },
    )


def test_ontology_converts_to_typed_runtime_artifacts() -> None:
    ontology = _build_ontology()

    artifacts = ontology.to_approved_artifacts()
    prompt_context = ontology.to_semantic_prompt_context()
    draft = ontology.to_semantic_artifact_draft()

    assert isinstance(artifacts, ApprovedArtifacts)
    assert artifacts.ontology_candidate is not None
    assert artifacts.shacl_candidate is not None
    assert artifacts.vocabulary_candidate is not None
    assert artifacts.ontology_candidate.ontology_name == "finance_graph"
    assert artifacts.ontology_candidate.classes[0].properties[0].datatype == "string"
    company_term = next(term for term in artifacts.vocabulary_candidate.terms if term.pref_label == "Company")
    assert "Issuer" in company_term.alt_labels
    assert any(term.pref_label == "REPORTS" for term in artifacts.vocabulary_candidate.terms)
    assert any(shape.target_class == "Company" for shape in artifacts.shacl_candidate.shapes)
    assert any(
        constraint.path == "name" and constraint.constraint == "minCount"
        for shape in artifacts.shacl_candidate.shapes
        for constraint in shape.properties
    )
    assert isinstance(prompt_context, SemanticPromptContext)
    assert "finance.core" in prompt_context.instructions[0]
    assert draft.name == "finance.core-1.2.0"
    assert draft.source_summary["graph_model"] == "lpg"
    assert draft.source_summary["node_count"] == 2


def test_client_exposes_ontology_bridge_helpers_without_local_engine() -> None:
    ontology = _build_ontology()
    client = Seocho(ontology=ontology)

    artifacts = client.approved_artifacts_from_ontology()
    prompt_context = client.prompt_context_from_ontology(
        instructions=["Prefer finance ontology labels."]
    )
    draft = client.artifact_draft_from_ontology(name="finance_v1")

    assert artifacts.ontology_candidate is not None
    assert prompt_context.instructions[0] == "Prefer finance ontology labels."
    assert draft.name == "finance_v1"
    assert draft.ontology_candidate.ontology_name == "finance_graph"


def test_local_query_builder_uses_registered_ontology_override() -> None:
    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload
            self.text = ""

        def json(self):
            return self._payload

    class _FakeLLM:
        def complete(self, *, system, user, temperature, response_format=None):  # noqa: ANN001
            return _FakeResponse({"intent": "count", "anchor_label": "Customer"})

    class _FakeGraphStore:
        def get_schema(self, *, database="neo4j"):  # noqa: ANN001
            return {"labels": ["Customer"], "relationship_types": []}

        def query(self, cypher, params=None, database="neo4j"):  # noqa: ANN001
            return [{"count": 1}]

    default_ontology = Ontology(
        name="default_graph",
        nodes={"Person": NodeDef(properties={"name": P(str, unique=True)})},
        relationships={},
    )
    customer_ontology = Ontology(
        name="customer_graph",
        package_id="customer.core",
        nodes={"Customer": NodeDef(properties={"name": P(str, unique=True)})},
        relationships={},
    )
    client = Seocho(
        ontology=default_ontology,
        graph_store=_FakeGraphStore(),
        llm=_FakeLLM(),
    )

    cypher, params, intent_data, error = client._engine._generate_cypher(  # type: ignore[attr-defined]
        "How many customers?",
        customer_ontology,
    )

    assert error is None
    assert params["workspace_id"] == client.workspace_id
    assert intent_data["anchor_label"] == "Customer"
    assert "MATCH (n:Customer)" in cypher
