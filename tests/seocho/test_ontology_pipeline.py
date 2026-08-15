"""WP-O pipeline contract: canonical form, derivation, lockfile, blast radius.

Requires rdflib (the seocho[ontology] extra); skips cleanly without it, same
as the owlready2-backed tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("rdflib")

from seocho import ontology_pipeline as pipeline  # noqa: E402

SHAPES = """\
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix fin: <https://seocho.dev/shapes/fin#> .

fin:AccountShape a sh:NodeShape ;
    sh:targetClass fin:Account ;
    sh:property [ sh:path fin:acct_no ; sh:datatype xsd:integer ] ;
    sh:property [ sh:path fin:transfersTo ; sh:class fin:Account ] .

fin:CompanyShape a sh:NodeShape ;
    sh:targetClass fin:Company ;
    sh:property [ sh:path fin:name ; sh:datatype xsd:string ] ;
    sh:property [ sh:path fin:owns ; sh:class fin:Account ] .
"""


@pytest.fixture
def shapes_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "shapes"
    directory.mkdir()
    (directory / "core.ttl").write_text(SHAPES, encoding="utf-8")
    return directory


def test_canonical_turtle_is_idempotent_and_order_insensitive(shapes_dir: Path):
    graph = pipeline.load_graph(shapes_dir / "core.ttl")
    once = pipeline.canonical_turtle(graph)
    # Re-parse the formatted output: formatting again must be a fixed point.
    reparsed_path = shapes_dir / "reparsed.ttl"
    reparsed_path.write_text(once, encoding="utf-8")
    twice = pipeline.canonical_turtle(pipeline.load_graph(reparsed_path))
    assert once == twice

    # A semantically identical file with reordered statements hashes the same.
    lines = SHAPES.split("\n\n")
    reordered = "\n\n".join([lines[0]] + list(reversed(lines[1:])))
    other = shapes_dir / "reordered.ttl"
    other.write_text(reordered, encoding="utf-8")
    assert pipeline.source_hash(graph) == pipeline.source_hash(pipeline.load_graph(other))


def test_derivation_yields_vocab_paths_and_addresses(shapes_dir: Path):
    derived = pipeline.derive_from_lockfree(shapes_dir)
    assert derived.classes == ["fin:Account", "fin:Company"]
    assert derived.path_index["fin:Account"]["fin:transfersTo"] == "fin:Account"
    assert derived.path_index["fin:Company"]["fin:owns"] == "fin:Account"
    assert "fin:acct_no" in derived.properties["fin:Account"]
    assert derived.address_space == ["class:fin:Account", "class:fin:Company"]


def test_build_verify_roundtrip_and_active_hash_covers_tool(shapes_dir: Path, tmp_path: Path):
    result = pipeline.build(shapes_dir)
    lock_path = tmp_path / "seocho.lock"
    pipeline.write_build(result, tmp_path / "build", lock_path)
    ok, problems = pipeline.verify(shapes_dir, lock_path)
    assert ok, problems

    # active_hash must cover the lockfile as a whole, not just shapes (§O.4):
    # a tool-version change with identical shapes must be drift.
    tampered = json.loads(lock_path.read_text())
    tampered["tool"]["pipeline"] = "seocho-ont/9.9.9"
    lock_path.write_text(json.dumps(tampered, indent=2, sort_keys=True))
    ok, problems = pipeline.verify(shapes_dir, lock_path)
    assert not ok
    assert any("tool" in p or "active_hash" in p for p in problems)


def test_shape_change_moves_active_hash_and_blast_radius_names_it(
        shapes_dir: Path, tmp_path: Path):
    proposed = tmp_path / "proposed"
    proposed.mkdir()
    changed = SHAPES.replace(
        "sh:property [ sh:path fin:owns ; sh:class fin:Account ] .",
        "sh:property [ sh:path fin:owns ; sh:class fin:Account ] ;\n"
        "    sh:property [ sh:path fin:country ; sh:datatype xsd:string ] .")
    (proposed / "core.ttl").write_text(changed, encoding="utf-8")

    report = pipeline.blast_radius(shapes_dir, proposed)
    assert report["active_hash_before"] != report["active_hash_after"]
    assert report["addresses_changed"] == ["class:fin:Company"]
    assert report["addresses_added"] == []
    assert 0 < report["address_space_share_touched"] <= 0.5


def test_lint_flags_shapeless_and_pathless(tmp_path: Path):
    directory = tmp_path / "shapes"
    directory.mkdir()
    (directory / "bad.ttl").write_text(
        "@prefix sh: <http://www.w3.org/ns/shacl#> .\n"
        "@prefix ex: <https://x.dev/#> .\n"
        "ex:Orphan a sh:NodeShape ;\n"
        "    sh:property [ sh:name \"no path here\" ] .\n",
        encoding="utf-8")
    findings = pipeline.lint(directory)
    assert any("no sh:targetClass" in f for f in findings)
    assert any(f.startswith("ERROR") and "lacks sh:path" in f for f in findings)


def test_ont_cli_group_end_to_end(shapes_dir: Path, tmp_path: Path, capsys):
    from seocho.cli import main

    out = tmp_path / "build"
    lock = tmp_path / "seocho.lock"
    assert main(["ont", "build", str(shapes_dir), "--out", str(out),
                 "--lock", str(lock)]) == 0
    assert (out / "address_space.json").exists()
    assert main(["ont", "verify", str(shapes_dir), "--lock", str(lock)]) == 0

    # fmt --check flags the hand-written fixture, fmt fixes it, check passes.
    assert main(["ont", "fmt", str(shapes_dir), "--check"]) == 1
    assert main(["ont", "fmt", str(shapes_dir)]) == 0
    assert main(["ont", "fmt", str(shapes_dir), "--check"]) == 0
    # formatting alone must not drift the lock: hashes are canonicalization-based
    assert main(["ont", "verify", str(shapes_dir), "--lock", str(lock)]) == 0
    capsys.readouterr()
