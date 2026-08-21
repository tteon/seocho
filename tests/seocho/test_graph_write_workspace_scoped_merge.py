"""Workspace-scoped node/rel MERGE (review #6): node identity is (id, _workspace_id),
so two tenants' identical id (e.g. the cross-source ~xs|<name>) never merge onto one
physical node in a shared graph, and a relationship never bridges two tenants' nodes.

Offline (fake driver): asserts the generated Cypher carries `_workspace_id: $ws` in
every MERGE/MATCH and that `ws=workspace_id` is bound.
"""

from __future__ import annotations

from seocho.store.graph import Neo4jGraphStore


class _Rec:
    def __init__(self):
        self.calls = []

    def run(self, query, **params):
        self.calls.append((query, params))

        class _R:
            def single(self_inner):
                return None

            def __iter__(self_inner):
                return iter([{"id": params.get("id", ""), "conflicts": []}])

        return _R()


class _SessCtx:
    def __init__(self, rec):
        self._rec = rec

    def __enter__(self):
        return self._rec

    def __exit__(self, *a):
        return False


class _FakeDriver:
    def __init__(self):
        self.rec = _Rec()

    def session(self, database=None, **kw):
        return _SessCtx(self.rec)

    def close(self):
        pass


def _store():
    s = Neo4jGraphStore("bolt://unit-test:7687", "neo4j", "p")
    s._driver = _FakeDriver()
    return s


def test_node_merge_is_workspace_scoped():
    s = _store()
    s.write([{"id": "~xs|acme corp", "label": "Company", "properties": {"name": "Acme Corp"}}],
            [], workspace_id="acme")
    node_calls = [(q, p) for q, p in s._driver.rec.calls if "MERGE (n:" in q]
    assert node_calls, "a node MERGE ran"
    q, p = node_calls[0]
    assert "_workspace_id: $ws" in q, "node identity must include _workspace_id"
    assert p.get("ws") == "acme"


def test_relationship_endpoints_are_workspace_scoped():
    s = _store()
    s.write(
        [{"id": "a", "label": "Company", "properties": {"name": "A"}},
         {"id": "b", "label": "Company", "properties": {"name": "B"}}],
        [{"source": "a", "target": "b", "type": "RELATED_TO", "properties": {}}],
        workspace_id="acme",
    )
    rel_calls = [(q, p) for q, p in s._driver.rec.calls if "-[r:" in q and "MATCH" in q]
    assert rel_calls, "a relationship MATCH+MERGE ran"
    q, p = rel_calls[0]
    assert q.count("_workspace_id: $ws") >= 2, "both endpoints scoped by workspace"
    assert p.get("ws") == "acme"


def test_two_tenants_same_id_bind_distinct_workspace():
    """The same source-agnostic id written under two tenants carries a different
    ws each — so the DB keys them as distinct (id, _workspace_id) nodes."""
    s = _store()
    s.write([{"id": "~xs|acme corp", "label": "Company", "properties": {"name": "Acme Corp"}}],
            [], workspace_id="acme")
    s.write([{"id": "~xs|acme corp", "label": "Company", "properties": {"name": "Acme Corp"}}],
            [], workspace_id="globex")
    node_calls = [p.get("ws") for q, p in s._driver.rec.calls if "MERGE (n:" in q]
    assert node_calls == ["acme", "globex"]
