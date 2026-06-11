"""Unit tests for seocho.gds — projection lifecycle, estimate gate, WCC stream."""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from seocho.gds import GDSMemoryError, GDSSession, gds_session


class FakeGraphStore:
    """Captures Cypher; replies from a queue keyed by a substring match."""

    def __init__(self, replies: Dict[str, List[Dict[str, Any]]] | None = None):
        self.calls: List[tuple[str, Dict[str, Any]]] = []
        self.replies = replies or {}

    def query(self, cypher: str, params: Dict[str, Any] | None = None, *,
              database: str | None = None) -> List[Dict[str, Any]]:
        self.calls.append((cypher, params or {}))
        for key, rows in self.replies.items():
            if key in cypher:
                return rows
        return []

    # GDSSession._run probes these names in order; expose `run`.
    def run(self, cypher: str, params: Dict[str, Any] | None = None, *,
            database: str | None = None) -> List[Dict[str, Any]]:
        return self.query(cypher, params, database=database)


_ESTIMATE_ROW = [{
    "nodeCount": 10, "relationshipCount": 9,
    "bytesMin": 1000, "bytesMax": 4000, "requiredMemory": "4 KiB",
}]


def test_estimate_gate_refuses_oversized_projection():
    store = FakeGraphStore({"estimate": _ESTIMATE_ROW})
    s = GDSSession(store, name="t")
    with pytest.raises(GDSMemoryError):
        # bytes_max=4000 > 30% of heap=10000 → refuse
        s.project_cypher(node_query="n", rel_query="r",
                         estimate_ok=True, heap_bytes=10_000)
    assert not any("gds.graph.project.cypher(" in c for c, _ in store.calls)


def test_projection_dropped_on_context_exit():
    store = FakeGraphStore({"estimate": _ESTIMATE_ROW})
    with gds_session(store, name="t") as s:
        s.project_cypher(node_query="n", rel_query="r")
    drops = [c for c, p in store.calls if "gds.graph.drop" in c and p.get("name") == "t"]
    assert drops, "projection must be dropped on __exit__"


def test_wcc_streams_components_and_writes_run_meta():
    rows = [
        {"eid": "4:abc:1", "name": "Microsoft", "componentId": 0},
        {"eid": "4:abc:2", "name": "Microsoft Corp.", "componentId": 0},
        {"eid": "4:abc:3", "name": "Chipotle", "componentId": 7},
    ]
    store = FakeGraphStore({"gds.wcc.stream": rows})
    s = GDSSession(store, name="er")
    out = s.wcc(workspace_id="mdm-demo")
    assert out == rows
    meta_calls = [(c, p) for c, p in store.calls if "GDSRunMeta" in c]
    assert meta_calls and meta_calls[0][1]["algo"] == "wcc"
    # extra is JSON-encoded: Neo4j properties cannot hold Maps.
    import json
    extra = json.loads(meta_calls[0][1]["extra"])
    assert extra["component_count"] == 2
    assert extra["node_count"] == 3
