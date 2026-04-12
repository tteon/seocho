"""Tests for seocho.indexing — chunking, dedup, pipeline."""

import pytest

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
            def write(self, nodes, relationships, *, database="neo4j", workspace_id="default", source_id=""):  # noqa: ANN001
                return {"nodes_created": len(nodes), "relationships_created": len(relationships), "errors": []}

        pipeline = IndexingPipeline(
            ontology=ontology,
            graph_store=FakeGraphStore(),
            llm=FakeLLM(),
        )

        result = pipeline.index("Cboe data")

        assert result.total_nodes == 1
        assert result.total_relationships == 1
