from __future__ import annotations

from seocho import NodeDef, Ontology, P, RelDef
from seocho.query.answering import QueryAnswerSynthesizer, build_evidence_bundle
from seocho.query.evidence_grounding import build_grounded_synthesis_prompt
from seocho.query.executor import GraphQueryExecutor
from seocho.query.planner import DeterministicQueryPlanner
from seocho.query.strategy import QueryStrategy


def _finance_ontology() -> Ontology:
    return Ontology(
        name="finance_benchmark_lpg",
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
        workspace_id="finance_benchmark_test",
    )

    plan = planner.plan("Delta in CBOE Data & Access Solutions rev from 2021-23.")

    assert plan.ok is True
    assert plan.intent_data["intent"] == "financial_metric_delta"
    assert plan.params["workspace_id"] == "finance_benchmark_test"
    assert "MATCH" in plan.cypher


def test_deterministic_query_planner_attaches_schema_hints_to_prompt_and_plan() -> None:
    class RecordingLLM(_FakeLLM):
        def __init__(self) -> None:
            self.system_prompt = ""

        def complete(self, *, system, user, temperature, response_format=None):  # noqa: ANN001
            self.system_prompt = system
            return super().complete(
                system=system,
                user=user,
                temperature=temperature,
                response_format=response_format,
            )

    llm = RecordingLLM()
    planner = DeterministicQueryPlanner(
        ontology=_finance_ontology(),
        llm=llm,
        workspace_id="finance_benchmark_test",
    )

    plan = planner.plan("Delta in CBOE Data & Access Solutions rev from 2021-23.")

    assert "Question-scoped schema hints" in llm.system_prompt
    assert plan.intent_data["schema_hints"]["anchor_label"] == "Company"
    assert "REPORTED" in plan.intent_data["schema_hints"]["relationship_candidates"]


def test_graph_query_executor_returns_canonical_execution_result() -> None:
    planner = DeterministicQueryPlanner(
        ontology=_finance_ontology(),
        llm=_FakeLLM(),
        workspace_id="finance_benchmark_test",
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
    assert bundle["route_profile"]["route_class"] == "R4_GRAPH_JOIN"
    assert bundle["answer_shape"] == "relationship_summary"
    assert bundle["database"] == "kgnormal"
    assert bundle["databases"] == ["kgnormal"]
    assert bundle["support_status"] == "supported"
    assert bundle["slot_fills"]["owner_or_operator"] == "Alex"
    assert bundle["slot_fills"]["target_entity"] == "Seoul Retail"
    assert bundle["slot_fills"]["relation_paths"] == ["MANAGES"]
    assert bundle["selected_triples"][0]["source"] == "Alex"
    assert bundle["selected_triples"][0]["relation"] == "MANAGES"
    assert bundle["selected_triples"][0]["target"] == "Seoul Retail"


def test_grounded_synthesis_prompt_turns_records_and_context_into_fragments() -> None:
    prompt = build_grounded_synthesis_prompt(
        question="What was revenue in 2023?",
        records=[
            {
                "company": "CBOE",
                "metric_name": "Data revenue",
                "year": "2023",
                "value": "$10.0 million",
                "supporting_fact": "CBOE data revenue was $10.0 million in 2023.",
            }
        ],
        vector_context="=== Knowledge graph ===\nCBOE also reported operating margin of 12%.",
        evidence_bundle={
            "focus_slots": ["target_entity", "financial_metric", "period", "supporting_fact"],
            "grounded_slots": ["target_entity", "financial_metric", "period"],
            "missing_slots": ["supporting_fact"],
            "slot_fills": {"target_entity": "CBOE"},
            "support_status": "partial",
        },
    )

    assert prompt["schema_version"] == "grounded_synthesis_prompt.v1"
    assert "Do not answer from model memory" in prompt["system_addendum"]
    assert '"id": "E1"' in prompt["user_addendum"]
    assert "$10.0 million" in prompt["user_addendum"]
    assert "professor_agent" in prompt["optimizer"]["profiles"][0]["agent_id"]
    assert prompt["missing_slots"] == ["supporting_fact"]


def test_query_answer_synthesizer_injects_grounding_contract_into_llm_prompt() -> None:
    class _AnswerResponse:
        text = "CBOE data revenue was $10.0 million in 2023."

    class _RecordingAnswerLLM:
        def __init__(self) -> None:
            self.system = ""
            self.user = ""

        def complete(self, *, system, user, temperature, response_format=None):  # noqa: ANN001
            self.system = system
            self.user = user
            return _AnswerResponse()

    llm = _RecordingAnswerLLM()
    synthesizer = QueryAnswerSynthesizer(
        query_strategy=QueryStrategy(_finance_ontology()),
        llm=llm,
    )

    answer = synthesizer.synthesize(
        "What was CBOE data revenue in 2023?",
        [{"company": "CBOE", "metric_name": "Data revenue", "year": "2023", "value": "$10.0 million"}],
        vector_context="CBOE data revenue was $10.0 million in 2023.",
        evidence_bundle={"grounded_slots": ["target_entity"], "slot_fills": {"target_entity": "CBOE"}},
    )

    assert answer == "CBOE data revenue was $10.0 million in 2023."
    assert "SEOCHO Evidence Grounding Contract" in llm.system
    assert "SEOCHO typed evidence payload" in llm.user
    assert '"source": "structured_record"' in llm.user
