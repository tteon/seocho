"""Tests for runtime.ontology_registry — Phase 1.5 loader + accessors.

Phase 1.5 activates the seam Phases 1, 2, 3 plumbed: a runtime ontology
registry populated from a JSON manifest, exposing:
  - active_context_hashes(workspace_id) -> {database: hash} for Phase 1
  - ontology_contexts(workspace_id) -> {graph_id: CompiledOntologyContext} for Phase 2

Phase 3 wraps the resulting agent.ontology_context_skew with the state
machine; that composition is exercised in test_agent_readiness.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from runtime.ontology_registry import (
    RuntimeOntologyRegistry,
    get_runtime_ontology_registry,
    load_runtime_ontologies_from_env,
    load_runtime_ontologies_from_manifest,
    reset_runtime_ontology_registry,
)
from seocho.ontology import NodeDef, Ontology, P, RelDef


@pytest.fixture(autouse=True)
def _isolated_registry():
    reset_runtime_ontology_registry()
    yield
    reset_runtime_ontology_registry()


def _ontology(name: str = "finance", *, version: str = "1.0.0") -> Ontology:
    return Ontology(
        name=name,
        package_id=f"company-{name}",
        version=version,
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
            "Person": NodeDef(properties={"name": P(str, unique=True)}),
        },
        relationships={
            "WORKS_AT": RelDef(source="Person", target="Company"),
        },
    )


# ---------------------------------------------------------------------------
# Registry behavior
# ---------------------------------------------------------------------------


def test_register_returns_compiled_context_with_stable_hash():
    registry = RuntimeOntologyRegistry()
    ctx = registry.register("finance", "kgnormal", _ontology(), workspace_id="acme")
    assert ctx.descriptor.context_hash
    assert registry.get_ontology("finance", workspace_id="acme") is not None
    assert registry.get_context("finance", workspace_id="acme") is ctx


def test_register_validates_inputs():
    registry = RuntimeOntologyRegistry()
    with pytest.raises(TypeError):
        registry.register("finance", "kgnormal", "not an ontology")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        registry.register("", "kgnormal", _ontology())
    with pytest.raises(ValueError):
        registry.register("finance", "", _ontology())


def test_active_context_hashes_keyed_by_database():
    """Phase 1 plumbing consumes {database: hash} so memory_service.ontology_context_mismatch
    can pass each entry as expected_context_hash to assess_graph_ontology_context_status."""

    registry = RuntimeOntologyRegistry()
    registry.register("finance", "kgnormal", _ontology("finance"))
    registry.register("legal", "kglegal", _ontology("legal"))

    hashes = registry.active_context_hashes()
    assert set(hashes) == {"kgnormal", "kglegal"}
    assert hashes["kgnormal"] != hashes["kglegal"]


def test_ontology_contexts_keyed_by_graph_id():
    """Phase 2 plumbing consumes {graph_id: CompiledOntologyContext} so
    AgentFactory.create_agents_for_graphs can probe each graph against its context."""

    registry = RuntimeOntologyRegistry()
    registry.register("finance", "kgnormal", _ontology("finance"))
    registry.register("legal", "kglegal", _ontology("legal"))

    contexts = registry.ontology_contexts()
    assert set(contexts) == {"finance", "legal"}
    assert contexts["finance"].descriptor.context_hash != contexts["legal"].descriptor.context_hash


def test_workspaces_are_isolated():
    registry = RuntimeOntologyRegistry()
    registry.register("finance", "kgnormal", _ontology(), workspace_id="acme")
    registry.register("finance", "kgnormal", _ontology(), workspace_id="other")

    acme_hashes = registry.active_context_hashes(workspace_id="acme")
    other_hashes = registry.active_context_hashes(workspace_id="other")
    # Even with the same ontology, workspace_id is part of the identity payload
    # so the descriptor hashes diverge.
    assert acme_hashes["kgnormal"] != other_hashes["kgnormal"]
    assert registry.active_context_hashes(workspace_id="missing") == {}


def test_re_register_replaces_existing_entry():
    registry = RuntimeOntologyRegistry()
    first = registry.register("finance", "kgnormal", _ontology(version="1.0.0"))
    second = registry.register("finance", "kgnormal", _ontology(version="2.0.0"))
    assert first.descriptor.context_hash != second.descriptor.context_hash
    assert registry.active_context_hashes()["kgnormal"] == second.descriptor.context_hash
    assert len(registry) == 1


def test_singleton_is_isolated_across_resets():
    a = get_runtime_ontology_registry()
    a.register("finance", "kgnormal", _ontology())
    assert len(a) == 1

    reset_runtime_ontology_registry()
    b = get_runtime_ontology_registry()
    assert b is not a
    assert len(b) == 0


# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------


def _write_manifest(
    tmp_path: Path,
    entries: list[dict],
    *,
    ontology_files: dict[str, Ontology] | None = None,
) -> Path:
    """Write a manifest plus optional sibling ontology files; return manifest path."""

    if ontology_files:
        for filename, ontology in ontology_files.items():
            ontology_path = tmp_path / filename
            ontology.to_jsonld(ontology_path)
    manifest = tmp_path / "ontologies.json"
    manifest.write_text(json.dumps(entries), encoding="utf-8")
    return manifest


def test_load_manifest_populates_registry(tmp_path):
    registry = RuntimeOntologyRegistry()
    manifest = _write_manifest(
        tmp_path,
        [
            {
                "graph_id": "finance",
                "database": "kgnormal",
                "ontology_path": "finance.jsonld",
            }
        ],
        ontology_files={"finance.jsonld": _ontology("finance")},
    )

    loaded = load_runtime_ontologies_from_manifest(manifest, registry=registry)
    assert loaded == 1
    assert "kgnormal" in registry.active_context_hashes()


def test_load_manifest_resolves_relative_paths_against_manifest_dir(tmp_path):
    nested = tmp_path / "config"
    nested.mkdir()
    ontologies_dir = tmp_path / "ontologies"
    ontologies_dir.mkdir()
    _ontology("legal").to_jsonld(ontologies_dir / "legal.jsonld")

    manifest = nested / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "graph_id": "legal",
                    "database": "kglegal",
                    "ontology_path": "../ontologies/legal.jsonld",
                }
            ]
        ),
        encoding="utf-8",
    )

    registry = RuntimeOntologyRegistry()
    loaded = load_runtime_ontologies_from_manifest(manifest, registry=registry)
    assert loaded == 1


def test_load_manifest_skips_malformed_entries(tmp_path, caplog):
    registry = RuntimeOntologyRegistry()
    _ontology("finance").to_jsonld(tmp_path / "finance.jsonld")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {"graph_id": "finance", "database": "kgnormal", "ontology_path": "finance.jsonld"},
                "not_a_dict",
                {"graph_id": "", "database": "kgother", "ontology_path": "x.jsonld"},
                {"graph_id": "missing", "database": "kgmissing", "ontology_path": "no_such.jsonld"},
            ]
        ),
        encoding="utf-8",
    )

    loaded = load_runtime_ontologies_from_manifest(manifest, registry=registry)
    assert loaded == 1
    assert "kgnormal" in registry.active_context_hashes()


def test_load_manifest_returns_zero_when_path_missing(tmp_path):
    registry = RuntimeOntologyRegistry()
    loaded = load_runtime_ontologies_from_manifest(tmp_path / "missing.json", registry=registry)
    assert loaded == 0
    assert len(registry) == 0


def test_load_manifest_returns_zero_when_payload_not_array(tmp_path):
    registry = RuntimeOntologyRegistry()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"not": "a list"}), encoding="utf-8")

    loaded = load_runtime_ontologies_from_manifest(manifest, registry=registry)
    assert loaded == 0
    assert len(registry) == 0


def test_env_loader_reads_path_from_env(tmp_path, monkeypatch):
    _ontology("finance").to_jsonld(tmp_path / "finance.jsonld")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "graph_id": "finance",
                    "database": "kgnormal",
                    "ontology_path": "finance.jsonld",
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SEOCHO_RUNTIME_ONTOLOGIES", str(manifest))

    registry = RuntimeOntologyRegistry()
    loaded = load_runtime_ontologies_from_env(registry=registry)
    assert loaded == 1


def test_env_loader_returns_zero_when_unset(monkeypatch):
    monkeypatch.delenv("SEOCHO_RUNTIME_ONTOLOGIES", raising=False)
    registry = RuntimeOntologyRegistry()
    assert load_runtime_ontologies_from_env(registry=registry) == 0
    assert len(registry) == 0


# ---------------------------------------------------------------------------
# End-to-end: registry hashes flow into the Phase 1 / Phase 2 consumers
# ---------------------------------------------------------------------------


def test_registry_drives_assess_graph_ontology_context_status_drift():
    """Structural promise: when the registry holds an ontology and the graph
    is stamped with a different hash, the existing assess_graph_ontology_context_status
    helper detects the drift via expected_context_hash."""

    from seocho.ontology_context import assess_graph_ontology_context_status

    registry = RuntimeOntologyRegistry()
    registry.register("finance", "kgnormal", _ontology())
    active_hashes = registry.active_context_hashes()
    assert "kgnormal" in active_hashes

    drifted = assess_graph_ontology_context_status(
        database="kgnormal",
        workspace_id="default",
        indexed_context_hashes=["stale-hash-from-graph"],
        expected_context_hash=active_hashes["kgnormal"],
        scoped_nodes=10,
    )
    assert drifted["mismatch"] is True
    assert "indexed_context_hash_differs_from_active" in drifted["mismatch_reasons"]

    matched = assess_graph_ontology_context_status(
        database="kgnormal",
        workspace_id="default",
        indexed_context_hashes=[active_hashes["kgnormal"]],
        expected_context_hash=active_hashes["kgnormal"],
        scoped_nodes=10,
    )
    assert matched["mismatch"] is False
