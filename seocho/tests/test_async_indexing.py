"""Tests for async indexing path: acomplete_with_task_hints, aextract, aindex, index_batch(max_workers)."""

import asyncio
from typing import Any, Dict

import pytest

from seocho.index.extraction_engine import CanonicalExtractionEngine
from seocho.index.pipeline import IndexingPipeline
from seocho.indexing import BatchIndexingResult
from seocho.ontology import NodeDef, Ontology, P, RelDef
from seocho.store.llm import acomplete_with_task_hints


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = payload
        self.usage = None

    def json(self) -> Dict[str, Any]:
        return self._payload


class _AsyncFakeLLM:
    """Fake LLM that implements both complete() and acomplete()."""

    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = payload
        self.complete_calls: int = 0
        self.acomplete_calls: int = 0

    def complete(self, *, system, user, temperature, response_format=None,
                 reasoning_mode=None, task_hint=None):
        self.complete_calls += 1
        return _FakeResponse(self._payload)

    async def acomplete(self, *, system, user, temperature, response_format=None,
                        reasoning_mode=None, task_hint=None):
        self.acomplete_calls += 1
        return _FakeResponse(self._payload)


class _FakeGraphStore:
    def __init__(self) -> None:
        self.written_nodes: list = []
        self.written_rels: list = []
        self.write_calls: int = 0

    def write(self, nodes, relationships, *, database="neo4j",
              workspace_id="default", source_id=""):
        self.write_calls += 1
        self.written_nodes.extend(nodes)
        self.written_rels.extend(relationships)
        return {
            "nodes_created": len(nodes),
            "relationships_created": len(relationships),
            "errors": [],
        }


def _simple_ontology() -> Ontology:
    return Ontology(
        name="test",
        nodes={"Company": NodeDef(properties={"name": P(str, unique=True)})},
        relationships={"RELATED_TO": RelDef(source="Company", target="Company")},
    )


def _extraction_payload() -> Dict[str, Any]:
    return {
        "nodes": [
            {"id": "acme", "label": "Company", "properties": {"name": "Acme Corp"}},
        ],
        "relationships": [],
    }


# ---------------------------------------------------------------------------
# acomplete_with_task_hints
# ---------------------------------------------------------------------------


class TestAcompleteWithTaskHints:
    def test_calls_acomplete(self):
        llm = _AsyncFakeLLM(_extraction_payload())
        response = asyncio.run(
            acomplete_with_task_hints(
                llm,
                system="sys",
                user="user",
                temperature=0.0,
                task_hint="json_extraction",
            )
        )
        assert response.json()["nodes"][0]["id"] == "acme"
        assert llm.acomplete_calls == 1
        assert llm.complete_calls == 0

    def test_strips_unsupported_kwargs_on_type_error(self):
        """If the backend raises TypeError for unknown kwargs, they are stripped and retried."""

        class _StrictLLM:
            calls = 0

            async def acomplete(self, *, system, user, temperature):
                self.calls += 1
                return _FakeResponse({"nodes": [], "relationships": []})

        llm = _StrictLLM()
        result = asyncio.run(
            acomplete_with_task_hints(
                llm,
                system="s",
                user="u",
                temperature=0.0,
                reasoning_mode=False,
                task_hint="json_extraction",
            )
        )
        assert llm.calls == 1
        assert result.json() == {"nodes": [], "relationships": []}


# ---------------------------------------------------------------------------
# CanonicalExtractionEngine.aextract
# ---------------------------------------------------------------------------


class TestAextract:
    def test_aextract_uses_acomplete(self):
        llm = _AsyncFakeLLM(_extraction_payload())
        engine = CanonicalExtractionEngine(ontology=_simple_ontology(), llm=llm)

        result = asyncio.run(
            engine.aextract("Acme Corp is a company.", category="general")
        )

        assert result["nodes"][0]["label"] == "Company"
        assert llm.acomplete_calls >= 1
        assert llm.complete_calls == 0

    def test_aextract_returns_same_shape_as_extract(self):
        """aextract output schema must match extract output schema."""
        llm = _AsyncFakeLLM(_extraction_payload())
        engine = CanonicalExtractionEngine(ontology=_simple_ontology(), llm=llm)

        sync_result = engine.extract("Acme Corp is a company.", category="general")
        async_result = asyncio.run(
            engine.aextract("Acme Corp is a company.", category="general")
        )

        assert set(sync_result.keys()) == set(async_result.keys())
        assert len(async_result["nodes"]) == len(sync_result["nodes"])

    def test_aextract_relaxed_retry_on_empty(self):
        """When first call returns empty graph, aextract retries in relaxed mode."""
        empty_payload = {"nodes": [], "relationships": []}
        non_empty_payload = _extraction_payload()

        class _TwoShotLLM:
            def __init__(self):
                self.calls = 0

            async def acomplete(self, *, system, user, temperature,
                                response_format=None, reasoning_mode=None, task_hint=None):
                self.calls += 1
                if self.calls == 1:
                    return _FakeResponse(empty_payload)
                return _FakeResponse(non_empty_payload)

        llm = _TwoShotLLM()
        engine = CanonicalExtractionEngine(ontology=_simple_ontology(), llm=llm)
        result = asyncio.run(engine.aextract("some text"))

        assert llm.calls == 2
        assert result["nodes"]  # retry populated nodes


# ---------------------------------------------------------------------------
# IndexingPipeline.aindex
# ---------------------------------------------------------------------------


class TestAindex:
    def test_aindex_returns_indexing_result(self):
        llm = _AsyncFakeLLM(_extraction_payload())
        store = _FakeGraphStore()
        pipeline = IndexingPipeline(
            ontology=_simple_ontology(),
            graph_store=store,
            llm=llm,
        )

        result = asyncio.run(pipeline.aindex("Acme Corp operates globally."))

        assert result.chunks_processed >= 1
        assert store.write_calls >= 1

    def test_aindex_uses_acomplete_not_complete(self):
        """aindex extraction uses acomplete; linking may still use complete (out of scope)."""
        llm = _AsyncFakeLLM(_extraction_payload())
        store = _FakeGraphStore()
        pipeline = IndexingPipeline(
            ontology=_simple_ontology(),
            graph_store=store,
            llm=llm,
        )

        asyncio.run(pipeline.aindex("Acme Corp operates globally."))

        # Extraction must go through acomplete; link() still uses sync complete (out of scope).
        assert llm.acomplete_calls >= 1

    def test_aindex_result_matches_sync_index_shape(self):
        """aindex and index must produce results with the same fields."""
        llm_sync = _AsyncFakeLLM(_extraction_payload())
        llm_async = _AsyncFakeLLM(_extraction_payload())
        store_sync = _FakeGraphStore()
        store_async = _FakeGraphStore()

        pipeline_sync = IndexingPipeline(
            ontology=_simple_ontology(), graph_store=store_sync, llm=llm_sync
        )
        pipeline_async = IndexingPipeline(
            ontology=_simple_ontology(), graph_store=store_async, llm=llm_async
        )

        sync_result = pipeline_sync.index("Acme Corp operates globally.")
        async_result = asyncio.run(pipeline_async.aindex("Acme Corp operates globally."))

        assert sync_result.chunks_processed == async_result.chunks_processed
        assert sync_result.ok == async_result.ok


# ---------------------------------------------------------------------------
# IndexingPipeline.index_batch(max_workers)
# ---------------------------------------------------------------------------


class TestIndexBatchParallel:
    def test_max_workers_1_is_sequential(self):
        """max_workers=1 must use the sync path (no acomplete calls)."""
        llm = _AsyncFakeLLM(_extraction_payload())
        store = _FakeGraphStore()
        pipeline = IndexingPipeline(
            ontology=_simple_ontology(), graph_store=store, llm=llm
        )

        batch = pipeline.index_batch(
            ["Document one.", "Document two."],
            max_workers=1,
        )

        assert isinstance(batch, BatchIndexingResult)
        assert batch.total_documents == 2
        assert llm.complete_calls >= 1
        assert llm.acomplete_calls == 0

    def test_max_workers_gt1_uses_acomplete(self):
        """max_workers > 1 must use the async extraction path (acomplete called for extraction)."""
        llm = _AsyncFakeLLM(_extraction_payload())
        store = _FakeGraphStore()
        pipeline = IndexingPipeline(
            ontology=_simple_ontology(), graph_store=store, llm=llm
        )

        batch = pipeline.index_batch(
            ["Document one.", "Document two."],
            max_workers=2,
        )

        assert isinstance(batch, BatchIndexingResult)
        assert batch.total_documents == 2
        # Extraction must use acomplete; link() still uses sync complete (out of scope).
        assert llm.acomplete_calls >= 1

    def test_parallel_and_sequential_produce_same_document_count(self):
        docs = ["Alpha Corp is growing.", "Beta Inc reported losses.", "Gamma Ltd expanded."]

        def _make_pipeline():
            return IndexingPipeline(
                ontology=_simple_ontology(),
                graph_store=_FakeGraphStore(),
                llm=_AsyncFakeLLM(_extraction_payload()),
            )

        batch_seq = _make_pipeline().index_batch(docs, max_workers=1)
        batch_par = _make_pipeline().index_batch(docs, max_workers=3)

        assert batch_seq.total_documents == batch_par.total_documents == 3
        assert batch_seq.successful == batch_par.successful

    def test_backward_compatible_default(self):
        """Calling index_batch without max_workers must work as before."""
        llm = _AsyncFakeLLM(_extraction_payload())
        store = _FakeGraphStore()
        pipeline = IndexingPipeline(
            ontology=_simple_ontology(), graph_store=store, llm=llm
        )

        batch = pipeline.index_batch(["Single document."])

        assert batch.total_documents == 1
        assert llm.complete_calls >= 1
        assert llm.acomplete_calls == 0
