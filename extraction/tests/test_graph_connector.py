"""Tests for graph-scoped multi-instance connector behavior."""

import json

from types import SimpleNamespace

from .. import graph_connector
from ..config import GraphTarget


def _patch_registry(monkeypatch, get_graph):
    """Swap the module's registry reference instead of mutating the singleton.

    `graph_registry` is one object shared with `runtime/server_runtime.py`. Until
    seocho-60u it was not — `extraction/config.py` loaded twice, so patching the
    method here reached a different object than the runtime used, and the leak
    was invisible. With one module there is one registry, and mutating its
    method during a test made the runtime resolve
    `bolt://finance:7687 / password="secret"`, failing later integration tests
    with Neo.ClientError.Security.Unauthorized.

    Rebinding the module attribute keeps the fake local to this module and
    leaves the shared object untouched.
    """
    monkeypatch.setattr(
        graph_connector,
        "graph_registry",
        SimpleNamespace(
            get_graph=get_graph,
            list_graph_ids=lambda: ["finance"],
            # Returning None keeps resolve_target on its explicit-override path,
            # which is what these tests exercise.
            find_by_database=lambda database: None,
        ),
    )

def test_resolve_target_from_graph_registry(monkeypatch):
    _patch_registry(
        monkeypatch,
        lambda graph_id: GraphTarget(
            graph_id=graph_id,
            database="kgfibo",
            uri="bolt://finance:7687",
            user="neo4j",
            password="secret",
            ontology_id="fibo",
        ),
    )
    connector = graph_connector.MultiGraphConnector()

    target = connector.resolve_target(graph_id="finance")

    assert target.graph_id == "finance"
    assert target.database == "kgfibo"
    assert target.uri == "bolt://finance:7687"


def test_run_cypher_uses_graph_bound_driver(monkeypatch):
    calls = []

    class _Record:
        @staticmethod
        def data():
            return {"ok": 1}

    class _Session:
        def __init__(self, database: str):
            self.database = database

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def run(self, query, parameters=None):
            calls.append(
                {
                    "database": self.database,
                    "query": query,
                    "parameters": parameters,
                }
            )
            return [_Record()]

    class _Driver:
        def __init__(self, uri, auth):
            self.uri = uri
            self.auth = auth

        def session(self, database: str):
            return _Session(database)

        def close(self):
            return None

    _patch_registry(
        monkeypatch,
        lambda graph_id: GraphTarget(
            graph_id=graph_id,
            database="kgfibo",
            uri="bolt://finance:7687",
            user="neo4j",
            password="secret",
            ontology_id="fibo",
        ),
    )
    # Replace the module's reference, not neo4j's GraphDatabase class. Patching
    # the class mutates global state shared with every other test in the
    # session; until seocho-60u that leak was hidden because extraction/ loaded
    # twice and the runtime held a different module object.
    monkeypatch.setattr(
        graph_connector,
        "GraphDatabase",
        SimpleNamespace(driver=lambda uri, auth: _Driver(uri, auth)),
    )
    connector = graph_connector.MultiGraphConnector()

    out = connector.run_cypher("RETURN 1 AS ok", graph_id="finance", params={"limit": 1})

    assert json.loads(out) == [{"ok": 1}]
    assert calls == [
        {
            "database": "kgfibo",
            "query": "RETURN 1 AS ok",
            "parameters": {"limit": 1},
        }
    ]


def test_query_normalizes_graph_bound_driver_rows(monkeypatch):
    class _Record:
        @staticmethod
        def data():
            return {"ok": 2}

    class _Session:
        def __init__(self, database: str):
            self.database = database

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def run(self, query, parameters=None):
            return [_Record()]

    class _Driver:
        def __init__(self, uri, auth):
            self.uri = uri
            self.auth = auth

        def session(self, database: str):
            return _Session(database)

        def close(self):
            return None

    _patch_registry(
        monkeypatch,
        lambda graph_id: GraphTarget(
            graph_id=graph_id,
            database="kgfibo",
            uri="bolt://finance:7687",
            user="neo4j",
            password="secret",
            ontology_id="fibo",
        ),
    )
    # Replace the module's reference, not neo4j's GraphDatabase class. Patching
    # the class mutates global state shared with every other test in the
    # session; until seocho-60u that leak was hidden because extraction/ loaded
    # twice and the runtime held a different module object.
    monkeypatch.setattr(
        graph_connector,
        "GraphDatabase",
        SimpleNamespace(driver=lambda uri, auth: _Driver(uri, auth)),
    )
    connector = graph_connector.MultiGraphConnector()

    rows = connector.query("RETURN 2 AS ok", database="kgfibo")

    assert rows == [{"ok": 2}]
