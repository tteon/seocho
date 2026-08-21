"""Ontology-drift barrier wiring (seocho-ia4.1): projector stamps the version,
enforce_drift_policy turns detect-into-barrier."""

from __future__ import annotations

from pathlib import Path

import pytest

from seocho.ontology.core import Ontology
from seocho.ontology.context import (
    compile_ontology_context,
    enforce_drift_policy,
    query_ontology_context_mismatch,
)
from seocho.graph_projector import GraphProjector
from seocho.qualification import CanonicalEntityRecord, GraphProjectionSnapshot


class _FakeStore:
    def __init__(self):
        self.nodes = []

    def write(self, nodes, relationships, *, database, workspace_id, source_id=None):
        for n in nodes:
            self.nodes.append(dict(n.get("properties", {})))
        return {"nodes_created": len(nodes), "relationships_created": len(relationships)}

    def query(self, cypher, params=None, database=None):
        ws = (params or {}).get("workspace_id", "default")
        scoped = [n for n in self.nodes
                  if str(n.get("_workspace_id", n.get("workspace_id", ws))) == str(ws)]
        return [{"raw_context_hashes": sorted({str(n.get("_ontology_context_hash", "")) for n in scoped}),
                 "scoped_nodes": len(scoped),
                 "missing_context_nodes": sum(1 for n in scoped if not n.get("_ontology_context_hash"))}]


def _ontos():
    y = (list(Path("examples").rglob("schema.yaml")) or list(Path("examples").rglob("*.yaml")))[0]
    o1, o2 = Ontology.load(str(y)), Ontology.load(str(y))
    del o2.nodes[sorted(o2.nodes.keys())[0]]     # breaking change
    o2.version = "2.0.0"
    return (compile_ontology_context(o1, workspace_id="acme"),
            compile_ontology_context(o2, workspace_id="acme"))


def _snap(n):
    return GraphProjectionSnapshot(
        snapshot_id="s", workspace_id="acme", graph_id="neo4j", database="neo4j",
        entities=[CanonicalEntityRecord(entity_id=f"e{i}", entity_type="Company",
                                        canonical_name=f"c{i}") for i in range(n)])


def test_projector_stamps_ontology_version_when_context_given():
    c1, _ = _ontos()
    store = _FakeStore()
    GraphProjector(graph_store=store, workspace_id="acme").project(
        _snap(3), database="neo4j", ontology_context=c1)
    assert all(n.get("_ontology_context_hash") == c1.descriptor.context_hash for n in store.nodes)


def test_projector_blind_without_context_is_the_old_bug():
    store = _FakeStore()
    GraphProjector(graph_store=store, workspace_id="acme").project(_snap(3), database="neo4j")
    assert all(not n.get("_ontology_context_hash") for n in store.nodes)


def test_barrier_detects_real_drift_and_blocks():
    c1, c2 = _ontos()
    store = _FakeStore()
    GraphProjector(graph_store=store, workspace_id="acme").project(
        _snap(5), database="neo4j", ontology_context=c1)   # data written under v1
    a = query_ontology_context_mismatch(store, c2, workspace_id="acme", database="neo4j")  # active v2
    assert a["mismatch"] is True
    assert enforce_drift_policy(a, policy="block")["blocked"] is True
    with pytest.raises(Exception):
        enforce_drift_policy(a, policy="raise")


def test_barrier_quiet_on_fresh_data_null_control():
    c1, _ = _ontos()
    store = _FakeStore()
    GraphProjector(graph_store=store, workspace_id="acme").project(
        _snap(5), database="neo4j", ontology_context=c1)
    a = query_ontology_context_mismatch(store, c1, workspace_id="acme", database="neo4j")  # same version
    assert a["mismatch"] is False
    assert enforce_drift_policy(a, policy="block")["blocked"] is False
