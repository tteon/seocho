"""Integration tests for runtime raw-ingest and semantic chat flow."""

import importlib
import os
import re
import sys
import types
from contextlib import nullcontext
from typing import Any, Dict, List
from unittest.mock import MagicMock

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _FakeRecord:
    def __init__(self, row: Dict[str, Any]):
        self._row = row

    def data(self) -> Dict[str, Any]:
        return dict(self._row)

    def __getitem__(self, item: str) -> Any:
        return self._row[item]


class _FakeResult:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = [_FakeRecord(r) for r in rows]

    def __iter__(self):
        return iter(self._rows)


class _GraphStore:
    def __init__(self):
        self.databases: Dict[str, Dict[str, Any]] = {}
        self.ensure_db("neo4j")
        self.ensure_db("system")
        self.ensure_db("kgnormal")
        self.ensure_db("kgfibo")

    def ensure_db(self, name: str) -> None:
        self.databases.setdefault(
            name,
            {
                "nodes": {},
                "relationships": [],
                "indexes": set(),
            },
        )


class _FakeTx:
    def __init__(self, store: _GraphStore, database: str):
        self._store = store
        self._database = database

    def run(self, query: str, **kwargs):
        db = self._store.databases[self._database]
        if "MERGE (n:`" in query and "SET n += $props" in query:
            match = re.search(r"MERGE \(n:`([^`]+)`", query)
            label = match.group(1) if match else "Entity"
            node_id = str(kwargs.get("id", ""))
            props = dict(kwargs.get("props", {}))
            db["nodes"][node_id] = {"id": node_id, "label": label, "properties": props}
            return _FakeResult([{"id": node_id}])

        if "MERGE (a)-[r:`" in query:
            match = re.search(r"MERGE \(a\)-\[r:`([^`]+)`\]->\(b\)", query)
            rel_type = match.group(1) if match else "RELATED_TO"
            db["relationships"].append(
                {
                    "source": kwargs.get("source_id"),
                    "target": kwargs.get("target_id"),
                    "type": rel_type,
                    "properties": dict(kwargs.get("props", {})),
                }
            )
            return _FakeResult([{"type": rel_type}])

        return _FakeResult([])


class _FakeSession:
    def __init__(self, store: _GraphStore, database: str):
        self._store = store
        self._database = database
        self._store.ensure_db(database)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute_write(self, fn, *args):
        return fn(_FakeTx(self._store, self._database), *args)

    def run(self, query: str, **kwargs):
        params = dict(kwargs.get("parameters", {}) or {})
        for key, value in kwargs.items():
            if key != "parameters":
                params[key] = value

        db = self._store.databases[self._database]
        compact = " ".join(query.split())

        if compact.startswith("CREATE DATABASE "):
            db_name = compact.split()[2]
            self._store.ensure_db(db_name)
            return _FakeResult([])

        if "SHOW FULLTEXT INDEXES" in compact or "SHOW INDEXES" in compact:
            rows = [
                {
                    "name": index_name,
                    "state": "ONLINE",
                    "entityType": "NODE",
                    "labelsOrTypes": ["Entity"],
                    "properties": ["name"],
                }
                for index_name in sorted(db["indexes"])
            ]
            return _FakeResult(rows)

        if "CREATE FULLTEXT INDEX" in compact:
            # CREATE FULLTEXT INDEX <name> IF NOT EXISTS ...
            parts = compact.split()
            index_name = parts[3] if len(parts) > 3 else "entity_fulltext"
            db["indexes"].add(index_name)
            return _FakeResult([])

        if "CALL db.index.fulltext.createNodeIndex" in compact:
            db["indexes"].add(str(params.get("name", "entity_fulltext")))
            return _FakeResult([])

        if "CALL db.labels()" in compact:
            labels = sorted({node["label"] for node in db["nodes"].values()})
            return _FakeResult([{"label": label} for label in labels])

        if "CALL db.relationshipTypes()" in compact:
            rels = sorted({rel["type"] for rel in db["relationships"]})
            return _FakeResult([{"relationshipType": rel} for rel in rels])

        if "CALL db.propertyKeys()" in compact:
            keys = set()
            for node in db["nodes"].values():
                keys.update(node["properties"].keys())
            return _FakeResult([{"propertyKey": key} for key in sorted(keys)])

        if "RETURN labels(n)[0] AS label, count(*) AS count" in compact:
            counts: Dict[str, int] = {}
            for node in db["nodes"].values():
                label = node["label"]
                counts[label] = counts.get(label, 0) + 1
            rows = [{"label": label, "count": count} for label, count in counts.items()]
            rows.sort(key=lambda row: row["count"], reverse=True)
            return _FakeResult(rows)

        if "CALL db.index.fulltext.queryNodes" in compact:
            return _FakeResult([])

        if "WHERE any(key IN $properties" in compact:
            return _FakeResult([])

        if "WHERE id(n) = $node_id" in compact:
            return _FakeResult([])

        return _FakeResult([])


class _FakeDriver:
    def __init__(self, store: _GraphStore):
        self._store = store

    def session(self, database: str = "neo4j"):
        return _FakeSession(self._store, database)

    def close(self):
        return None


@pytest.fixture(scope="module")
def app_module():
    """Import agent_server with an in-memory fake Neo4j backend."""
    store = _GraphStore()

    class FakeGraphDatabase:
        @staticmethod
        def driver(*_args, **_kwargs):
            return _FakeDriver(store)

    fake_neo4j = types.ModuleType("neo4j")
    fake_neo4j.GraphDatabase = FakeGraphDatabase

    fake_neo4j_exceptions = types.ModuleType("neo4j.exceptions")
    fake_neo4j_exceptions.ServiceUnavailable = RuntimeError
    fake_neo4j_exceptions.SessionExpired = RuntimeError

    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = MagicMock()

    fake_faiss = MagicMock()

    class DummyAgent:
        def __init__(self, *args, **kwargs):
            self.name = kwargs.get("name", "DummyAgent")
            self.instructions = kwargs.get("instructions", "")
            self.tools = kwargs.get("tools", [])
            self.handoffs = kwargs.get("handoffs", [])

    class DummyRunner:
        @staticmethod
        async def run(*_args, **_kwargs):
            return types.SimpleNamespace(final_output="", to_input_list=lambda: [])

    def function_tool(func):
        return func

    class DummyRunContextWrapper:
        pass

    fake_agents = types.SimpleNamespace(
        Agent=DummyAgent,
        Runner=DummyRunner,
        function_tool=function_tool,
        RunContextWrapper=DummyRunContextWrapper,
        trace=lambda *args, **kwargs: nullcontext(),
    )

    with pytest.MonkeyPatch().context() as mp:
        mp.setenv("OPENAI_API_KEY", "test-key")
        mp.setenv("OPIK_URL_OVERRIDE", "")
        with pytest.MonkeyPatch().context() as mp_modules:
            mp_modules.setitem(sys.modules, "neo4j", fake_neo4j)
            mp_modules.setitem(sys.modules, "neo4j.exceptions", fake_neo4j_exceptions)
            mp_modules.setitem(sys.modules, "openai", fake_openai)
            mp_modules.setitem(sys.modules, "faiss", fake_faiss)
            mp_modules.setitem(sys.modules, "agents", fake_agents)
            for module_name in [
                "agent_server",
                "config",
                "database_manager",
                "graph_loader",
                "runtime_ingest",
                "semantic_query_flow",
                "fulltext_index",
                "dependencies",
            ]:
                sys.modules.pop(module_name, None)
            import agent_server

            module = importlib.reload(agent_server)
            module._integration_graph_store = store
            return module


@pytest.fixture
async def client(app_module):
    transport = httpx.ASGITransport(app=app_module.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client


def _label_count(records: List[Dict[str, Any]], label: str) -> int:
    for row in records:
        if row.get("label") == label:
            return int(row.get("count", 0))
    return 0


@pytest.mark.anyio
async def test_ingest_then_semantic_chat_flow(client):
    ingest_response = await client.post(
        "/platform/ingest/raw",
        json={
            "workspace_id": "default",
            "target_database": "kgruntimea",
            "records": [
                {"id": "raw_a1", "content": "ACME acquired Beta in 2024."},
            ],
        },
    )
    assert ingest_response.status_code == 200
    ingest_payload = ingest_response.json()
    assert ingest_payload["status"] in {"success", "success_with_fallback"}
    assert ingest_payload["records_processed"] == 1
    assert ingest_payload["records_failed"] == 0
    assert ingest_payload["total_nodes"] >= 2

    chat_response = await client.post(
        "/platform/chat/send",
        json={
            "session_id": "runtime-flow-a",
            "message": "Show graph labels in kgruntimea",
            "mode": "semantic",
            "workspace_id": "default",
            "databases": ["kgruntimea"],
        },
    )
    assert chat_response.status_code == 200
    chat_payload = chat_response.json()
    assert chat_payload["runtime_payload"]["route"] == "lpg"
    assert len(chat_payload["history"]) == 2
    records = chat_payload["runtime_payload"]["lpg_result"]["records"]
    assert _label_count(records, "Document") >= 1
    assert _label_count(records, "Entity") >= 1


@pytest.mark.anyio
async def test_database_scoped_counts_are_isolated(client):
    # DB A: 1 record -> fewer nodes
    response_a = await client.post(
        "/platform/ingest/raw",
        json={
            "workspace_id": "default",
            "target_database": "kgruntimeb",
            "records": [{"id": "raw_b1", "content": "ALPHA meets BETA."}],
        },
    )
    assert response_a.status_code == 200

    # DB B: 2 records -> more nodes
    response_b = await client.post(
        "/platform/ingest/raw",
        json={
            "workspace_id": "default",
            "target_database": "kgruntimec",
            "records": [
                {"id": "raw_c1", "content": "OMEGA supports SIGMA."},
                {"id": "raw_c2", "content": "SIGMA integrates DELTA."},
            ],
        },
    )
    assert response_b.status_code == 200

    chat_a = await client.post(
        "/platform/chat/send",
        json={
            "session_id": "runtime-flow-b",
            "message": "Show graph labels",
            "mode": "semantic",
            "workspace_id": "default",
            "databases": ["kgruntimeb"],
        },
    )
    chat_c = await client.post(
        "/platform/chat/send",
        json={
            "session_id": "runtime-flow-c",
            "message": "Show graph labels",
            "mode": "semantic",
            "workspace_id": "default",
            "databases": ["kgruntimec"],
        },
    )
    assert chat_a.status_code == 200
    assert chat_c.status_code == 200

    records_a = chat_a.json()["runtime_payload"]["lpg_result"]["records"]
    records_c = chat_c.json()["runtime_payload"]["lpg_result"]["records"]
    assert _label_count(records_c, "Entity") > _label_count(records_a, "Entity")
    assert _label_count(records_c, "Document") > _label_count(records_a, "Document")
