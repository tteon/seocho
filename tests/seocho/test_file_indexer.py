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

    def test_csv_short_row_does_not_crash(self, tmp_dir):
        # Regression #140: a data row shorter than the header used to leave
        # content=None (DictReader restval), which crashed index_file's
        # content.strip(). Missing fields now coerce to "".
        f = tmp_dir / "ragged.csv"
        f.write_text("id,content,category\n1,ACME news,news\n2\n")
        records = read_csv_file(f)
        assert len(records) == 2
        assert records[1]["content"] == ""
        # every content is a real string, so downstream .strip() is safe
        for r in records:
            assert isinstance(r["content"], str)
            r["content"].strip()  # would raise AttributeError on None
        assert records[1]["metadata"]["category"] == ""

    def test_csv_long_row_drops_overflow_restkey(self, tmp_dir):
        # A row longer than the header collects overflow under DictReader's
        # None restkey; metadata must stay string-keyed and serializable.
        f = tmp_dir / "long.csv"
        f.write_text("id,content\n1,hello,extra1,extra2\n")
        records = read_csv_file(f)
        assert len(records) == 1
        assert records[0]["content"] == "hello"
        assert None not in records[0]["metadata"]
        json.dumps(records[0]["metadata"])  # must be serializable


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

    def test_json_array_of_non_objects_skipped_with_warning(self, tmp_dir, caplog):
        # read_json_file expects objects; non-object items (strings, numbers,
        # null) are not documents here, but must not vanish silently.
        f = tmp_dir / "strings.json"
        f.write_text(json.dumps(["First doc", "Second doc", 42, None]))
        with caplog.at_level("WARNING"):
            records = read_json_file(f)
        assert records == []
        assert sum("Skipping non-object item" in r.message for r in caplog.records) == 4

    def test_json_array_mixed_keeps_objects(self, tmp_dir):
        f = tmp_dir / "mixed.json"
        f.write_text(json.dumps([{"content": "obj doc"}, "string doc", 42]))
        records = read_json_file(f)
        assert len(records) == 1
        assert records[0]["content"] == "obj doc"
        assert records[0]["metadata"]["item_index"] == 0


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

    def test_jsonl_non_object_lines_skipped_with_warning(self, tmp_dir, caplog):
        f = tmp_dir / "strings.jsonl"
        f.write_text('{"content": "obj"}\n"bare string"\n42\n')
        with caplog.at_level("WARNING"):
            records = read_jsonl_file(f)
        assert len(records) == 1
        assert records[0]["content"] == "obj"
        assert sum("Skipping non-object JSON" in r.message for r in caplog.records) == 2


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


def test_failed_index_is_retried_instead_of_cached_as_unchanged(tmp_path):
    from seocho.index.file_reader import FileIndexer
    from seocho.index.pipeline import IndexingResult

    class Pipeline:
        default_database = "neo4j"
        def __init__(self):
            self.calls = 0
        def index(self, content, **kwargs):
            self.calls += 1
            return IndexingResult(write_errors=["database write failed"])

    source = tmp_path / "doc.txt"
    source.write_text("A document requiring retry")
    pipeline = Pipeline()
    indexer = FileIndexer(pipeline)
    assert indexer.index_directory(tmp_path).files_failed == 1
    assert indexer.index_directory(tmp_path).files_failed == 1
    assert pipeline.calls == 2


def test_legacy_tracking_state_remains_readable(tmp_path: Path) -> None:
    path = tmp_path / 'legacy.txt'
    path.write_text('legacy content')
    stat = path.stat()
    (tmp_path / '.seocho_index').write_text(json.dumps({
        'version': 1, 'files': [{
            'path': str(path), 'mtime': stat.st_mtime, 'size': stat.st_size,
            'source_id': 'legacy-source', 'content_hash': 'legacy-hash',
        }],
    }))
    tracker = FileTracker(tmp_path)
    assert not tracker.needs_indexing(path)
    assert tracker.get_source_id(path) == 'legacy-source'
    tracker.save()
    saved = json.loads((tmp_path / '.seocho_index').read_text())
    assert saved['version'] == 2 and saved['change_detection'] == 'mtime_size'
    assert saved['files'][0]['content_hash'] == 'legacy-hash'


def test_tracking_does_not_reread_indexed_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from seocho.index.file_reader import FileIndexer
    from seocho.index.pipeline import IndexingResult

    class Pipeline:
        def index(self, content: str, **kwargs: object) -> IndexingResult:
            assert content == 'One document.'
            return IndexingResult(source_id='source', total_nodes=1, chunks_processed=1)

    path = tmp_path / 'document.txt'
    path.write_text('One document.')
    reads = []
    original = Path.read_text

    def read_text(self: Path, *args: object, **kwargs: object) -> str:
        reads.append(self)
        return original(self, *args, **kwargs)

    tracker = FileTracker(tmp_path)
    monkeypatch.setattr(Path, 'read_text', read_text)
    result = FileIndexer(Pipeline()).index_file(path, tracker=tracker)
    assert result.status == 'indexed'
    assert reads == [path]  # The document reader runs once; tracking only stats it.
    assert tracker.get_source_id(path) == 'source'
    assert not tracker.needs_indexing(path)
