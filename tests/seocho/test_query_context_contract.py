"""Public ask contracts with real routing/planning/answering and I/O doubles.

These tests establish orchestration parity, not live DB or LLM quality/performance.
"""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

import pytest

from seocho import NodeDef, Ontology, P, RelDef, Seocho
from seocho.store.llm import LLMResponse


CONTEXT = {"role": "contract-test analyst", "focus": ["evidence gaps"]}
EMPTY_CONTEXTS = [
    None,
    {},
    {"role": "", "focus": []},
    {"role": " \t\n"},
    {"focus": [None, "", " \t"], "constraints": {"note": None}},
]
QUESTION = "What was Apple Inc.'s total revenue for fiscal year 2024?"
FINANCE_ROWS = [
    {
        "company": "Apple Inc.",
        "metric_name": "revenue",
        "year": "2024",
        "value": 391035000000.0,
    },
    {
        "company": "Other company",
        "metric_name": "revenue",
        "year": "2024",
        "value": 1000000.0,
    },
]
OBSERVATION_ROWS = [
    {"value": 391035000000.0, "unit": "USD", "period": "fiscal:2024:FY"},
]


class RecordingLLM:
    def __init__(self, *, fail_synthesis: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_synthesis = fail_synthesis

    def complete(self, *, system: str, user: str, **kwargs: Any) -> LLMResponse:
        self.calls.append(deepcopy({"system": system, "user": user, **kwargs}))
        if kwargs.get("task_hint") == "answer_synthesis":
            if self.fail_synthesis:
                raise RuntimeError("reframing unavailable")
            return LLMResponse(
                text="Final answer",
                model="contract-test",
                usage={
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "total_tokens": 110,
                },
            )
        if kwargs.get("task_hint") == "intent_classification":
            return LLMResponse(
                text='{"intent":"financial_metric_lookup",'
                '"anchor_entity":"Apple Inc.","anchor_label":"Company",'
                '"metric_name":"revenue","years":["2024"]}',
                model="contract-test",
            )
        return LLMResponse(
            text='{"intent":"metric_lookup",'
            '"metric_surface":"total revenue","entity_surface":"Apple Inc.",'
            '"period":"FY2024"}',
            model="contract-test",
        )


class RecordingGraph:
    def __init__(
        self,
        *,
        periods: tuple[str, ...] = ("fiscal:2024:FY",),
        empty_observation: bool = False,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.periods = periods
        self.empty_observation = empty_observation

    def get_schema(self, *, database: str) -> dict[str, Any]:
        self.calls.append({"schema": database})
        return {
            "labels": ["Company", "FinancialMetric"],
            "relationship_types": ["REPORTED"],
        }

    def query(
        self, cypher: str, *, params: Any = None, database: str = "neo4j", **kwargs: Any
    ) -> list[dict[str, Any]]:
        if "raw_context_hashes" in cypher:
            rows = [
                {
                    "raw_context_hashes": [],
                    "scoped_nodes": 0,
                    "missing_context_nodes": 0,
                }
            ]
        elif "collect(DISTINCT" in cypher:
            rows = [{"periods": list(self.periods)}]
        elif "Observation" in cypher:
            rows = [] if self.empty_observation else OBSERVATION_ROWS
        else:
            rows = FINANCE_ROWS
        self.calls.append(
            deepcopy(
                {
                    "cypher": cypher,
                    "params": params,
                    "database": database,
                    "rows": rows,
                    **kwargs,
                }
            )
        )
        return deepcopy(rows)


@pytest.fixture(autouse=True)
def controlled_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "SEOCHO_ROUTE_PROFILE",
        "SEOCHO_MULTI_PLAN",
        "SEOCHO_PLAN_GATE",
        "SEOCHO_CHUNK_FALLBACK",
        "SEOCHO_DETERMINISTIC_FINANCIAL",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SEOCHO_SEMANTIC_LAYER", "0")
    monkeypatch.setenv("SEOCHO_VERIFIED_FINANCIAL_ANSWER", "1")
    monkeypatch.setenv("SEOCHO_TRACE_BACKEND", "none")


def client_pair(**graph_kwargs: Any) -> tuple[Seocho, RecordingGraph, RecordingLLM]:
    graph = RecordingGraph(**graph_kwargs)
    llm = RecordingLLM()
    ontology = Ontology(
        name="finance",
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
            "FinancialMetric": NodeDef(
                properties={"name": P(str), "value": P(str), "year": P(str)}
            ),
        },
        relationships={"REPORTED": RelDef(source="Company", target="FinancialMetric")},
    )
    client = Seocho(
        ontology=ontology, graph_store=graph, llm=llm, workspace_id="context-contract"
    )
    return client, graph, llm


def test_semantic_fast_path_preserves_retrieval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEOCHO_SEMANTIC_LAYER", "1")
    baseline, graph, llm = client_pair()
    contextual, context_graph, context_llm = client_pair()
    answer = baseline.ask(QUESTION)
    contextual.ask(QUESTION, query_context=CONTEXT)

    assert answer == "$391,035 million (fiscal:2024:FY)"
    assert context_graph.calls == graph.calls
    assert len(graph.calls) == 2  # real arbiter probe + exact-key lookup
    assert context_llm.calls[:-1] == llm.calls
    assert contextual._engine._last_semantic_route == "STRUCTURED"
    final_call = context_llm.calls[-1]
    assert final_call["task_hint"] == "answer_synthesis"
    assert answer in final_call["user"]
    assert json.dumps(OBSERVATION_ROWS) in final_call["user"]
    assert "contract-test analyst" in final_call["system"]


@pytest.mark.parametrize("deterministic", [True, False])
def test_pipeline_preserves_plan_and_rows(
    monkeypatch: pytest.MonkeyPatch,
    deterministic: bool,
) -> None:
    monkeypatch.setenv("SEOCHO_VERIFIED_FINANCIAL_ANSWER", str(int(deterministic)))
    baseline, graph, llm = client_pair()
    contextual, context_graph, context_llm = client_pair()
    answer = baseline.ask(QUESTION)
    contextual.ask(QUESTION, query_context=CONTEXT)

    assert context_graph.calls == graph.calls
    for key in ("cypher", "params", "result_count", "intent_data", "evidence_bundle"):
        assert contextual.last_query_metadata[key] == baseline.last_query_metadata[key]

    def before_synthesis(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [c for c in calls if c.get("task_hint") != "answer_synthesis"]

    assert before_synthesis(context_llm.calls) == before_synthesis(llm.calls)
    final_call = context_llm.calls[-1]
    assert "contract-test analyst" in final_call["system"]
    assert json.dumps(FINANCE_ROWS) in final_call["user"]
    if deterministic:
        assert (
            baseline.last_query_metadata["answer_envelope"]["answer_source"]
            == "deterministic"
        )
        assert answer in final_call["user"]
        assert (
            contextual.last_query_metadata["answer_envelope"]["answer_source"]
            == "llm_synthesis"
        )
        assert len(context_llm.calls) == len(llm.calls) + 1
    else:
        assert len(context_llm.calls) == len(llm.calls)
        assert final_call["user"] == llm.calls[-1]["user"]


@pytest.mark.parametrize("context", EMPTY_CONTEXTS)
@pytest.mark.parametrize("semantic", [True, False])
def test_empty_context_is_complete_noop(
    monkeypatch: pytest.MonkeyPatch,
    context: Any,
    semantic: bool,
) -> None:
    monkeypatch.setenv("SEOCHO_SEMANTIC_LAYER", str(int(semantic)))
    baseline, graph, llm = client_pair()
    contextual, context_graph, context_llm = client_pair()
    assert contextual.ask(QUESTION, query_context=context) == baseline.ask(QUESTION)
    assert context_graph.calls == graph.calls
    assert context_llm.calls == llm.calls


def test_clarification_remains_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEOCHO_SEMANTIC_LAYER", "1")
    baseline, graph, llm = client_pair(periods=("fiscal:2023:FY",))
    contextual, context_graph, context_llm = client_pair(periods=("fiscal:2023:FY",))
    answer = baseline.ask(QUESTION)
    assert "FY2023" in answer
    assert contextual.ask(QUESTION, query_context=CONTEXT) == answer
    assert context_graph.calls == graph.calls
    assert context_llm.calls == llm.calls


def test_semantic_empty_lookup_preserves_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEOCHO_SEMANTIC_LAYER", "1")
    baseline, graph, llm = client_pair(empty_observation=True)
    contextual, context_graph, context_llm = client_pair(empty_observation=True)
    answer = baseline.ask(QUESTION)
    contextual.ask(QUESTION, query_context=CONTEXT)
    assert context_graph.calls == graph.calls
    assert context_llm.calls[:-1] == llm.calls
    assert answer in context_llm.calls[-1]["user"]
    assert contextual._engine._last_semantic_route == "NARRATIVE"


@pytest.mark.parametrize("semantic", [True, False])
def test_reframing_failure_does_not_retry_retrieval(
    monkeypatch: pytest.MonkeyPatch,
    semantic: bool,
) -> None:
    monkeypatch.setenv("SEOCHO_SEMANTIC_LAYER", str(int(semantic)))
    baseline, graph, _ = client_pair()
    baseline.ask(QUESTION)
    contextual, context_graph, context_llm = client_pair()
    context_llm.fail_synthesis = True
    with pytest.raises(RuntimeError, match="reframing unavailable"):
        contextual.ask(QUESTION, query_context=CONTEXT)
    assert context_graph.calls == graph.calls


@pytest.mark.parametrize("semantic", [True, False])
def test_structured_rejection_precedes_io(
    monkeypatch: pytest.MonkeyPatch,
    semantic: bool,
) -> None:
    monkeypatch.setenv("SEOCHO_SEMANTIC_LAYER", str(int(semantic)))
    client, graph, llm = client_pair()
    with pytest.raises(ValueError, match="engine='deterministic'"):
        client.ask(QUESTION, engine="structured", query_context=CONTEXT)
    assert graph.calls == []
    assert llm.calls == []


@pytest.mark.parametrize("context", [CONTEXT, {"role": "risk"}])
def test_remote_rejection_precedes_transport(context: Any) -> None:
    from test_client_boundaries import _FakeSession

    session = _FakeSession([])
    client = Seocho(base_url="http://localhost:8001", session=session)
    with pytest.raises(ValueError, match="local engine mode"):
        client.ask(QUESTION, query_context=context)
    assert session.calls == []


@pytest.mark.parametrize("context", EMPTY_CONTEXTS)
def test_remote_empty_context_preserves_request(context: Any) -> None:
    from test_client_boundaries import _FakeResponse, _FakeSession

    def run(**kwargs: Any) -> tuple[str, list[dict[str, Any]]]:
        session = _FakeSession([_FakeResponse(payload={"response": "Remote answer"})])
        client = Seocho(
            base_url="http://localhost:8001",
            session=session,
            workspace_id="context-contract",
        )
        return client.ask(QUESTION, **kwargs), session.calls

    assert run(query_context=context) == run()


@pytest.mark.parametrize("context", EMPTY_CONTEXTS)
def test_structured_empty_context_preserves_execution(context: Any) -> None:
    def run(**kwargs: Any) -> tuple[str, list[dict[str, Any]]]:
        client, graph, _ = client_pair()
        # Keep the real structured orchestrator/guardrails; supply its documented
        # generator and synthesizer seams so no provider service is needed.
        client._engine._structured_cypher_generator = lambda question, schema: (
            "MATCH (c:Company {_workspace_id:$workspace_id}) RETURN c.name AS name LIMIT $limit"
        )
        client._engine._structured_synthesizer = lambda question, rows: (
            "Structured answer"
        )
        answer = client.ask(QUESTION, engine="structured", **kwargs)
        assert client.last_query_metadata["answer_source"] == "structured"
        return answer, graph.calls

    assert run(query_context=context) == run()


@pytest.mark.parametrize("reuse_client", [False, True])
@pytest.mark.parametrize("context", [None, CONTEXT])
def test_semantic_response_reports_its_own_evidence(
    monkeypatch: pytest.MonkeyPatch,
    reuse_client: bool,
    context: Any,
) -> None:
    client, graph, llm = client_pair()
    if reuse_client:
        client.ask_response(QUESTION, database="prior-db")
    before = len(graph.calls)
    monkeypatch.setenv("SEOCHO_SEMANTIC_LAYER", "1")
    response = client.ask_response(
        QUESTION, database="semantic-db", query_context=context
    )

    assert len(graph.calls) == before + 2  # metadata must not perform more queries
    lookup = graph.calls[-1]
    metadata = client.last_query_metadata
    assert metadata["database"] == lookup["database"] == "semantic-db"
    assert metadata["workspace_id"] == "context-contract"
    assert metadata["cypher"] == lookup["cypher"]
    assert metadata["params"] == lookup["params"]
    assert metadata["result_count"] == 1
    envelope = response.answer_envelope
    assert envelope["answer"] == response.response
    assert envelope["answer_source"] == (
        "llm_synthesis" if context else "deterministic"
    )
    assert envelope["support_assessment"]["row_count"] == 1
    assert {p["database"] for p in envelope["evidence_bundle"]["provenance"]} == {
        "semantic-db"
    }
    assert envelope["evidence_bundle"]["provenance"][0]["keys"] == sorted(
        OBSERVATION_ROWS[0]
    )
    timing = envelope["latency_breakdown_ms"]
    assert timing["retrieval_ms"] == timing["semantic_retrieval_ms"]
    assert timing["total_ms"] >= 0
    assert envelope["token_usage"]["source"] == "unavailable"
    assert envelope["token_usage"]["exact"] is False
    assert response.routing_decision["route"] == "STRUCTURED"
    if context:
        assert response.response == "Final answer"
        assert llm.calls[-1]["task_hint"] == "answer_synthesis"
        assert "generation_ms" in timing
        assert envelope["synthesis_usage"] == {
            "prompt_tokens": 100,
            "completion_tokens": 10,
            "total_tokens": 110,
        }
    else:
        assert "synthesis_usage" not in envelope


@pytest.mark.parametrize("context", [None, CONTEXT])
def test_semantic_clarification_replaces_previous_evidence(
    monkeypatch: pytest.MonkeyPatch,
    context: Any,
) -> None:
    client, graph, llm = client_pair(periods=("fiscal:2023:FY",))
    client.ask_response(QUESTION, database="prior-db")
    graph_count, llm_count = len(graph.calls), len(llm.calls)
    monkeypatch.setenv("SEOCHO_SEMANTIC_LAYER", "1")
    response = client.ask_response(
        QUESTION, database="clarify-db", query_context=context
    )

    assert "FY2023" in response.response
    assert len(graph.calls) == graph_count + 1
    assert len(llm.calls) == llm_count + 1  # decomposition only
    assert client.last_query_metadata["database"] == "clarify-db"
    assert client.last_query_metadata["cypher"] == ""
    assert client.last_query_metadata["result_count"] == 0
    assert response.answer_envelope["evidence_bundle"]["provenance"] == []
    assert response.answer_envelope["answer_source"] == "clarification"
    assert "synthesis_usage" not in response.answer_envelope
    assert response.routing_decision["route"] == "CLARIFY"


@pytest.mark.parametrize("semantic", [False, True])
def test_reframing_error_does_not_expose_previous_metadata(
    monkeypatch: pytest.MonkeyPatch,
    semantic: bool,
) -> None:
    client, graph, llm = client_pair()
    client.ask(QUESTION, database="prior-db")
    llm.fail_synthesis = True
    monkeypatch.setenv("SEOCHO_SEMANTIC_LAYER", str(int(semantic)))
    with pytest.raises(RuntimeError, match="reframing unavailable"):
        client.ask_response(QUESTION, database="failed-db", query_context=CONTEXT)
    assert client.last_query_metadata == {}
