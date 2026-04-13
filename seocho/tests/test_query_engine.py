from __future__ import annotations

from seocho import NodeDef, Ontology, P, RelDef
from seocho.query.answering import build_evidence_bundle
from seocho.query.executor import GraphQueryExecutor
from seocho.query.planner import DeterministicQueryPlanner


def _finance_ontology() -> Ontology:
    return Ontology(
        name="finder_lpg",
        graph_model="lpg",
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
            "FinancialMetric": NodeDef(properties={"name": P(str), "value": P(str), "year": P(str)}),
        },
        relationships={
            "REPORTED": RelDef(source="Company", target="FinancialMetric"),
        },
    )


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
    def query(self, cypher: str, *, params=None, database: str = "neo4j"):  # noqa: ANN001
        return [{"company": "CBOE", "metric_name": "Revenue 2023", "year": "2023", "value": "10.0"}]


def test_deterministic_query_planner_returns_canonical_query_plan() -> None:
    planner = DeterministicQueryPlanner(
        ontology=_finance_ontology(),
        llm=_FakeLLM(),
        workspace_id="finder_test",
    )

    plan = planner.plan("Delta in CBOE Data & Access Solutions rev from 2021-23.")

    assert plan.ok is True
    assert plan.intent_data["intent"] == "financial_metric_delta"
    assert plan.params["workspace_id"] == "finder_test"
    assert "MATCH" in plan.cypher


def test_graph_query_executor_returns_canonical_execution_result() -> None:
    planner = DeterministicQueryPlanner(
        ontology=_finance_ontology(),
        llm=_FakeLLM(),
        workspace_id="finder_test",
    )
    plan = planner.plan("Delta in CBOE Data & Access Solutions rev from 2021-23.")

    executor = GraphQueryExecutor(graph_store=_FakeGraphStore(), database="neo4j")
    execution = executor.execute(plan)

    assert execution.ok is True
    assert execution.records[0]["company"] == "CBOE"


def test_build_evidence_bundle_shared_contract() -> None:
    bundle = build_evidence_bundle(
        question="Who manages Seoul retail?",
        semantic_context={
            "intent": {
                "intent_id": "responsibility_lookup",
                "required_relations": ["MANAGES"],
                "required_entity_types": ["Person", "Organization"],
                "focus_slots": ["owner_or_operator", "target_entity", "supporting_fact"],
            },
            "matches": {
                "Seoul retail": [
                    {
                        "display_name": "Seoul Retail",
                        "database": "kgnormal",
                        "node_id": "777",
                        "labels": ["Account"],
                        "source": "fulltext",
                        "final_score": 0.91,
                    }
                ]
            },
        },
        memory={
            "memory_id": "mem_123",
            "database": "kgnormal",
            "content_preview": "Alex manages Seoul Retail.",
            "entities": [{"name": "Alex", "labels": ["Person"]}],
        },
        matched_entities=["Seoul Retail"],
        reasons=["entity_match"],
        score=0.91,
    )

    assert bundle["schema_version"] == "evidence_bundle.v2"
    assert bundle["intent_id"] == "responsibility_lookup"
    assert bundle["slot_fills"]["owner_or_operator"] == "Alex"
    assert bundle["slot_fills"]["target_entity"] == "Seoul Retail"
