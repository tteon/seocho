"""Regression for #118 — an extraction failure (e.g. a reasoning-model
timeout) must not be silently absorbed into a heuristic Entity fallback that
reports success, and the reasoning-model presets need more than the 120s
default timeout that was tripping that fallback.
"""

from __future__ import annotations

import pytest

from seocho.index.pipeline import IndexingPipeline
from seocho.ontology import NodeDef, Ontology, P
from seocho.store.llm import create_llm_backend, get_provider_spec


def _ontology() -> Ontology:
    return Ontology(
        name="t",
        nodes={"Company": NodeDef(properties={"name": P(str, unique=True)})},
        relationships={},
    )


class _RaisingLLM:
    def complete(self, *, system, user, temperature, response_format=None, **kw):
        raise TimeoutError("extraction timed out")


class _FakeGraphStore:
    def write(self, nodes, relationships, *, database="neo4j", workspace_id="default", source_id=""):
        return {"nodes_created": len(nodes), "relationships_created": len(relationships), "errors": []}


def _pipeline(on_failure):
    return IndexingPipeline(
        ontology=_ontology(), graph_store=_FakeGraphStore(), llm=_RaisingLLM(),
        extraction_on_failure=on_failure,
    )


# --- failure policy -------------------------------------------------------

def test_extraction_failure_raises_by_default():
    with pytest.raises(RuntimeError, match="Extraction failed"):
        _pipeline("raise").index("ACME expanded into Asia.")


def test_extraction_failure_degrades_only_when_opted_in():
    result = _pipeline("degrade").index("ACME expanded into Asia.")
    assert result.fallback_used is True
    assert "TimeoutError" in result.fallback_reason
    # degraded extraction is not a clean success
    assert result.ok is False


def test_invalid_policy_rejected():
    with pytest.raises(ValueError, match="extraction_on_failure"):
        IndexingPipeline(
            ontology=_ontology(), graph_store=_FakeGraphStore(), llm=_RaisingLLM(),
            extraction_on_failure="bogus",
        )


# --- reasoning-model timeout ---------------------------------------------

def test_kimi_default_timeout_has_headroom():
    backend = create_llm_backend(provider="kimi", api_key="dummy")
    assert backend._timeout == get_provider_spec("kimi").default_timeout
    assert backend._timeout > 120.0


def test_openai_keeps_baseline_timeout():
    backend = create_llm_backend(provider="openai", api_key="dummy")
    assert backend._timeout == 120.0


def test_explicit_timeout_overrides_preset():
    backend = create_llm_backend(provider="kimi", api_key="dummy", timeout=42.0)
    assert backend._timeout == 42.0
