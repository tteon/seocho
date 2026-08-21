"""The canonical planner-path executor enforces workspace scope at the store.

#613 closed the agent-facing execute_cypher tool; this closes the same gap on
GraphQueryExecutor, which every deterministic-engine plan flows through. A
workspace-bound executor must pass workspace_id + enforce_workspace_filter=True
so the store refuses unscoped Cypher; a workspace-less executor keeps the
legacy call shape (embedded/local single-tenant callers).
"""

from __future__ import annotations

from seocho.query.contracts import QueryPlan
from seocho.query.executor import GraphQueryExecutor


class _RecordingStore:
    def __init__(self):
        self.kwargs = None

    def query(self, cypher, **kwargs):
        self.kwargs = kwargs
        return [{"ok": 1}]


SCOPED = "MATCH (c:Company {_workspace_id: $workspace_id}) RETURN c LIMIT 5"


def test_workspace_bound_executor_enforces_at_the_store():
    store = _RecordingStore()
    ex = GraphQueryExecutor(graph_store=store, database="neo4j", workspace_id="acme")
    result = ex.execute(QueryPlan(question="q", cypher=SCOPED, params={"workspace_id": "acme"}))
    assert result.error is None and result.records == [{"ok": 1}]
    assert store.kwargs["workspace_id"] == "acme"
    assert store.kwargs["enforce_workspace_filter"] is True


def test_workspaceless_executor_keeps_legacy_call_shape():
    store = _RecordingStore()
    ex = GraphQueryExecutor(graph_store=store, database="neo4j")
    ex.execute(QueryPlan(question="q", cypher=SCOPED, params={}))
    assert "workspace_id" not in store.kwargs
    assert "enforce_workspace_filter" not in store.kwargs
