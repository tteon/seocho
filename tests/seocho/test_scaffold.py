from __future__ import annotations

import pytest

from seocho.ontology import Ontology
from seocho.run_spec import load_run_spec
from seocho.scaffold import create_sample_project


def test_create_sample_project_is_runnable_config(tmp_path) -> None:
    target = tmp_path / "hello"

    result = create_sample_project(target)

    assert result.path == target
    assert (target / "schema.yaml").exists()
    assert (target / "docs" / "acme.md").exists()
    ontology = Ontology.load(target / "schema.yaml")
    assert ontology.name == "hello_company"
    assert "Company" in ontology.nodes
    spec = load_run_spec(target / "seocho.run.yaml")
    assert spec.ontology_path == "./schema.yaml"
    assert spec.documents_path == "./docs/"
    assert spec.query_model() == "mara/MiniMax-M2.5"
    assert spec.query.get("answer_style") == "evidence"
    assert len(spec.questions) == 3


def test_create_sample_project_refuses_non_empty_directory(tmp_path) -> None:
    target = tmp_path / "hello"
    target.mkdir()
    (target / "note.txt").write_text("mine", encoding="utf-8")

    with pytest.raises(FileExistsError, match="not empty"):
        create_sample_project(target)


def test_create_sample_project_force_overwrites_scaffold_files_only(tmp_path) -> None:
    target = tmp_path / "hello"
    create_sample_project(target)
    (target / "schema.yaml").write_text("broken: true\n", encoding="utf-8")
    (target / "private.txt").write_text("keep", encoding="utf-8")

    create_sample_project(target, force=True)

    assert "hello_company" in (target / "schema.yaml").read_text(encoding="utf-8")
    assert (target / "private.txt").read_text(encoding="utf-8") == "keep"
