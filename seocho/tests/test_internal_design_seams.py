from __future__ import annotations

import seocho.client as client_module
from runtime.agent_state import AgentStateMachine
from seocho.events import DomainEvent, InMemoryEventPublisher
from seocho.index.ingestion_facade import IngestRequest, IngestionFacade
from seocho.local_engine import _LocalEngine
from seocho.query.agent_factory import AgentConfig, AgentFactory
from seocho.query.query_proxy import QueryProxy, QueryRequest


class _FakeIndexingResult:
    def __init__(self, *, source_id: str = "src-1") -> None:
        self.source_id = source_id

    def to_dict(self) -> dict[str, object]:
        return {"source_id": self.source_id, "ok": True}


class _FakeIndexingPipeline:
    def __init__(self) -> None:
        self.strict_validation = False
        self.calls: list[dict[str, object]] = []

    def index(self, content: str, *, database: str, category: str, metadata=None):  # noqa: ANN001
        self.calls.append(
            {
                "content": content,
                "database": database,
                "category": category,
                "metadata": metadata,
                "strict_validation": self.strict_validation,
            }
        )
        return _FakeIndexingResult(source_id="src-99")


class _RecordingPolicy:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def validate_query(
        self,
        *,
        cypher: str,
        workspace_id: str,
        ontology_profile: str,
        database: str,
        params=None,
    ) -> None:
        self.calls.append(
            {
                "cypher": cypher,
                "workspace_id": workspace_id,
                "ontology_profile": ontology_profile,
                "database": database,
                "params": dict(params or {}),
            }
        )


class _FakeGraphStore:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def query(self, cypher: str, *, params=None, database: str = "neo4j"):  # noqa: ANN001
        self.calls.append(
            {
                "cypher": cypher,
                "params": dict(params or {}),
                "database": database,
            }
        )
        return [{"answer": 1}]


def test_domain_event_records_basic_metadata() -> None:
    event = DomainEvent(
        kind="ingest.started",
        workspace_id="ws-1",
        payload={"database": "demo"},
    )

    assert event.kind == "ingest.started"
    assert event.workspace_id == "ws-1"
    assert event.payload["database"] == "demo"
    assert event.event_id
    assert event.ts_ms > 0


def test_ingestion_facade_publishes_lifecycle_events() -> None:
    publisher = InMemoryEventPublisher()
    pipeline = _FakeIndexingPipeline()
    facade = IngestionFacade(pipeline, publisher=publisher)

    result = facade.ingest(
        IngestRequest(
            content="Acme acquired Beta.",
            workspace_id="ws-1",
            database="kgdemo",
            category="finance",
            metadata={"source": "unit"},
            strict_validation=True,
            source_id="doc-1",
        )
    )

    assert result.to_dict()["source_id"] == "src-99"
    assert pipeline.calls[0]["strict_validation"] is True
    assert [event.kind for event in publisher.events] == ["ingest.started", "ingest.finished"]
    assert publisher.events[0].payload["source_id"] == "doc-1"
    assert publisher.events[1].payload["source_id"] == "src-99"


def test_query_proxy_validates_and_publishes_success() -> None:
    publisher = InMemoryEventPublisher()
    policy = _RecordingPolicy()
    store = _FakeGraphStore()
    proxy = QueryProxy(store, publisher=publisher, policy=policy)

    records = proxy.query(
        QueryRequest(
            cypher="MATCH (n) RETURN n",
            params={"limit": 5},
            workspace_id="ws-1",
            database="kgdemo",
            ontology_profile="finance",
        )
    )

    assert records == [{"answer": 1}]
    assert policy.calls[0]["ontology_profile"] == "finance"
    assert store.calls[0]["database"] == "kgdemo"
    assert publisher.events[-1].kind == "query.succeeded"
    assert publisher.events[-1].payload["result_count"] == 1


def test_agent_factory_registers_and_creates_agents() -> None:
    publisher = InMemoryEventPublisher()
    factory = AgentFactory(publisher=publisher)
    factory.register("semantic", lambda config: {"mode": config.mode, "database": config.database})

    agent = factory.create(
        AgentConfig(
            mode="semantic",
            workspace_id="ws-1",
            database="kgdemo",
            ontology_profile="finance",
        )
    )

    assert agent == {"mode": "semantic", "database": "kgdemo"}
    assert publisher.events[-1].kind == "agent.created"
    assert publisher.events[-1].payload["mode"] == "semantic"


def test_agent_state_machine_publishes_transitions() -> None:
    publisher = InMemoryEventPublisher()
    state = AgentStateMachine(workspace_id="ws-1", publisher=publisher)

    assert state.can_answer() is False
    assert state.can_query_graph() is False

    state.mark_ready()
    assert state.can_answer() is True
    assert state.can_query_graph() is True

    state.mark_degraded("graph temporarily unavailable")
    assert state.can_answer() is True
    assert state.can_query_graph() is False

    state.mark_blocked("policy denied")
    assert state.can_answer() is False
    assert state.can_query_graph() is False
    assert publisher.events[-1].payload["to"] == "blocked"


def test_client_imports_local_engine_from_dedicated_module() -> None:
    assert client_module._LocalEngine is _LocalEngine
    assert _LocalEngine.__module__ == "seocho.local_engine"
