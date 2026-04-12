from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from seocho import Seocho
from seocho.agent_config import AgentConfig
from seocho.http_runtime import create_bundle_runtime_app
from seocho.ontology import NodeDef, Ontology, P, RelDef
from seocho.query.strategy import PromptTemplate
from seocho.runtime_bundle import (
    RuntimeBundle,
    RuntimeGraphBinding,
    RuntimeGraphStoreConfig,
    RuntimeLLMConfig,
)


class Neo4jGraphStore:
    def __init__(self, uri: str = "bolt://unit-test:7687", user: str = "neo4j", password: str = "password") -> None:
        self._uri = uri
        self._user = user
        self._password = password

    def close(self) -> None:
        return None


class OpenAIBackend:
    def __init__(self, model: str = "gpt-4o", api_key: str | None = None, base_url: str | None = None) -> None:
        self.provider = "openai"
        self.model = model
        self._api_key = api_key
        self._api_key_env = "OPENAI_API_KEY"
        self._base_url = base_url or ""


class DummyLocalEngine:
    def __init__(
        self,
        *,
        ontology,
        graph_store,
        llm,
        workspace_id,
        extraction_prompt=None,
        agent_config=None,
    ) -> None:
        self.ontology = ontology
        self.graph_store = graph_store
        self.llm = llm
        self.workspace_id = workspace_id
        self.extraction_prompt = extraction_prompt
        self.agent_config = agent_config


class FakeBundleRuntimeClient:
    def __init__(self) -> None:
        self.workspace_id = "default"
        self.add_calls = []
        self.ask_calls = []

    def add(self, content: str, *, metadata=None, database: str = "neo4j", category: str = "memory"):
        self.add_calls.append(
            {
                "content": content,
                "metadata": dict(metadata or {}),
                "database": database,
                "category": category,
            }
        )

        class _Memory:
            def to_dict(self_nonlocal):
                return {
                    "memory_id": "mem-1",
                    "workspace_id": "default",
                    "content": content,
                    "metadata": dict(metadata or {}),
                    "status": "active",
                    "database": database,
                }

        return _Memory()

    def ask(self, query: str, *, database: str = "neo4j", reasoning_mode: bool = False, repair_budget: int = 0) -> str:
        self.ask_calls.append(
            {
                "query": query,
                "database": database,
                "reasoning_mode": reasoning_mode,
                "repair_budget": repair_budget,
            }
        )
        return f"answer:{database}:{query}"

    def query(self, cypher: str, *, params=None, database: str = "neo4j"):
        query_text = str((params or {}).get("query", ""))
        return [
            {
                "memory_id": "mem-1",
                "content": f"{query_text} content",
                "content_preview": f"{query_text} preview",
                "metadata": {"source": "fake"},
                "matched_entity": "Alex",
            }
        ]


@pytest.fixture
def ontology() -> Ontology:
    return Ontology(
        name="portable_company_graph",
        graph_model="lpg",
        nodes={
            "Person": NodeDef(properties={"name": P(str, unique=True)}),
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
        },
        relationships={
            "WORKS_AT": RelDef(source="Person", target="Company", cardinality="MANY_TO_ONE"),
        },
    )


@pytest.fixture
def patch_local_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("seocho.client._LocalEngine", DummyLocalEngine)


def test_export_runtime_bundle_serializes_local_sdk_configuration(
    ontology: Ontology,
    patch_local_engine: None,
) -> None:
    client = Seocho(
        ontology=ontology,
        graph_store=Neo4jGraphStore(),
        llm=OpenAIBackend(model="gpt-4.1-mini"),
        extraction_prompt=PromptTemplate(system="Extract for {{ontology_name}}", user="Text: {{text}}"),
        agent_config=AgentConfig(reasoning_mode=True, repair_budget=2, answer_style="evidence"),
    )

    bundle = client.export_runtime_bundle(app_name="portable-app", default_database="news")

    assert bundle.app_name == "portable-app"
    assert bundle.graph_store.uri == "bolt://unit-test:7687"
    assert bundle.graph_store.user == "neo4j"
    assert bundle.graph_store.default_database == "news"
    assert bundle.llm.model == "gpt-4.1-mini"
    assert bundle.agent_config["reasoning_mode"] is True
    assert bundle.extraction_prompt is not None
    assert "ontology_name" in bundle.extraction_prompt.system
    assert bundle.graphs[0].database == "news"

    client.close()


def test_export_runtime_bundle_rejects_custom_python_strategies(
    ontology: Ontology,
    patch_local_engine: None,
) -> None:
    client = Seocho(
        ontology=ontology,
        graph_store=Neo4jGraphStore(),
        llm=OpenAIBackend(),
        agent_config=AgentConfig(custom_query_strategy=object()),
    )

    with pytest.raises(ValueError, match="cannot include custom Python indexing/query strategies"):
        client.export_runtime_bundle(default_database="news")

    client.close()


def test_from_runtime_bundle_rehydrates_local_client(
    monkeypatch: pytest.MonkeyPatch,
    ontology: Ontology,
    patch_local_engine: None,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-key")
    monkeypatch.setenv("NEO4J_PASSWORD", "password")
    monkeypatch.setattr("seocho.store.graph.Neo4jGraphStore", Neo4jGraphStore)
    monkeypatch.setattr("seocho.store.llm.OpenAIBackend", OpenAIBackend)

    bundle = RuntimeBundle(
        app_name="rehydrated-app",
        workspace_id="default",
        ontology=ontology.to_dict(),
        llm=RuntimeLLMConfig(model="gpt-4.1"),
        graph_store=RuntimeGraphStoreConfig(uri="bolt://bundle:7687", user="neo4j", default_database="news"),
        agent_config=AgentConfig(reasoning_mode=True, repair_budget=1).to_dict(),
        graphs=[
            RuntimeGraphBinding(
                graph_id="news",
                database="news",
                ontology_id=ontology.name,
                graph_model="lpg",
                uri="bolt://bundle:7687",
            )
        ],
    )

    client = Seocho.from_runtime_bundle(bundle)

    assert client._local_mode is True
    assert client.workspace_id == "default"
    assert client.ontology.name == ontology.name
    assert client.graph_store._uri == "bolt://bundle:7687"
    assert client.llm.model == "gpt-4.1"
    assert client.agent_config.reasoning_mode is True

    client.close()


def test_from_runtime_bundle_rehydrates_non_openai_provider(
    monkeypatch: pytest.MonkeyPatch,
    ontology: Ontology,
    patch_local_engine: None,
) -> None:
    class DeepSeekBackend:
        def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None) -> None:
            self.provider = "deepseek"
            self.model = model
            self._api_key = api_key
            self._api_key_env = "DEEPSEEK_API_KEY"
            self._base_url = base_url or ""

    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.setenv("NEO4J_PASSWORD", "password")
    monkeypatch.setattr("seocho.store.graph.Neo4jGraphStore", Neo4jGraphStore)
    monkeypatch.setattr(
        "seocho.store.llm.create_llm_backend",
        lambda **kwargs: DeepSeekBackend(
            model=str(kwargs["model"]),
            api_key=kwargs.get("api_key"),
            base_url=kwargs.get("base_url"),
        ),
    )

    bundle = RuntimeBundle(
        app_name="deepseek-app",
        workspace_id="default",
        ontology=ontology.to_dict(),
        llm=RuntimeLLMConfig(
            kind="openai_compatible",
            provider="deepseek",
            model="deepseek-chat",
            base_url="https://api.deepseek.com",
            api_key_env="DEEPSEEK_API_KEY",
        ),
        graph_store=RuntimeGraphStoreConfig(uri="bolt://bundle:7687", user="neo4j", default_database="news"),
        graphs=[
            RuntimeGraphBinding(
                graph_id="news",
                database="news",
                ontology_id=ontology.name,
                graph_model="lpg",
                uri="bolt://bundle:7687",
            )
        ],
    )

    client = Seocho.from_runtime_bundle(bundle)

    assert client._local_mode is True
    assert client.llm.provider == "deepseek"
    assert client.llm.model == "deepseek-chat"
    assert client.llm._base_url == "https://api.deepseek.com"

    client.close()


def test_bundle_runtime_app_exposes_http_compatibility_surface() -> None:
    bundle = RuntimeBundle(
        app_name="portable-app",
        workspace_id="default",
        ontology={"graph_type": "portable_company_graph", "graph_model": "rdf"},
        llm=RuntimeLLMConfig(model="gpt-4.1"),
        graph_store=RuntimeGraphStoreConfig(default_database="finance"),
        agent_config={"routing": "auto", "reasoning_mode": True, "repair_budget": 2},
        graphs=[
            RuntimeGraphBinding(
                graph_id="finance",
                database="finance",
                ontology_id="portable_company_graph",
                graph_model="rdf",
                uri="bolt://bundle:7687",
            )
        ],
    )
    app = create_bundle_runtime_app(bundle, client=FakeBundleRuntimeClient())
    http = TestClient(app)

    health = http.get("/health/runtime")
    assert health.status_code == 200
    assert health.json()["runtime_mode"] == "bundle_local_engine"

    graphs = http.get("/graphs")
    assert graphs.status_code == 200
    assert graphs.json()["graphs"][0]["graph_id"] == "finance"

    created = http.post(
        "/api/memories",
        json={
            "workspace_id": "default",
            "content": "Alex manages Seoul retail.",
            "database": "finance",
        },
    )
    assert created.status_code == 200
    assert created.json()["memory"]["memory_id"] == "mem-1"

    searched = http.post(
        "/api/memories/search",
        json={"workspace_id": "default", "query": "Alex", "databases": ["finance"]},
    )
    assert searched.status_code == 200
    assert searched.json()["results"][0]["database"] == "finance"

    chatted = http.post(
        "/api/chat",
        json={"workspace_id": "default", "message": "Who is Alex?", "databases": ["finance"]},
    )
    assert chatted.status_code == 200
    assert chatted.json()["assistant_message"] == "answer:finance:Who is Alex?"

    semantic = http.post(
        "/run_agent_semantic",
        json={
            "workspace_id": "default",
            "query": "Who is Alex?",
            "databases": ["finance"],
            "reasoning_mode": True,
            "repair_budget": 2,
        },
    )
    assert semantic.status_code == 200
    payload = semantic.json()
    assert payload["route"] == "rdf"
    assert payload["strategy_decision"]["executed_mode"] == "semantic_repair"
    assert payload["support_assessment"]["status"] == "supported"


def test_bundle_runtime_app_rejects_workspace_mismatch() -> None:
    bundle = RuntimeBundle(
        app_name="portable-app",
        workspace_id="default",
        ontology={"graph_type": "portable_company_graph", "graph_model": "lpg"},
        graphs=[RuntimeGraphBinding(graph_id="neo4j", database="neo4j", ontology_id="portable_company_graph")],
    )
    app = create_bundle_runtime_app(bundle, client=FakeBundleRuntimeClient())
    http = TestClient(app)

    response = http.post("/api/chat", json={"workspace_id": "other", "message": "Who is Alex?"})
    assert response.status_code == 400
    assert "Workspace mismatch" in response.json()["detail"]
