"""Last-writer-wins-with-timestamps on graph writes (seocho-4rg, Lamport).

Offline: writes stamp _writer_ts/_writer_agent and the MERGE carries the LWW
guard. Live (DozerDB, skipped without it): a stale write cannot clobber a node
that already carries a newer _writer_ts — the ordering primitive the
reflection/escalation retry loop assumes.
"""

from __future__ import annotations

import os

import pytest

from seocho.store.graph import Neo4jGraphStore


# --------------------------------------------------------------------------- #
# Offline — stamping + guard wiring (mock driver, no DB)
# --------------------------------------------------------------------------- #

class _Rec:
    def __init__(self):
        self.calls = []

    def run(self, query, **params):
        self.calls.append((query, params))

        class _R:
            def single(self_inner):
                return None

            def __iter__(self_inner):
                return iter([])

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


def _store_with_fake():
    store = Neo4jGraphStore("bolt://unit-test:7687", "neo4j", "p")
    store._driver = _FakeDriver()
    return store


def test_node_write_stamps_writer_ts_and_lww_guard():
    store = _store_with_fake()
    store.write(
        [{"id": "c1", "label": "Company", "properties": {"name": "ACME"}}],
        [],
        database="testdb",
        source_id="src1",
    )
    node_calls = [c for c in store._driver.rec.calls if "MERGE (n:" in c[0]]
    assert node_calls, "no node MERGE issued"
    query, params = node_calls[0]
    # LWW guard present in the Cypher
    assert "_writer_ts" in query and "CASE WHEN" in query and "ELSE {}" in query
    # incoming row carries a writer timestamp + agent
    row = params["rows"][0]
    assert isinstance(row["props"]["_writer_ts"], float)
    assert row["props"]["_writer_agent"] == "src1"


def test_relationship_write_is_lww_guarded():
    store = _store_with_fake()
    store.write(
        [{"id": "a", "label": "Company", "properties": {}},
         {"id": "b", "label": "Company", "properties": {}}],
        [{"source": "a", "target": "b", "type": "REPORTED", "properties": {}}],
        database="testdb",
        source_id="src1",
    )
    rel_calls = [c for c in store._driver.rec.calls if "MERGE (a)-[r:" in c[0]]
    assert rel_calls, "no rel MERGE issued"
    query, params = rel_calls[0]
    assert "r._writer_ts" in query and "CASE WHEN" in query
    assert params["rows"][0]["props"]["_writer_ts"] is not None


# --------------------------------------------------------------------------- #
# Live (DozerDB) — stale write is ignored
# --------------------------------------------------------------------------- #

def _live_store():
    pw = os.getenv("NEO4J_PASSWORD", "seocho-dev")
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    try:
        store = Neo4jGraphStore(uri, os.getenv("NEO4J_USER", "neo4j"), pw)
        with store._driver.session(database="neo4j") as s:
            s.run("RETURN 1").single()
        return store
    except Exception:
        return None


@pytest.mark.integration
def test_stale_write_does_not_clobber_newer_live():
    store = _live_store()
    if store is None:
        pytest.skip("DozerDB not reachable")
    db = "neo4j"
    nid = "lww_itest_node"
    try:
        # seed a node with a FAR-FUTURE writer ts + sentinel value
        with store._driver.session(database=db) as s:
            s.run(
                "MERGE (n:_LWWTest {id:$id}) SET n._writer_ts=9e18, n.v='keep', n._source_id='lww'",
                id=nid,
            )
        # a normal write (now << 9e18) must NOT overwrite v
        store.write(
            [{"id": nid, "label": "_LWWTest", "properties": {"v": "clobbered"}}],
            [], database=db, source_id="lww",
        )
        with store._driver.session(database=db) as s:
            v = s.run("MATCH (n:_LWWTest {id:$id}) RETURN n.v AS v", id=nid).single()["v"]
        assert v == "keep", f"stale write clobbered a newer node: v={v!r}"
    finally:
        with store._driver.session(database=db) as s:
            s.run("MATCH (n:_LWWTest {id:$id}) DETACH DELETE n", id=nid)
        store.close()


# --------------------------------------------------------------------------- #
# issue #183 — multi-document provenance (_sources accumulation + safe delete)
# --------------------------------------------------------------------------- #

def test_node_write_accumulates_sources_outside_lww_guard():
    store = _store_with_fake()
    store.write(
        [{"id": "c1", "label": "Company", "properties": {"name": "ACME"}}],
        [],
        database="testdb",
        source_id="doc-a",
    )
    node_calls = [c for c in store._driver.rec.calls if "MERGE (n:" in c[0]]
    query, _params = node_calls[0]
    # _sources accumulation is a separate SET after the LWW-guarded one:
    # NULL -> seed list, missing -> append, present -> keep (idempotent).
    assert "SET n._sources = CASE WHEN n._sources IS NULL" in query
    assert "n._sources + row.props._source_id" in query


def test_rel_write_accumulates_sources():
    store = _store_with_fake()
    store.write(
        [{"id": "a", "label": "Company", "properties": {}},
         {"id": "b", "label": "Company", "properties": {}}],
        [{"source": "a", "target": "b", "type": "REPORTED", "properties": {}}],
        database="testdb",
        source_id="doc-a",
    )
    rel_calls = [c for c in store._driver.rec.calls if "MERGE (a)-[r:" in c[0]]
    query, _params = rel_calls[0]
    assert "SET r._sources = CASE WHEN r._sources IS NULL" in query


def test_delete_by_source_retires_before_deleting():
    store = _store_with_fake()
    store.delete_by_source("doc-a", database="testdb")
    calls = [q for q, _ in store._driver.rec.calls]
    retire = [q for q in calls if "SET n._sources = rest" in q]
    delete = [q for q in calls if "DETACH DELETE n" in q]
    assert retire, "no retire pass issued"
    assert delete, "no delete pass issued"
    # retire only touches multi-source nodes; delete only sole-source/legacy
    assert "size([s IN n._sources WHERE s <> $sid]) > 0" in retire[0]
    assert "size([s IN n._sources WHERE s <> $sid]) = 0" in delete[0]
    assert "n._sources IS NULL AND n._source_id = $sid" in delete[0]
    # retire runs before delete
    assert calls.index(retire[0]) < calls.index(delete[0])


# --------------------------------------------------------------------------- #
# seocho-uxs.1 — merge_conflicts surfacing on the node MERGE
# --------------------------------------------------------------------------- #

class _ConflictRec(_Rec):
    """Fake whose node-MERGE returns one conflict record."""

    def run(self, query, **params):
        self.calls.append((query, params))
        is_node_merge = "MERGE (n:" in query and "RETURN row.id AS id" in query

        class _R:
            def single(self_inner):
                return None

            def __iter__(self_inner):
                if is_node_merge:
                    return iter([
                        {"id": "c1", "conflicts": [
                            {"property": "value", "existing": "2.1B", "incoming": "96.8B"}
                        ]}
                    ])
                return iter([])

        return _R()


def test_node_merge_query_computes_and_returns_conflicts():
    store = _store_with_fake()
    store.write(
        [{"id": "c1", "label": "Company", "properties": {"name": "ACME", "value": "96.8B"}}],
        [], database="testdb", source_id="doc-b",
    )
    node_calls = [c for c in store._driver.rec.calls if "MERGE (n:" in c[0]]
    query = node_calls[0][0]
    # conflict pre-SET capture + RETURN are wired into the batch query
    assert "_conflicts" in query and "RETURN row.id AS id" in query
    assert "k <> 'id'" in query and "NOT k STARTS WITH '_'" in query


def test_merge_conflicts_collected_into_summary():
    store = Neo4jGraphStore("bolt://unit-test:7687", "neo4j", "p")
    store._driver = _FakeDriver()
    store._driver.rec = _ConflictRec()
    summary = store.write(
        [{"id": "c1", "label": "Company", "properties": {"name": "ACME", "value": "96.8B"}}],
        [], database="testdb", source_id="doc-b",
    )
    assert summary["merge_conflicts"] == [
        {"label": "Company", "key": "c1", "property": "value",
         "existing": "2.1B", "incoming": "96.8B", "source_id": "doc-b"}
    ]
    assert summary["nodes_created"] == 1
