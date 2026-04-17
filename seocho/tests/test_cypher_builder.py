from __future__ import annotations

from seocho import NodeDef, Ontology, P, RelDef, Seocho
from seocho.query.cypher_builder import CypherBuilder


def _finance_ontology(graph_model: str = "lpg") -> Ontology:
    relationships = {
        "REPORTED": RelDef(source="Company", target="FinancialMetric", description="Company reported metric"),
    }
    namespace = ""
    if graph_model == "rdf":
        namespace = "https://seocho.dev/fibo/"
        relationships = {
            "reported": RelDef(
                source="Company",
                target="FinancialMetric",
                description="Company reported metric",
                same_as="fibo:hasReportedMetric",
            )
        }
    return Ontology(
        name=f"finance_benchmark_{graph_model}",
        graph_model=graph_model,
        namespace=namespace,
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
            "FinancialMetric": NodeDef(properties={"name": P(str), "value": P(str), "year": P(str)}),
        },
        relationships=relationships,
    )


def test_builder_normalizes_finance_delta_intent_from_question() -> None:
    builder = CypherBuilder(_finance_ontology())

    intent = builder.normalize_intent(
        "Delta in CBOE Data & Access Solutions rev from 2021-23.",
        {"anchor_entity": "CBOE"},
    )

    assert intent["intent"] == "financial_metric_delta"
    assert intent["anchor_label"] == "Company"
    assert intent["target_label"] == "FinancialMetric"
    assert intent["years"] == ["2021", "2023"]
    assert "revenue" in intent["metric_aliases"]
    assert set(intent["metric_scope_tokens"]) >= {"data", "access", "solutions"}


def test_builder_financial_metric_query_uses_workspace_and_rel_candidates() -> None:
    builder = CypherBuilder(_finance_ontology("rdf"))

    cypher, params = builder.build(
        intent="financial_metric_delta",
        anchor_entity="CBOE",
        anchor_label="Company",
        target_label="FinancialMetric",
        metric_name="Data & Access Solutions revenue",
        metric_aliases=["revenue", "revenues", "rev"],
        metric_scope_tokens=["data", "access", "solutions"],
        years=["2021", "2023"],
        workspace_id="finance_benchmark_test",
    )

    assert "relationship_candidates" in params
    assert "reported" in params["relationship_candidates"]
    assert "HASREPORTEDMETRIC" in {value.upper() for value in params["relationship_candidates"]}
    assert params["workspace_id"] == "finance_benchmark_test"
    assert "metric_scope_tokens" in params
    assert "coalesce(c._workspace_id, '') = $workspace_id" in cypher


def test_builder_relationship_lookup_returns_target_properties_and_supporting_fact() -> None:
    ontology = Ontology(
        name="company_graph",
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
            "Person": NodeDef(properties={"name": P(str, unique=True), "title": P(str)}),
        },
        relationships={"EMPLOYS": RelDef(source="Company", target="Person")},
    )
    builder = CypherBuilder(ontology)

    cypher, _ = builder.build(
        intent="relationship_lookup",
        anchor_entity="Alphabet",
        anchor_label="Company",
        target_label="Person",
        relationship_type="EMPLOYS",
        workspace_id="acme",
    )

    assert "target_properties" in cypher
    assert "supporting_fact" in cypher


class _FakeLLMResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.text = ""

    def json(self) -> dict:
        return dict(self._payload)


class _FakeLLM:
    def complete(self, *, system, user, temperature, response_format=None):  # noqa: ANN001
        return _FakeLLMResponse(
            {
                "intent": "financial_metric_delta",
                "anchor_entity": "CBOE",
                "anchor_label": "Company",
                "metric_name": "Data & Access Solutions revenue",
                "years": ["2021", "2023"],
            }
        )


class _FakeGraphStore:
    def __init__(self) -> None:
        self.calls = []

    def get_schema(self, *, database: str = "neo4j") -> dict:
        return {"labels": ["Company", "FinancialMetric"], "relationship_types": ["REPORTED", "reported"]}

    def query(self, cypher: str, *, params=None, database: str = "neo4j"):  # noqa: ANN001
        self.calls.append({"cypher": cypher, "params": dict(params or {}), "database": database})
        return [
            {
                "company": "Cboe Global Markets, Inc. and Subsidiaries",
                "metric_name": "Data and access solutions revenue 2021",
                "year": "2021",
                "value": "427.7",
                "relationship": "reported",
            },
            {
                "company": "Cboe Global Markets, Inc. and Subsidiaries",
                "metric_name": "Data and access solutions revenue 2023",
                "year": "2023",
                "value": "539.2",
                "relationship": "reported",
            },
        ]


def test_local_engine_finance_delta_returns_deterministic_answer() -> None:
    client = Seocho(
        ontology=_finance_ontology(),
        graph_store=_FakeGraphStore(),
        llm=_FakeLLM(),
        workspace_id="finance_benchmark_test",
    )

    answer = client.ask(
        "Delta in CBOE Data & Access Solutions rev from 2021-23.",
        database="neo4j",
        reasoning_mode=True,
        repair_budget=1,
    )

    assert "111.5" in answer
    assert "2021" in answer
    assert "2023" in answer
    assert "Cboe Global Markets, Inc. and Subsidiaries" in answer


def test_local_engine_relationship_answer_includes_titles_from_target_properties() -> None:
    ontology = Ontology(
        name="company_graph",
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
            "Person": NodeDef(properties={"name": P(str, unique=True), "title": P(str)}),
        },
        relationships={"EMPLOYS": RelDef(source="Company", target="Person")},
    )

    class RelationshipLLM:
        def complete(self, *, system, user, temperature, response_format=None):  # noqa: ANN001
            return _FakeLLMResponse(
                {
                    "intent": "relationship_lookup",
                    "anchor_entity": "Alphabet",
                    "anchor_label": "Company",
                    "target_label": "Person",
                    "relationship_type": "EMPLOYS",
                }
            )

    class RelationshipGraphStore:
        def get_schema(self, *, database: str = "neo4j") -> dict:
            return {"labels": ["Company", "Person"], "relationship_types": ["EMPLOYS"]}

        def query(self, cypher: str, *, params=None, database: str = "neo4j"):  # noqa: ANN001
            return [
                {
                    "source": "Alphabet Inc.",
                    "relationship": "EMPLOYS",
                    "target": "Sundar Pichai",
                    "target_labels": ["Person"],
                    "target_properties": {"title": "CEO"},
                    "supporting_fact": "",
                },
                {
                    "source": "Alphabet Inc.",
                    "relationship": "EMPLOYS",
                    "target": "Ruth Porat",
                    "target_labels": ["Person"],
                    "target_properties": {"title": "CFO"},
                    "supporting_fact": "",
                },
            ]

    client = Seocho(
        ontology=ontology,
        graph_store=RelationshipGraphStore(),
        llm=RelationshipLLM(),
        workspace_id="finance_benchmark_test",
    )

    answer = client.ask("Who are the key executives at Alphabet?", database="neo4j")

    assert "Sundar Pichai as CEO" in answer
    assert "Ruth Porat as CFO" in answer
