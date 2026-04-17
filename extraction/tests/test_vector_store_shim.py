import os
import pickle
import sys
from types import SimpleNamespace


ROOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..")
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


import extraction.vector_store as vector_store_module
import seocho.store.vector as canonical_vector_store


class _FakeFaissIndex:
    def __init__(self) -> None:
        self.ntotal = 0


class _FakeFaiss:
    def write_index(self, index, path):  # noqa: ANN001
        with open(path, "wb") as fh:
            pickle.dump({"ntotal": index.ntotal}, fh)

    def read_index(self, path):  # noqa: ANN001
        with open(path, "rb") as fh:
            payload = pickle.load(fh)
        index = _FakeFaissIndex()
        index.ntotal = int(payload["ntotal"])
        return index


class _FakeEmbeddingBackend:
    def embed(self, texts, *, model=None):  # noqa: ANN001
        return [[1.0, 0.0, 0.0] for _ in texts]


class _FakeCanonicalStore:
    def __init__(self, **kwargs):  # noqa: ANN003, ANN001
        self.kwargs = dict(kwargs)
        self._embedding_backend = _FakeEmbeddingBackend()
        self._model = "test-embedding-model"
        self._index = _FakeFaissIndex()
        self._docs = []
        self._id_to_idx = {}

    def _embed(self, texts):  # noqa: ANN001
        return self._embedding_backend.embed(texts, model=self._model)

    def add(self, doc_id, text, *, metadata=None):  # noqa: ANN001
        idx = len(self._docs)
        self._docs.append({"id": doc_id, "text": text, "metadata": metadata or {}})
        self._id_to_idx[doc_id] = idx
        self._index.ntotal = len(self._docs)

    def search(self, query, *, limit=5):  # noqa: ANN001
        items = []
        for doc in self._docs[:limit]:
            items.append(
                SimpleNamespace(
                    doc_id=doc["id"],
                    text=doc["text"],
                    metadata=dict(doc.get("metadata", {})),
                )
            )
        return items


def test_vector_store_shim_uses_canonical_embedding_backend(monkeypatch) -> None:
    monkeypatch.setattr(canonical_vector_store, "FAISSVectorStore", _FakeCanonicalStore)

    store = vector_store_module.VectorStore(api_key="test", dimension=3)
    store.add_document("doc-1", "hello world")

    assert store.embed_text("hello") == [1.0, 0.0, 0.0]
    assert store.search("hello", k=1) == [{"id": "doc-1", "text": "hello world"}]
    assert store.doc_map == {0: "doc-1"}
    assert store._store.kwargs == {"api_key": "test", "dimension": 3}


def test_vector_store_shim_persists_and_restores_metadata(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(canonical_vector_store, "FAISSVectorStore", _FakeCanonicalStore)
    monkeypatch.setitem(sys.modules, "faiss", _FakeFaiss())

    store = vector_store_module.VectorStore(api_key="test", dimension=3)
    store.add_document("doc-1", "hello world")
    store.save_index(str(tmp_path))

    restored = vector_store_module.VectorStore(api_key="test", dimension=3)
    restored.load_index(str(tmp_path))

    assert restored.doc_map == {0: "doc-1"}
    assert restored.documents == [{"id": "doc-1", "text_preview": "hello world"}]
    assert restored._store._index.ntotal == 1
