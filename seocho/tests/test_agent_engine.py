from __future__ import annotations

from seocho import NodeDef, Ontology, P, RelDef
from seocho.agent import (
    SessionContext,
    create_indexing_agent,
    create_query_agent,
    normalize_execution_mode,
)


def _make_test_ontology() -> Ontology:
    return Ontology(
        name="test_finance",
        nodes={
            "Company": NodeDef(properties={"name": P(required=True), "industry": P()}),
            "Person": NodeDef(properties={"name": P(required=True), "role": P()}),
        },
        relationships={
            "EMPLOYS": RelDef(source="Company", target="Person", properties={"since": P()}),
        },
    )


class _FakeLLM:
    model = "fake-model"

    def to_agents_sdk_model(self, *, model=None):  # noqa: ANN001
        try:
            from agents.models.interface import Model

            class _FakeModel(Model):
                async def get_response(self, *a, **kw):  # noqa: ANN002, ANN003
                    raise NotImplementedError

                async def stream_response(self, *a, **kw):  # noqa: ANN002, ANN003
                    raise NotImplementedError

            return _FakeModel()
        except ImportError:  # pragma: no cover
            from unittest.mock import MagicMock

            return MagicMock()


class _FakeGraphStore:
    pass


def test_normalize_execution_mode_uses_canonical_contract() -> None:
    assert normalize_execution_mode("agent") == "agent"
    assert normalize_execution_mode("SUPERVISOR") == "supervisor"
    assert normalize_execution_mode("unexpected") == "pipeline"
    assert normalize_execution_mode(None) == "pipeline"


def test_session_context_lives_in_canonical_agent_package() -> None:
    context = SessionContext()
    context.add_indexing("src1", 2, 1, "Preview text", mode="agent")
    context.add_query("Who works here?", "Alice works here.", mode="pipeline")

    assert context.total_nodes == 2
    assert context.total_relationships == 1
    assert "Answered 1 question" in context.summary()


def test_canonical_agent_factory_creates_indexing_and_query_agents() -> None:
    try:
        import agents  # noqa: F401
    except ImportError:  # pragma: no cover
        return

    ontology = _make_test_ontology()
    llm = _FakeLLM()
    store = _FakeGraphStore()

    indexing_agent = create_indexing_agent(ontology=ontology, graph_store=store, llm=llm)
    query_agent = create_query_agent(ontology=ontology, graph_store=store, llm=llm)

    assert indexing_agent.name == "IndexingAgent"
    assert query_agent.name == "QueryAgent"
