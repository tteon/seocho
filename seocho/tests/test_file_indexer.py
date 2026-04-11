"""Tests for seocho.file_indexer — file reading and tracking."""

import json
import tempfile
from pathlib import Path

import pytest

from seocho.file_indexer import (
    FileTracker,
    read_csv_file,
    read_json_file,
    read_jsonl_file,
    read_text_file,
    SUPPORTED_EXTENSIONS,
)


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


class TestTextReader:
    def test_read_txt(self, tmp_dir):
        f = tmp_dir / "test.txt"
        f.write_text("Hello world.\nSecond line.")
        records = read_text_file(f)
        assert len(records) == 1
        assert "Hello world" in records[0]["content"]
        assert records[0]["metadata"]["format"] == ".txt"

    def test_read_md(self, tmp_dir):
        f = tmp_dir / "test.md"
        f.write_text("# Title\n\nParagraph here.")
        records = read_text_file(f)
        assert len(records) == 1
        assert "# Title" in records[0]["content"]


class TestCSVReader:
    def test_csv_with_content_column(self, tmp_dir):
        f = tmp_dir / "data.csv"
        f.write_text("id,content,category\n1,ACME acquired Beta,news\n2,Beta provides analytics,news\n")
        records = read_csv_file(f)
        assert len(records) == 2
        assert records[0]["content"] == "ACME acquired Beta"
        assert records[0]["metadata"]["category"] == "news"
        assert records[0]["metadata"]["row_index"] == 0

    def test_csv_without_content_column(self, tmp_dir):
        f = tmp_dir / "data.csv"
        f.write_text("name,role\nAlice,CEO\nBob,CTO\n")
        records = read_csv_file(f)
        assert len(records) == 2
        assert "Alice" in records[0]["content"]
        assert "CEO" in records[0]["content"]

    def test_empty_csv(self, tmp_dir):
        f = tmp_dir / "empty.csv"
        f.write_text("col1,col2\n")
        records = read_csv_file(f)
        assert len(records) == 0


class TestJSONReader:
    def test_json_array(self, tmp_dir):
        f = tmp_dir / "data.json"
        f.write_text(json.dumps([
            {"content": "First doc", "tag": "a"},
            {"content": "Second doc", "tag": "b"},
        ]))
        records = read_json_file(f)
        assert len(records) == 2
        assert records[0]["content"] == "First doc"
        assert records[0]["metadata"]["tag"] == "a"

    def test_json_single_object(self, tmp_dir):
        f = tmp_dir / "single.json"
        f.write_text(json.dumps({"content": "Only doc"}))
        records = read_json_file(f)
        assert len(records) == 1
        assert records[0]["content"] == "Only doc"


class TestJSONLReader:
    def test_jsonl_lines(self, tmp_dir):
        f = tmp_dir / "data.jsonl"
        f.write_text(
            '{"content": "line one"}\n'
            '{"content": "line two"}\n'
            '\n'
            '{"content": "line three"}\n'
        )
        records = read_jsonl_file(f)
        assert len(records) == 3
        assert records[2]["content"] == "line three"

    def test_jsonl_malformed_line(self, tmp_dir):
        f = tmp_dir / "bad.jsonl"
        f.write_text('{"content": "good"}\nnot json\n{"content": "also good"}\n')
        records = read_jsonl_file(f)
        assert len(records) == 2  # bad line skipped


class TestFileTracker:
    def test_new_file_needs_indexing(self, tmp_dir):
        tracker = FileTracker(tmp_dir)
        f = tmp_dir / "test.txt"
        f.write_text("content")
        assert tracker.needs_indexing(f) is True

    def test_indexed_file_unchanged(self, tmp_dir):
        tracker = FileTracker(tmp_dir)
        f = tmp_dir / "test.txt"
        f.write_text("content")
        tracker.mark_indexed(f, "src-123", "hash-abc")
        tracker.save()

        tracker2 = FileTracker(tmp_dir)
        assert tracker2.needs_indexing(f) is False

    def test_modified_file_needs_reindex(self, tmp_dir):
        tracker = FileTracker(tmp_dir)
        f = tmp_dir / "test.txt"
        f.write_text("content v1")
        tracker.mark_indexed(f, "src-1", "hash-1")
        tracker.save()

        # Modify file
        import time
        time.sleep(0.05)
        f.write_text("content v2")

        tracker2 = FileTracker(tmp_dir)
        assert tracker2.needs_indexing(f) is True

    def test_get_source_id(self, tmp_dir):
        tracker = FileTracker(tmp_dir)
        f = tmp_dir / "test.txt"
        f.write_text("content")
        tracker.mark_indexed(f, "src-456", "hash-xyz")
        assert tracker.get_source_id(f) == "src-456"

    def test_remove(self, tmp_dir):
        tracker = FileTracker(tmp_dir)
        f = tmp_dir / "test.txt"
        f.write_text("content")
        tracker.mark_indexed(f, "src-789", "hash-def")
        removed_id = tracker.remove(f)
        assert removed_id == "src-789"
        assert tracker.get_source_id(f) is None

    def test_persistence(self, tmp_dir):
        tracker = FileTracker(tmp_dir)
        f = tmp_dir / "a.txt"
        f.write_text("aaa")
        tracker.mark_indexed(f, "s1", "h1")
        tracker.save()

        # New tracker loads from disk
        tracker2 = FileTracker(tmp_dir)
        assert tracker2.get_source_id(f) == "s1"


class TestSupportedExtensions:
    def test_all_expected(self):
        assert ".txt" in SUPPORTED_EXTENSIONS
        assert ".md" in SUPPORTED_EXTENSIONS
        assert ".csv" in SUPPORTED_EXTENSIONS
        assert ".json" in SUPPORTED_EXTENSIONS
        assert ".jsonl" in SUPPORTED_EXTENSIONS
