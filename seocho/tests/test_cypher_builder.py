from __future__ import annotations

from seocho import NodeDef, Ontology, P, RelDef, Seocho
from seocho.query.answering import QueryAnswerSynthesizer
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


def test_builder_normalizes_vehicle_delivery_question_as_financial_metric_lookup() -> None:
    builder = CypherBuilder(_finance_ontology())

    intent = builder.normalize_intent(
        "How many vehicles did Tesla deliver in 2022 vs 2021?",
        {"anchor_entity": "Tesla"},
    )

    assert intent["intent"] == "financial_metric_lookup"
    assert intent["anchor_label"] == "Company"
    assert intent["target_label"] == "FinancialMetric"
    assert intent["years"] == ["2022", "2021"]
    assert "vehicle deliveries" in intent["metric_aliases"]
    assert intent["metric_scope_tokens"] == []


def test_builder_drops_explanatory_scope_tokens_for_gross_margin_question() -> None:
    builder = CypherBuilder(_finance_ontology())

    intent = builder.normalize_intent(
        "What drove NVIDIA's gross margin expansion?",
        {"anchor_entity": "NVIDIA"},
    )

    assert intent["intent"] == "financial_metric_lookup"
    assert "margin" in intent["metric_aliases"]
    assert intent["metric_scope_tokens"] == ["gross"]


def test_builder_normalizes_legal_relationship_lookup_from_question() -> None:
    ontology = Ontology(
        name="legal_graph",
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
            "LegalIssue": NodeDef(properties={"name": P(str, unique=True), "status": P(str)}),
        },
        relationships={"INVOLVED_IN": RelDef(source="Company", target="LegalIssue")},
    )
    builder = CypherBuilder(ontology)

    intent = builder.normalize_intent(
        "What legal issues does Microsoft face?",
        {"anchor_entity": "Microsoft"},
    )

    assert intent["intent"] == "relationship_lookup"
    assert intent["anchor_label"] == "Company"
    assert intent["target_label"] == "LegalIssue"
    assert intent["relationship_type"] == "INVOLVED_IN"


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


def test_local_engine_legal_relationship_answer_lists_issues() -> None:
    ontology = Ontology(
        name="legal_graph",
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
            "LegalIssue": NodeDef(properties={"name": P(str, unique=True), "status": P(str)}),
        },
        relationships={"INVOLVED_IN": RelDef(source="Company", target="LegalIssue")},
    )

    class LegalLLM:
        def complete(self, *, system, user, temperature, response_format=None):  # noqa: ANN001
            return _FakeLLMResponse({"anchor_entity": "Microsoft"})

    class LegalGraphStore:
        def get_schema(self, *, database: str = "neo4j") -> dict:
            return {"labels": ["Company", "LegalIssue"], "relationship_types": ["INVOLVED_IN"]}

        def query(self, cypher: str, *, params=None, database: str = "neo4j"):  # noqa: ANN001
            return [
                {
                    "source": "Microsoft",
                    "relationship": "INVOLVED_IN",
                    "target": "an EU antitrust investigation into Teams bundling with Office 365",
                    "target_labels": ["LegalIssue"],
                    "target_properties": {"status": "open"},
                    "supporting_fact": "",
                },
                {
                    "source": "Microsoft",
                    "relationship": "INVOLVED_IN",
                    "target": "ongoing LinkedIn acquisition litigation",
                    "target_labels": ["LegalIssue"],
                    "target_properties": {"status": "open"},
                    "supporting_fact": "",
                },
                {
                    "source": "Microsoft",
                    "relationship": "INVOLVED_IN",
                    "target": "various patent infringement claims",
                    "target_labels": ["LegalIssue"],
                    "target_properties": {"status": "open"},
                    "supporting_fact": "",
                },
            ]

    client = Seocho(
        ontology=ontology,
        graph_store=LegalGraphStore(),
        llm=LegalLLM(),
        workspace_id="finance_benchmark_test",
    )

    answer = client.ask("What legal issues does Microsoft face?", database="neo4j")

    assert "Microsoft faces" in answer
    assert "Teams bundling with Office 365" in answer
    assert "LinkedIn acquisition litigation" in answer
    assert "patent infringement claims" in answer


def test_local_engine_legal_neighbors_answer_keeps_specific_issue_sentences() -> None:
    ontology = Ontology(
        name="legal_graph",
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
            "LegalIssue": NodeDef(properties={"name": P(str, unique=True), "status": P(str)}),
        },
        relationships={"INVOLVED_IN": RelDef(source="Company", target="LegalIssue")},
    )
    supporting_fact = (
        "Microsoft Corporation faces various legal proceedings and claims. "
        "In June 2022, the European Commission opened an antitrust investigation into Microsoft's bundling of Teams with Office 365. "
        "The company also faces ongoing litigation related to the LinkedIn acquisition and various patent infringement claims."
    )

    class LegalNeighborsLLM:
        def complete(self, *, system, user, temperature, response_format=None):  # noqa: ANN001
            return _FakeLLMResponse(
                {
                    "intent": "neighbors",
                    "anchor_entity": "Microsoft",
                    "anchor_label": "Company",
                }
            )

    class LegalNeighborsGraphStore:
        def get_schema(self, *, database: str = "neo4j") -> dict:
            return {"labels": ["Company", "LegalIssue"], "relationship_types": ["INVOLVED_IN"]}

        def query(self, cypher: str, *, params=None, database: str = "neo4j"):  # noqa: ANN001
            return [
                {
                    "entity": "Microsoft",
                    "properties": {"name": "Microsoft", "content_preview": supporting_fact},
                    "neighbors": [],
                    "supporting_fact": supporting_fact,
                }
            ]

    client = Seocho(
        ontology=ontology,
        graph_store=LegalNeighborsGraphStore(),
        llm=LegalNeighborsLLM(),
        workspace_id="finance_benchmark_test",
    )

    answer = client.ask("What legal issues does Microsoft face?", database="neo4j")

    assert "antitrust investigation into Microsoft's bundling of Teams with Office 365" in answer
    assert "LinkedIn acquisition" in answer
    assert "patent infringement claims" in answer


def test_local_engine_financial_lookup_compares_multiple_years_without_currency_for_counts() -> None:
    class DeliveryLLM:
        def complete(self, *, system, user, temperature, response_format=None):  # noqa: ANN001
            return _FakeLLMResponse(
                {
                    "anchor_entity": "Tesla",
                }
            )

    class DeliveryGraphStore:
        def get_schema(self, *, database: str = "neo4j") -> dict:
            return {"labels": ["Company", "FinancialMetric"], "relationship_types": ["REPORTED"]}

        def query(self, cypher: str, *, params=None, database: str = "neo4j"):  # noqa: ANN001
            return [
                {
                    "company": "Tesla",
                    "metric_name": "Vehicle Deliveries 2022",
                    "year": "2022",
                    "value": "1310000",
                    "relationship": "REPORTED",
                    "supporting_fact": (
                        "Tesla Inc. recorded automotive revenue of $71.5 billion in 2022, "
                        "up from $47.2 billion in 2021. The company delivered 1.31 million "
                        "vehicles in 2022 compared to 936,000 in the prior year."
                    ),
                },
                {
                    "company": "Tesla",
                    "metric_name": "Vehicle Deliveries 2021",
                    "year": "2021",
                    "value": "936000",
                    "relationship": "REPORTED",
                    "supporting_fact": (
                        "Tesla Inc. recorded automotive revenue of $71.5 billion in 2022, "
                        "up from $47.2 billion in 2021. The company delivered 1.31 million "
                        "vehicles in 2022 compared to 936,000 in the prior year."
                    ),
                },
            ]

    client = Seocho(
        ontology=_finance_ontology(),
        graph_store=DeliveryGraphStore(),
        llm=DeliveryLLM(),
        workspace_id="finance_benchmark_test",
    )

    answer = client.ask("How many vehicles did Tesla deliver in 2022 vs 2021?", database="neo4j")

    assert "1,310,000 in 2022" in answer
    assert "936,000 in 2021" in answer
    assert "$" not in answer


def test_local_engine_financial_lookup_explains_nvidia_gross_margin_expansion() -> None:
    class GrossMarginLLM:
        def complete(self, *, system, user, temperature, response_format=None):  # noqa: ANN001
            return _FakeLLMResponse(
                {
                    "anchor_entity": "NVIDIA",
                }
            )

    class GrossMarginGraphStore:
        def get_schema(self, *, database: str = "neo4j") -> dict:
            return {"labels": ["Company", "FinancialMetric"], "relationship_types": ["REPORTED"]}

        def query(self, cypher: str, *, params=None, database: str = "neo4j"):  # noqa: ANN001
            return [
                {
                    "company": "NVIDIA",
                    "metric_name": "Gross Margin 2023",
                    "year": "2023",
                    "value": "72.7%",
                    "relationship": "REPORTED",
                    "supporting_fact": (
                        "The company's gross margin expanded to 72.7% from 56.9%, driven by strong demand "
                        "for AI accelerator chips including the H100 and A100 product lines."
                    ),
                }
            ]

    client = Seocho(
        ontology=_finance_ontology(),
        graph_store=GrossMarginGraphStore(),
        llm=GrossMarginLLM(),
        workspace_id="finance_benchmark_test",
    )

    answer = client.ask("What drove NVIDIA's gross margin expansion?", database="neo4j")

    assert "72.7%" in answer
    assert "56.9%" in answer
    assert "H100 and A100 product lines" in answer


def test_financial_answer_rewrites_prior_year_using_question_years() -> None:
    synthesizer = QueryAnswerSynthesizer(query_strategy=object(), llm=object())

    answer = synthesizer._normalize_relative_year_references(
        "The company delivered 1.31 million vehicles in 2022 compared to 936,000 in the prior year.",
        ["2021", "2022"],
    )

    assert "prior year" not in answer
    assert "936,000 in 2021" in answer


def test_financial_answer_handles_ladybug_column_alias_fallbacks() -> None:
    synthesizer = QueryAnswerSynthesizer(query_strategy=object(), llm=object())

    answer = synthesizer.build_deterministic_answer(
        "How many vehicles did Tesla deliver in 2022 vs 2021?",
        [
            {
                "col_0": "Tesla Inc.",
                "col_1": "Vehicles Delivered 2021",
                "col_2": "2021",
                "col_3": "936000",
                "col_4": "REPORTED",
                "col_5": (
                    "Tesla Inc. recorded automotive revenue of $71.5 billion in 2022, "
                    "up from $47.2 billion in 2021. The company delivered 1.31 million "
                    "vehicles in 2022 compared to 936,000 in the prior year."
                ),
            },
            {
                "col_0": "Tesla Inc.",
                "col_1": "Vehicles Delivered 2022",
                "col_2": "2022",
                "col_3": "1310000",
                "col_4": "REPORTED",
                "col_5": (
                    "Tesla Inc. recorded automotive revenue of $71.5 billion in 2022, "
                    "up from $47.2 billion in 2021. The company delivered 1.31 million "
                    "vehicles in 2022 compared to 936,000 in the prior year."
                ),
            },
        ],
        {
            "intent": "financial_metric_delta",
            "anchor_entity": "Tesla",
            "metric_name": "vehicles delivered",
            "metric_aliases": ["vehicle deliveries", "deliveries"],
            "years": ["2021", "2022"],
        },
    )

    assert answer is not None
    assert "1,310,000 in 2022" in answer
    assert "936,000 in 2021" in answer


def test_financial_delta_answer_humanizes_large_currency_values() -> None:
    synthesizer = QueryAnswerSynthesizer(query_strategy=object(), llm=object())

    answer = synthesizer.build_deterministic_answer(
        "What was JPMorgan's net interest income growth?",
        [
            {
                "col_0": "JPMorgan Chase & Co.",
                "col_1": "Net Interest Income 2022",
                "col_2": "2022",
                "col_3": "66700000000",
                "col_4": "REPORTED",
            },
            {
                "col_0": "JPMorgan Chase & Co.",
                "col_1": "Net Interest Income 2023",
                "col_2": "2023",
                "col_3": "87100000000",
                "col_4": "REPORTED",
            },
        ],
        {
            "intent": "financial_metric_delta",
            "anchor_entity": "JPMorgan",
            "metric_name": "net interest income",
            "metric_aliases": ["net interest income", "income"],
            "years": ["2022", "2023"],
        },
    )

    assert answer is not None
    assert "$87.1 billion" in answer
    assert "$66.7 billion" in answer
    assert "$20.4 billion" in answer
