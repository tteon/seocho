from __future__ import annotations

import sys
from types import ModuleType

from seocho.store.vector import LanceDBVectorStore, create_vector_store


class _FakeEmbeddingBackend:
    def embed(self, texts, *, model=None):
        vectors = []
        for text in texts:
            lowered = str(text).lower()
            if "alpha" in lowered:
                vectors.append([1.0, 0.0])
            elif "beta" in lowered:
                vectors.append([0.0, 1.0])
            else:
                vectors.append([0.5, 0.5])
        return vectors


class _FakeArrowTable:
    def __init__(self, rows):
        self._rows = rows
        self.num_rows = len(rows)

    def to_pylist(self):
        return list(self._rows)


class _FakeQuery:
    def __init__(self, rows, query_vector):
        self._rows = rows
        self._query_vector = query_vector
        self._limit = len(rows)

    def limit(self, value):
        self._limit = value
        return self

    def to_arrow(self):
        def score(row):
            vector = row["vector"]
            return sum(left * right for left, right in zip(self._query_vector, vector))

        rows = sorted(self._rows, key=score, reverse=True)[: self._limit]
        return _FakeArrowTable(
            [{**row, "_distance": float(score(row))} for row in rows]
        )


class _FakeLanceTable:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def add(self, rows):
        self.rows.extend(rows)

    def search(self, vector):
        return _FakeQuery(self.rows, vector)

    def delete(self, where):
        needle = where.split("=")[1].strip().strip("'")
        self.rows = [row for row in self.rows if row["id"] != needle]

    def count_rows(self):
        return len(self.rows)


class _FakeLanceDB:
    def __init__(self):
        self.tables = {}

    def open_table(self, name):
        if name not in self.tables:
            raise KeyError(name)
        return self.tables[name]

    def create_table(self, name, data):
        table = _FakeLanceTable(rows=data)
        self.tables[name] = table
        return table


def test_lancedb_vector_store_round_trip(monkeypatch):
    module = ModuleType("lancedb")
    fake_db = _FakeLanceDB()
    module.connect = lambda uri, **kwargs: fake_db
    monkeypatch.setitem(sys.modules, "lancedb", module)

    store = LanceDBVectorStore(
        uri="/tmp/fake-lancedb",
        table_name="docs",
        embedding_backend=_FakeEmbeddingBackend(),
        model="fake-embed",
    )
    store.add("doc-alpha", "alpha report", metadata={"kind": "finance"})
    store.add("doc-beta", "beta report", metadata={"kind": "risk"})

    results = store.search("alpha question", limit=1)

    assert results[0].id == "doc-alpha"
    assert results[0].metadata == {"kind": "finance"}
    assert store.count() == 2
    assert store.delete("doc-alpha") is True
    assert store.count() == 1


def test_create_vector_store_supports_lancedb(monkeypatch):
    module = ModuleType("lancedb")
    module.connect = lambda uri, **kwargs: _FakeLanceDB()
    monkeypatch.setitem(sys.modules, "lancedb", module)

    store = create_vector_store(
        kind="lancedb",
        uri="/tmp/fake-lancedb",
        table_name="docs",
        embedding_backend=_FakeEmbeddingBackend(),
        model="fake-embed",
    )

    assert isinstance(store, LanceDBVectorStore)
