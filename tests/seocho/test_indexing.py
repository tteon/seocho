"""Tests for seocho.indexing — chunking, dedup, pipeline."""

import pytest

from seocho.client import Seocho
from seocho.indexing import (
    BatchIndexingResult,
    IndexingResult,
    chunk_text,
    content_hash,
)
from seocho.index.pipeline import IndexingPipeline
from seocho.ontology import NodeDef, Ontology, P, RelDef


class TestChunking:
    def test_short_text_no_split(self):
        chunks = chunk_text("short text", max_chars=100)
        assert len(chunks) == 1
        assert chunks[0] == "short text"

    def test_long_text_splits(self):
        text = "\n\n".join(f"Paragraph {i} with some content." for i in range(20))
        chunks = chunk_text(text, max_chars=100, overlap_chars=20)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 150  # allow some flex due to paragraph boundaries

    def test_overlap_preserves_context(self):
        text = "para one content here\n\npara two content here\n\npara three content"
        chunks = chunk_text(text, max_chars=30, overlap_chars=10)
        assert len(chunks) >= 2
        # Overlap means later chunks start with tail of previous
        if len(chunks) > 1:
            assert chunks[1][:5] != "para "  # starts with overlap, not fresh

    def test_empty_text(self):
        chunks = chunk_text("")
        assert len(chunks) == 1
        assert chunks[0] == ""

    def test_no_paragraphs(self):
        text = "Single long line " * 100
        chunks = chunk_text(text, max_chars=200)
        assert len(chunks) == 1  # no \n\n separator found

    def test_custom_separator(self):
        text = "a. sentence one. sentence two. sentence three."
        chunks = chunk_text(text, max_chars=20, separator=". ")
        assert len(chunks) >= 2


class TestContentHash:
    def test_case_insensitive(self):
        assert content_hash("Hello World") == content_hash("hello world")

    def test_whitespace_normalized(self):
        assert content_hash("  hello   world  ") == content_hash("hello world")

    def test_different_content(self):
        assert content_hash("alpha") != content_hash("beta")

    def test_deterministic(self):
        h1 = content_hash("test")
        h2 = content_hash("test")
        assert h1 == h2

    def test_length(self):
        h = content_hash("anything")
        assert len(h) == 16  # sha256[:16]


class TestIndexingResult:
    def test_ok_when_no_errors(self):
        r = IndexingResult(chunks_processed=1, total_nodes=5)
        assert r.ok is True

    def test_not_ok_when_write_errors(self):
        r = IndexingResult(chunks_processed=1, write_errors=["fail"])
        assert r.ok is False

    def test_not_ok_when_no_chunks(self):
        r = IndexingResult(chunks_processed=0)
        assert r.ok is False

    def test_to_dict(self):
        r = IndexingResult(source_id="abc", chunks_processed=2, total_nodes=3)
        d = r.to_dict()
        assert d["source_id"] == "abc"
        assert d["ok"] is True
        assert d["total_nodes"] == 3


class TestBatchIndexingResult:
    def test_ok_when_no_failures(self):
        b = BatchIndexingResult(total_documents=2, successful=2)
        assert b.ok is True

    def test_not_ok_when_failures(self):
        b = BatchIndexingResult(total_documents=2, successful=1, failed=1)
        assert b.ok is False

    def test_to_dict(self):
        b = BatchIndexingResult(total_documents=3, successful=2, failed=1, skipped=0)
        d = b.to_dict()
        assert d["total_documents"] == 3
        assert d["ok"] is False
        assert isinstance(d["results"], list)


class TestExtractionNormalization:
    def test_normalizes_name_only_nodes_and_from_to_relationships(self):
        ontology = Ontology(
            name="finder",
            nodes={
                "Company": NodeDef(properties={"name": P(str, unique=True), "sector": P(str)}),
                "FinancialMetric": NodeDef(properties={"name": P(str), "value": P(str), "year": P(str)}),
            },
            relationships={
                "REPORTED": RelDef(source="Company", target="FinancialMetric"),
            },
        )
        pipeline = IndexingPipeline(
            ontology=ontology,
            graph_store=object(),
            llm=object(),
        )

        normalized = pipeline._normalize_extraction_payload(
            {
                "nodes": [
                    {"name": "Cboe Global Markets, Inc.", "sector": "Financial Services"},
                    {"name": "Revenue - Data and access solutions 2023", "value": 539.2, "year": 2023},
                ],
                "relationships": [
                    {
                        "from": "Cboe Global Markets, Inc.",
                        "to": "Revenue - Data and access solutions 2023",
                        "type": "REPORTED",
                    }
                ],
            }
        )

        assert normalized["nodes"][0]["label"] == "Company"
        assert normalized["nodes"][1]["label"] == "FinancialMetric"
        assert normalized["relationships"][0]["source"] == normalized["nodes"][0]["id"]
        assert normalized["relationships"][0]["target"] == normalized["nodes"][1]["id"]

    def test_linking_does_not_drop_original_relationships_when_linker_returns_none(self):
        ontology = Ontology(
            name="finder",
            nodes={
                "Company": NodeDef(properties={"name": P(str, unique=True), "sector": P(str)}),
                "FinancialMetric": NodeDef(properties={"name": P(str), "value": P(str), "year": P(str)}),
            },
            relationships={"REPORTED": RelDef(source="Company", target="FinancialMetric")},
        )

        class FakeResponse:
            def __init__(self, payload):
                self._payload = payload
                self.usage = None

            def json(self):
                return self._payload

        class FakeLLM:
            def __init__(self):
                self.calls = 0

            def complete(self, *, system, user, temperature, response_format=None):  # noqa: ANN001
                self.calls += 1
                if self.calls == 1:
                    return FakeResponse(
                        {
                            "nodes": [
                                {"name": "Cboe Global Markets, Inc.", "sector": "Financial Services"},
                                {"name": "Revenue - Data and access solutions 2023", "value": 539.2, "year": 2023},
                            ],
                            "relationships": [
                                {
                                    "from": "Cboe Global Markets, Inc.",
                                    "to": "Revenue - Data and access solutions 2023",
                                    "type": "REPORTED",
                                }
                            ],
                        }
                    )
                return FakeResponse(
                    {
                        "nodes": [
                            {
                                "id": "cboe_global_markets_inc",
                                "label": "Company",
                                "properties": {"name": "Cboe Global Markets, Inc.", "sector": "Financial Services"},
                            }
                        ],
                        "relationships": [],
                    }
                )

        class FakeGraphStore:
            def __init__(self):
                self.last_nodes = []
                self.last_relationships = []

            def write(self, nodes, relationships, *, database="neo4j", workspace_id="default", source_id=""):  # noqa: ANN001
                self.last_nodes = list(nodes)
                self.last_relationships = list(relationships)
                return {"nodes_created": len(nodes), "relationships_created": len(relationships), "errors": []}

        store = FakeGraphStore()
        pipeline = IndexingPipeline(
            ontology=ontology,
            graph_store=store,
            llm=FakeLLM(),
        )

        result = pipeline.index("Cboe data")

        assert result.total_nodes == len(store.last_nodes)
        assert result.total_relationships == len(store.last_relationships)
        assert result.layered_graph_summary is not None
        assert result.layered_graph_summary["chunk_count"] == 1
        company = next(node for node in store.last_nodes if node["label"] == "Company")
        document = next(node for node in store.last_nodes if node["label"] == "Document")
        document_version = next(node for node in store.last_nodes if node["label"] == "DocumentVersion")
        chunk = next(node for node in store.last_nodes if node["label"] == "Chunk")
        # T2.2: domain nodes (Company) no longer carry the document-level
        # content_preview — graph nodes must abstract, not duplicate evidence.
        assert "content_preview" not in company["properties"]
        assert company["properties"]["entity_id"] == "cboe_global_markets_inc"
        assert company["properties"]["class"] == "Company"
        assert company["properties"]["mention_count"] == 1
        assert document["properties"]["content_preview"] == "Cboe data"
        assert document_version["properties"]["chunk_count"] == 1
        assert chunk["properties"]["chunk_id"].endswith("_chunk_0000")
        # Chunk nodes still carry their own short preview (chunk-local, not doc-level)
        assert "content_preview" in chunk["properties"]
        chunk_mention = next(
            rel
            for rel in store.last_relationships
            if rel["type"] == "MENTIONS" and rel["source"] == chunk["id"]
        )
        assert chunk_mention["properties"]["evidence_span"] == "Cboe data"
        assert chunk_mention["properties"]["extraction_run_id"].startswith("run-")
        assert chunk_mention["properties"]["prompt_version"] == "runtime-memory-v1"
        assert chunk_mention["properties"]["role"] == "chunk_evidence"
        assert any(rel["type"] == "MENTIONS" for rel in store.last_relationships)
        assert any(rel["type"] == "HAS_CHUNK" for rel in store.last_relationships)

    def test_empty_extraction_uses_heuristic_fallback_instead_of_skipping_chunk(self):
        ontology = Ontology(
            name="finder",
            nodes={"Company": NodeDef(properties={"name": P(str, unique=True)})},
            relationships={},
        )

        class FakeResponse:
            def __init__(self, payload):
                self._payload = payload
                self.usage = None

            def json(self):
                return self._payload

        class FakeLLM:
            def complete(self, *, system, user, temperature, response_format=None):  # noqa: ANN001
                return FakeResponse({"nodes": [], "relationships": []})

        class FakeGraphStore:
            def __init__(self):
                self.last_nodes = []
                self.last_relationships = []

            def write(self, nodes, relationships, *, database="neo4j", workspace_id="default", source_id=""):  # noqa: ANN001
                self.last_nodes = list(nodes)
                self.last_relationships = list(relationships)
                return {"nodes_created": len(nodes), "relationships_created": len(relationships), "errors": []}

        store = FakeGraphStore()
        pipeline = IndexingPipeline(
            ontology=ontology,
            graph_store=store,
            llm=FakeLLM(),
        )

        result = pipeline.index("ACME expanded into Asia.")

        assert result.fallback_used is True
        assert "EmptyExtraction" in result.fallback_reason
        assert result.chunks_processed == 1
        assert result.total_nodes > 0
        assert any(node["label"] == "Entity" for node in store.last_nodes)

    def test_vector_store_receives_chunk_rows_with_layered_metadata(self):
        ontology = Ontology(
            name="finder",
            nodes={"Company": NodeDef(properties={"name": P(str, unique=True)})},
            relationships={},
        )

        class FakeResponse:
            def __init__(self, payload):
                self._payload = payload
                self.usage = None

            def json(self):
                return self._payload

        class FakeLLM:
            def complete(self, *, system, user, temperature, response_format=None):  # noqa: ANN001
                return FakeResponse(
                    {
                        "nodes": [{"id": "acme", "label": "Company", "properties": {"name": "ACME"}}],
                        "relationships": [],
                    }
                )

        class FakeGraphStore:
            def write(self, nodes, relationships, *, database="neo4j", workspace_id="default", source_id=""):  # noqa: ANN001
                return {
                    "nodes_created": len(nodes),
                    "relationships_created": len(relationships),
                    "errors": [],
                }

        class FakeVectorStore:
            def __init__(self):
                self.rows = []

            def add_batch(self, items):
                self.rows.extend(items)
                return len(items)

        vector_store = FakeVectorStore()
        pipeline = IndexingPipeline(
            ontology=ontology,
            graph_store=FakeGraphStore(),
            llm=FakeLLM(),
            vector_store=vector_store,
        )

        result = pipeline.index("ACME expanded into Asia.", metadata={"source_type": "text"})

        assert len(vector_store.rows) == 1
        row = vector_store.rows[0]
        assert row["id"].endswith("_chunk_0000")
        assert row["metadata"]["memory_id"] == result.source_id
        assert row["metadata"]["version_id"] == result.layered_graph_summary["version_id"]
        assert row["metadata"]["entity_ids"] == ["acme"]
        assert result.layered_graph_summary["vector_indexed_chunks"] == 1

    def test_markdown_headings_materialize_section_layer(self):
        ontology = Ontology(
            name="finder",
            nodes={"Company": NodeDef(properties={"name": P(str, unique=True)})},
            relationships={},
        )

        class FakeResponse:
            def __init__(self, payload):
                self._payload = payload
                self.usage = None

            def json(self):
                return self._payload

        class FakeLLM:
            def complete(self, *, system, user, temperature, response_format=None):  # noqa: ANN001
                return FakeResponse(
                    {
                        "nodes": [{"id": "acme", "label": "Company", "properties": {"name": "ACME"}}],
                        "relationships": [],
                    }
                )

        class FakeGraphStore:
            def __init__(self):
                self.last_nodes = []
                self.last_relationships = []

            def write(self, nodes, relationships, *, database="neo4j", workspace_id="default", source_id=""):  # noqa: ANN001
                self.last_nodes = list(nodes)
                self.last_relationships = list(relationships)
                return {
                    "nodes_created": len(nodes),
                    "relationships_created": len(relationships),
                    "errors": [],
                }

        store = FakeGraphStore()
        pipeline = IndexingPipeline(
            ontology=ontology,
            graph_store=store,
            llm=FakeLLM(),
            max_chunk_chars=45,
        )

        text = (
            "# Overview\n\n"
            "ACME launched a new product in Asia.\n\n"
            "## Risks\n\n"
            "ACME faces supply chain risk in the region."
        )
        result = pipeline.index(text, metadata={"source_type": "text"})

        section_nodes = [node for node in store.last_nodes if node["label"] == "Section"]
        chunk_nodes = [node for node in store.last_nodes if node["label"] == "Chunk"]
        assert result.layered_graph_summary["section_count"] == 2
        assert len(section_nodes) == 2
        assert {node["properties"]["section_path"] for node in chunk_nodes} == {"Overview", "Overview / Risks"}
        assert any(rel["type"] == "HAS_SECTION" for rel in store.last_relationships)
        assert any(rel["type"] == "PART_OF" for rel in store.last_relationships)


class TestStructuredGraphIngest:
    def test_seocho_add_graph_materializes_sections_and_chunks(self):
        ontology = Ontology(
            name="contracts",
            nodes={"Company": NodeDef(properties={"name": P(str, unique=True)})},
            relationships={},
        )

        class FakeGraphStore:
            def __init__(self):
                self.write_calls = 0
                self.last_nodes = []
                self.last_relationships = []

            def write(self, nodes, relationships, *, database="neo4j", workspace_id="default", source_id=""):  # noqa: ANN001
                self.write_calls += 1
                self.last_nodes = list(nodes)
                self.last_relationships = list(relationships)
                return {
                    "nodes_created": len(nodes),
                    "relationships_created": len(relationships),
                    "errors": [],
                }

        store = FakeGraphStore()
        client = Seocho(ontology=ontology, graph_store=store, llm=object())

        memory = client.add_graph(
            {
                "nodes": [{"id": "acme", "label": "Company", "properties": {"name": "ACME"}}],
                "relationships": [],
            },
            content=(
                "# Overview\n\n"
                "ACME entered Asia.\n\n"
                "## Risks\n\n"
                "ACME faces supply chain pressure."
            ),
        )

        assert memory.status == "active"
        assert memory.source_type == "structured_graph"
        assert memory.metadata["layered_graph_summary"]["section_count"] == 2
        assert any(node["label"] == "Section" for node in memory.entities)
        assert any(node["label"] == "Chunk" for node in store.last_nodes)
        assert store.write_calls == 1

    def test_seocho_add_graph_strict_validation_rejects_invalid_payload(self):
        ontology = Ontology(
            name="contracts",
            nodes={"Company": NodeDef(properties={"name": P(str, unique=True)})},
            relationships={},
        )

        class FakeGraphStore:
            def __init__(self):
                self.write_calls = 0

            def write(self, nodes, relationships, *, database="neo4j", workspace_id="default", source_id=""):  # noqa: ANN001
                self.write_calls += 1
                return {
                    "nodes_created": len(nodes),
                    "relationships_created": len(relationships),
                    "errors": [],
                }

        store = FakeGraphStore()
        client = Seocho(ontology=ontology, graph_store=store, llm=object())

        memory = client.add_graph(
            {
                "nodes": [{"id": "broken-company", "label": "Company", "properties": {}}],
                "relationships": [],
            },
            strict_validation=True,
        )

        assert memory.status == "failed"
        assert memory.metadata["validation_errors"]
        assert store.write_calls == 0
