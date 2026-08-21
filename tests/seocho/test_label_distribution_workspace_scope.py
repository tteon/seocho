"""Regression for #132 — LPGAgent._label_distribution fallback must scope by
workspace_id like the other resolver queries. It ran `MATCH (n) ...` with no
workspace predicate, so the fallback returned label counts aggregated across
every workspace in the database.
"""

from __future__ import annotations

from seocho.query.semantic_agents import LPGAgent


class _CapturingConnector:
    def __init__(self):
        self.calls = []

    def query(self, query, params=None, database=None):
        self.calls.append({"query": query, "params": params, "database": database})
        return []


def test_label_distribution_query_is_workspace_scoped():
    conn = _CapturingConnector()
    agent = LPGAgent(connector=conn)

    agent._label_distribution(["neo4j"], workspace_id="ws-a")

    assert conn.calls, "expected the fallback query to run"
    call = conn.calls[0]
    # the query carries a workspace predicate (not a bare MATCH (n))
    assert "_workspace_id" in call["query"]
    assert "$workspace_id" in call["query"]
    # and the workspace id is bound as a parameter
    assert call["params"] == {"workspace_id": "ws-a"}


def test_label_distribution_empty_workspace_disables_filter():
    # Empty workspace_id keeps the cross-workspace behavior intentionally
    # (same convention as the read-filter), via the `$workspace_id = ''` branch.
    conn = _CapturingConnector()
    agent = LPGAgent(connector=conn)
    agent._label_distribution(["neo4j"], workspace_id="")
    assert conn.calls[0]["params"] == {"workspace_id": ""}
    assert "$workspace_id = ''" in conn.calls[0]["query"]
