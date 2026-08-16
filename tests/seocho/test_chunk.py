"""Tests for seocho.index.chunk — Chunk dataclass + chunk() function.

Covers:
- Chunk dataclass attribute parity with the legacy chunk_text behavior.
- Deterministic chunk_id format (matches the existing on-disk convention).
- Char offset correctness for happy-path and overlapping chunks.
- Empty input and single-chunk-short-input edge cases.
- chunk_text() back-compat shim returns identical list[str] as new chunk().
"""

from __future__ import annotations

import pytest

from seocho.index.chunk import Chunk, build_chunk_id, chunk
from seocho.index.pipeline import chunk_text


class TestChunkDataclass:
    def test_chunk_id_format_is_deterministic(self):
        assert build_chunk_id("src-abc", 0) == "src-abc_chunk_0000"
        assert build_chunk_id("src-abc", 7) == "src-abc_chunk_0007"
        assert build_chunk_id("src-abc", 1234) == "src-abc_chunk_1234"

    def test_chunk_is_frozen(self):
        c = Chunk(
            chunk_id="src_chunk_0000",
            text="hello",
            ordinal=0,
            char_start=0,
            char_end=5,
        )
        with pytest.raises(AttributeError):
            c.text = "mutated"  # type: ignore[misc]


class TestChunkFunctionHappyPath:
    def test_short_text_returns_single_chunk(self):
        chunks = chunk("hello world", source_id="doc1", max_chars=100)
        assert len(chunks) == 1
        c = chunks[0]
        assert c.chunk_id == "doc1_chunk_0000"
        assert c.text == "hello world"
        assert c.ordinal == 0
        assert c.char_start == 0
        assert c.char_end == 11

    def test_empty_text_returns_single_empty_chunk(self):
        chunks = chunk("", source_id="doc1")
        assert len(chunks) == 1
        assert chunks[0].text == ""
        assert chunks[0].char_start == -1
        assert chunks[0].char_end == -1

    def test_long_text_splits_with_correct_ids(self):
        text = "\n\n".join(f"Paragraph {i} with some content." for i in range(20))
        chunks = chunk(text, source_id="doc1", max_chars=100, overlap_chars=20)
        assert len(chunks) > 1
        for i, c in enumerate(chunks):
            assert c.chunk_id == f"doc1_chunk_{i:04d}"
            assert c.ordinal == i

    def test_char_offsets_locate_chunk_text_in_source(self):
        text = "\n\n".join(f"Paragraph {i} with content." for i in range(10))
        chunks = chunk(text, source_id="doc1", max_chars=100, overlap_chars=0)
        for c in chunks:
            if c.char_start < 0:
                continue
            assert text[c.char_start : c.char_end] == c.text

    def test_overlap_first_chunk_locatable(self):
        text = "\n\n".join(f"Para {i} body" for i in range(8))
        chunks = chunk(text, source_id="doc1", max_chars=40, overlap_chars=10)
        first = chunks[0]
        assert text[first.char_start : first.char_end] == first.text

    def test_overlapped_long_chunks_keep_valid_offsets(self):
        # Regression for #124. Two effects combined to collapse overlapped
        # chunks to (-1, -1) under the old post-hoc `str.find` locator:
        #   1. bodies longer than ~512 chars carry a 200-char overlap prefix
        #      that drifts out of the 512-char lookback window, and
        #   2. paragraphs are stripped before re-joining, so a body whose
        #      source had surrounding whitespace no longer matches verbatim
        #      and `find` returns -1.
        # The whitespace padding below reproduces (2) on top of (1). Offsets
        # are now tracked from real paragraph positions, so they stay valid.
        paragraphs = [
            "   " + f"Paragraph {i}: " + ("lorem ipsum dolor sit amet " * 6).strip() + "   "
            for i in range(12)
        ]
        text = "\n\n".join(paragraphs)
        chunks = chunk(text, source_id="doc1", max_chars=800, overlap_chars=200)

        assert len(chunks) > 1
        assert any(len(c.text) > 512 for c in chunks)  # the failing-size regime

        prev_start = -1
        for c in chunks:
            # No chunk loses its provenance (the old code returned (-1, -1)).
            assert c.char_start >= 0
            assert c.char_end > c.char_start
            assert c.char_end <= len(text)
            # Spans advance monotonically through the document.
            assert c.char_start >= prev_start
            prev_start = c.char_start
            # The span ends exactly at this chunk's last paragraph, so the
            # source slice resolves the chunk's own (non-overlap) tail even
            # when stripping changed the body away from the raw source.
            last_paragraph = c.text.split("\n\n")[-1]
            assert text[c.char_start : c.char_end].endswith(last_paragraph)

    def test_markdown_headings_attach_section_paths(self):
        text = (
            "# Overview\n\n"
            "ACME launched a new product.\n\n"
            "## Risks\n\n"
            "Supply chain pressure remained elevated."
        )
        chunks = chunk(text, source_id="doc1", max_chars=35, overlap_chars=0)
        section_paths = {c.section_path for c in chunks}
        assert "Overview" in section_paths
        assert "Overview / Risks" in section_paths
        leaf_chunk = next(c for c in chunks if c.section_path == "Overview / Risks")
        assert leaf_chunk.section_title == "Risks"
        assert leaf_chunk.section_level == 2


class TestChunkTextBackCompatShim:
    def test_shim_matches_chunk_text_outputs(self):
        text = "\n\n".join(f"Block {i} text here." for i in range(15))
        strings = chunk_text(text, max_chars=80, overlap_chars=15)
        rich = chunk(text, source_id="_text", max_chars=80, overlap_chars=15)
        assert strings == [c.text for c in rich]

    def test_shim_preserves_empty_input(self):
        chunks = chunk_text("")
        assert len(chunks) == 1
        assert chunks[0] == ""

    def test_shim_preserves_custom_separator(self):
        text = "a. sentence one. sentence two. sentence three."
        chunks = chunk_text(text, max_chars=20, separator=". ")
        assert len(chunks) >= 2
