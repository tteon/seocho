"""Scheduling: cheap point lookups take the light lane (seocho-ia4 D4).

Cross-tenant fairness is STRUCTURAL — a SeochoOS (and its LaneScheduler) is one
instance per (workspace, database), so one tenant's storm consumes only its own
permits (the flow review's "one tenant starves co-tenants" does not apply to this
instance model). The remaining, real scheduling-quality fix is within a workspace:
a fan-out's burst of id-equality+LIMIT-1 resolves must not flood the heavy lane on
EWMA cold-start.
"""

from __future__ import annotations

from seocho.operating_layer import LaneScheduler, _is_cheap_point_lookup


def test_point_lookup_shape_detection():
    assert _is_cheap_point_lookup(
        "MATCH (n {id: $addr, _workspace_id: $ws}) RETURN n LIMIT 1")
    assert _is_cheap_point_lookup("MATCH (n) WHERE n.id = $x RETURN n.name LIMIT 1")
    # a scan / aggregation is NOT a cheap point lookup
    assert not _is_cheap_point_lookup(
        "MATCH (a)-[r]->(b) RETURN count(r) LIMIT 1")
    assert not _is_cheap_point_lookup(
        "MATCH (n:Company {_workspace_id: $ws}) RETURN n ORDER BY n.name LIMIT 1")
    assert not _is_cheap_point_lookup(          # no LIMIT 1 => not obviously cheap
        "MATCH (n {id: $x}) RETURN n LIMIT $limit")


def test_light_lane_presence_gates_the_fast_path():
    with_light = LaneScheduler(max_inflight=8, light_permits=4)
    assert with_light.has_light_lane is True
    single = LaneScheduler(max_inflight=8)      # light_permits=0 -> single lane
    assert single.has_light_lane is False


def test_cross_tenant_scheduler_isolation_is_structural():
    """Two workspaces get two SeochoOS instances -> two LaneSchedulers -> one
    tenant's inflight never consumes another's permits (no starvation by design)."""
    from seocho.operating_layer import SeochoOS
    from seocho.ontology import Ontology

    class _G:  # minimal graph store; we never execute here
        def query(self, *a, **k):
            return []

    onto = Ontology("t", package_id="t", version="1.0.0", nodes={})
    a = SeochoOS(graph_store=_G(), ontology=onto, database="neo4j", workspace_id="acme",
                 max_inflight=4, light_permits=2)
    b = SeochoOS(graph_store=_G(), ontology=onto, database="neo4j", workspace_id="globex",
                 max_inflight=4, light_permits=2)
    assert a._admission is not b._admission, "each workspace has its own scheduler"
