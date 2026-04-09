"""Tests for graph-scoped multi-instance connector behavior."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import graph_connector
from config import GraphTarget


def test_resolve_target_from_graph_registry(monkeypatch):
    monkeypatch.setattr(
        graph_connector.graph_registry,
        "get_graph",
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

    monkeypatch.setattr(
        graph_connector.graph_registry,
        "get_graph",
        lambda graph_id: GraphTarget(
            graph_id=graph_id,
            database="kgfibo",
            uri="bolt://finance:7687",
            user="neo4j",
            password="secret",
            ontology_id="fibo",
        ),
    )
    monkeypatch.setattr(
        graph_connector.GraphDatabase,
        "driver",
        lambda uri, auth: _Driver(uri, auth),
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
