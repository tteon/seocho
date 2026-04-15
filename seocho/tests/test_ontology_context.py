from __future__ import annotations

from seocho import NodeDef, Ontology, P, RelDef
from seocho.index.pipeline import IndexingPipeline
from seocho.ontology_context import OntologyContextCache, compile_ontology_context


def _ontology(version: str = "1.0.0") -> Ontology:
    return Ontology(
        name="finance",
        package_id="company-finance",
        version=version,
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
            "FinancialMetric": NodeDef(properties={"name": P(str), "value": P(str)}),
        },
        relationships={
            "REPORTED": RelDef(source="Company", target="FinancialMetric"),
        },
    )


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return dict(self._payload)


class _FakeLLM:
    model = "fake-model"

    def complete(self, *, system, user, temperature, response_format=None):  # noqa: ANN001
        return _FakeResponse(
            {
                "nodes": [
                    {"id": "acme", "label": "Company", "properties": {"name": "ACME"}},
                    {
                        "id": "revenue",
                        "label": "FinancialMetric",
                        "properties": {"name": "Revenue", "value": "10"},
                    },
                ],
                "relationships": [
                    {"source": "acme", "target": "revenue", "type": "REPORTED", "properties": {}}
                ],
            }
        )


class _FakeGraphStore:
    def write(self, nodes, relationships, *, database="neo4j", workspace_id="default", source_id=""):  # noqa: ANN001
        return {
            "nodes_created": len(nodes),
            "relationships_created": len(relationships),
            "errors": [],
        }


def test_compile_ontology_context_has_stable_identity() -> None:
    first = compile_ontology_context(
        _ontology(),
        workspace_id="acme",
        profile="finder-financials",
    )
    second = compile_ontology_context(
        _ontology(),
        workspace_id="acme",
        profile="finder-financials",
    )
    changed = compile_ontology_context(
        _ontology(version="1.1.0"),
        workspace_id="acme",
        profile="finder-financials",
    )

    assert first.descriptor.context_hash == second.descriptor.context_hash
    assert first.descriptor.context_hash != changed.descriptor.context_hash
    assert first.descriptor.ontology_id == "company-finance"
    assert first.descriptor.profile == "finder-financials"
    assert first.descriptor.glossary_term_count >= 3
    assert first.descriptor.glossary_hash
    assert "financial_metric_lookup" in first.descriptor.deterministic_intents


def test_glossary_aliases_are_part_of_context_identity() -> None:
    base = _ontology()
    aliased = _ontology()
    aliased.nodes["Company"].aliases.append("Issuer")

    base_context = compile_ontology_context(base)
    aliased_context = compile_ontology_context(aliased)

    assert base_context.descriptor.glossary_hash != aliased_context.descriptor.glossary_hash
    assert base_context.descriptor.context_hash != aliased_context.descriptor.context_hash


def test_ontology_context_cache_tracks_hits() -> None:
    ontology = _ontology()
    cache = OntologyContextCache(max_size=2)

    first = cache.get(ontology, workspace_id="acme", profile="default")
    second = cache.get(ontology, workspace_id="acme", profile="default")

    assert first is second
    assert cache.stats()["hits"] == 1
    assert cache.stats()["misses"] == 1


def test_indexing_result_records_ontology_context() -> None:
    pipeline = IndexingPipeline(
        ontology=_ontology(),
        graph_store=_FakeGraphStore(),
        llm=_FakeLLM(),
        workspace_id="acme",
        ontology_profile="finder-financials",
    )

    result = pipeline.index("ACME reported revenue of 10.", database="neo4j")
    payload = result.to_dict()

    assert result.ok is True
    assert payload["ontology_context"]["workspace_id"] == "acme"
    assert payload["ontology_context"]["profile"] == "finder-financials"
    assert payload["ontology_context"]["context_hash"]
