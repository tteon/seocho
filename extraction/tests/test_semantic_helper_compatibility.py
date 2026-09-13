"""Shared semantic owners preserve legacy imports and evidence selection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from extraction.ontology_hints import OntologyHintStore as LegacyHints
from extraction.semantic_profile_packages import (
    select_semantic_profile_package as legacy_select,
)
from extraction.semantic_vocabulary import ManagedVocabularyResolver as LegacyVocabulary
from seocho.query.ontology_hints import OntologyHintStore
from seocho.query.profile_packages import select_semantic_profile_package
from seocho.query.vocabulary import ManagedVocabularyResolver


def test_legacy_hint_and_profile_contracts(tmp_path: Path) -> None:
    hints = tmp_path / "hints.json"
    hints.write_text(
        json.dumps(
            {"aliases": {"ACME Co.": "Acme"}, "label_keywords": {"Company": ["firm"]}}
        )
    )
    assert LegacyHints is OntologyHintStore
    store = LegacyHints(str(hints))
    assert store.resolve_alias("ACME CO") == "Acme"
    assert store.infer_label_hints("Which firm?") == {"company"}
    assert legacy_select is select_semantic_profile_package
    package = legacy_select(
        intent_id="responsibility_lookup", constraint_slice={"database": "kgfibo"}
    )
    assert package is not None and package.relation_priority[0] == "OWNS"


@pytest.mark.parametrize("resolver_type", [ManagedVocabularyResolver, LegacyVocabulary])
def test_approved_workspace_aliases_override_global_only_after_cache_clear(
    tmp_path: Path,
    resolver_type: type[ManagedVocabularyResolver],
) -> None:
    def write(
        workspace: str, artifact_id: str, name: str, status: str = "approved"
    ) -> Path:
        directory = tmp_path / workspace
        directory.mkdir(exist_ok=True)
        path = directory / f"{artifact_id}.json"
        path.write_text(
            json.dumps(
                {
                    "artifact_id": artifact_id,
                    "workspace_id": workspace,
                    "status": status,
                    "created_at": "2026-09-13",
                    "approved_at": "2026-09-13",
                    "vocabulary_candidate": {
                        "terms": [{"canonical": name, "aliases": ["sample"]}]
                    },
                }
            )
        )
        return path

    write("global", "global", "Global")
    path = write("tenant", "local", "Local")
    write("tenant", "draft", "Unapproved", "draft")
    resolver = resolver_type(base_dir=str(tmp_path), global_workspace_id="global")
    assert resolver.resolve_alias("sample", "tenant") == "Local"
    assert resolver.resolve_alias("sample", "another") == "Global"
    path.unlink()
    assert resolver.resolve_alias("sample", "tenant") == "Local"
    resolver.clear_cache()
    assert resolver.resolve_alias("sample", "tenant") == "Global"
