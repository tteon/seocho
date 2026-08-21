"""Durable writes for the semantic artifact store (#139).

create/approve/deprecate wrote with open("w") + json.dump, so an interrupted
write left a truncated file that broke every later read, and list_semantic_
artifacts json.load'd every file with no guard, so one corrupt file 500'd the
whole list endpoint. Writes are now atomic (temp + os.replace) and listing
skips unreadable files.
"""

from __future__ import annotations

import json

from extraction.semantic_artifact_store import (
    _atomic_write_json,
    list_semantic_artifacts,
    save_semantic_artifact,
)


def _save(base_dir, name):
    return save_semantic_artifact(
        workspace_id="ws",
        ontology_candidate={},
        shacl_candidate={},
        name=name,
        base_dir=str(base_dir),
    )


def test_corrupt_artifact_does_not_break_listing(tmp_path):
    _save(tmp_path, "good-1")
    _save(tmp_path, "good-2")
    # Simulate a truncated/partially-written artifact.
    (tmp_path / "ws" / "sa_broken.json").write_text('{"artifact_id": "sa_broken", ', encoding="utf-8")

    rows = list_semantic_artifacts("ws", base_dir=str(tmp_path))

    names = {r["name"] for r in rows}
    assert names == {"good-1", "good-2"}  # the corrupt file is skipped, not fatal


def test_save_leaves_no_temp_files(tmp_path):
    _save(tmp_path, "good")
    files = list((tmp_path / "ws").iterdir())
    assert all(f.suffix == ".json" and not f.name.endswith(".tmp") for f in files)
    assert len(files) == 1


def test_atomic_write_roundtrip_and_overwrite(tmp_path):
    path = tmp_path / "a.json"
    _atomic_write_json(path, {"v": 1})
    assert json.loads(path.read_text()) == {"v": 1}
    _atomic_write_json(path, {"v": 2})  # overwrite in place
    assert json.loads(path.read_text()) == {"v": 2}
    # no leftover temp files in the directory
    assert [p.name for p in tmp_path.iterdir()] == ["a.json"]
