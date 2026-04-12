"""Tests for seocho.prompt_strategy."""

import pytest

from seocho import Seocho
from seocho.ontology import NodeDef, Ontology, P, RelDef
from seocho.prompt_strategy import (
    ExtractionStrategy,
    LinkingStrategy,
    QueryStrategy,
    _sanitize_prompt_value,
)
from seocho.query import PRESET_PROMPTS, PromptTemplate


@pytest.fixture
def ontology():
    return Ontology(
        name="test",
        nodes={
            "Person": NodeDef(properties={"name": P(str, unique=True)}),
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
        },
        relationships={
            "WORKS_AT": RelDef(source="Person", target="Company", cardinality="MANY_TO_ONE"),
        },
    )


class TestExtractionStrategy:
    def test_render_system_contains_ontology(self, ontology):
        ext = ExtractionStrategy(ontology)
        system, user = ext.render("Some text")
        assert "test" in system  # ontology name
        assert "Person" in system
        assert "Company" in system
        assert "WORKS_AT" in system
        assert "JSON format" in system

    def test_render_user_contains_text(self, ontology):
        ext = ExtractionStrategy(ontology)
        _, user = ext.render("Marie Curie worked at University of Paris")
        assert "Marie Curie" in user

    def test_shacl_constraints_injected(self, ontology):
        ext = ExtractionStrategy(ontology, shacl_constraints="Company.name required")
        system, _ = ext.render("text")
        assert "Company.name required" in system

    def test_vocabulary_terms_injected(self, ontology):
        ext = ExtractionStrategy(ontology, vocabulary_terms="Corp → Company")
        system, _ = ext.render("text")
        assert "Corp → Company" in system

    def test_metadata_sanitized(self, ontology):
        ext = ExtractionStrategy(ontology)
        system, _ = ext.render("text", metadata="x" * 5000)
        assert "truncated" in system
        assert len(system) < 20000

    def test_finder_financials_prompt_preserves_segment_line_items(self, ontology):
        system, _ = PRESET_PROMPTS["finder_financials"].render(ontology.to_extraction_context(), "text")
        assert "segment line items" in system
        assert "Do not replace a segment metric with Total Revenues." in system

    def test_local_add_propagates_custom_extraction_prompt(self, ontology):
        class FakeResponse:
            def __init__(self, payload):
                self._payload = payload
                self.usage = None

            def json(self):
                return self._payload

        class FakeLLM:
            def __init__(self):
                self.system_prompts = []
                self._count = 0

            def complete(self, *, system, user, temperature, response_format=None):  # noqa: ANN001
                self.system_prompts.append(system)
                self._count += 1
                if self._count == 1:
                    return FakeResponse(
                        {
                            "nodes": [{"id": "c1", "label": "Company", "properties": {"name": "Samsung"}}],
                            "relationships": [],
                        }
                    )
                return FakeResponse(
                    {
                        "nodes": [{"id": "c1", "label": "Company", "properties": {"name": "Samsung"}}],
                        "relationships": [],
                    }
                )

        class FakeGraphStore:
            def write(self, nodes, relationships, *, database="neo4j", workspace_id="default", source_id=""):  # noqa: ANN001
                return {"nodes_created": len(nodes), "relationships_created": len(relationships), "errors": []}

        client = Seocho(
            ontology=ontology,
            graph_store=FakeGraphStore(),
            llm=FakeLLM(),
            extraction_prompt=PromptTemplate(system="CUSTOM EXTRACTION PROMPT\n{{entity_types}}"),
        )

        client.add("Samsung is a company.", database="neo4j", category="general")

        assert any("CUSTOM EXTRACTION PROMPT" in prompt for prompt in client._engine.llm.system_prompts)


class TestQueryStrategy:
    def test_render_includes_schema(self, ontology):
        qs = QueryStrategy(ontology)
        system, user = qs.render("Who works at Samsung?")
        assert "Person" in system
        assert "WORKS_AT" in system
        assert "Ontology Query Profile" in system
        assert "UNIQUE" in system
        assert "many-to-one" in system.lower()

    def test_render_user_has_question(self, ontology):
        qs = QueryStrategy(ontology)
        _, user = qs.render("Who works at Samsung?")
        assert "Samsung" in user

    def test_schema_info_injected(self, ontology):
        qs = QueryStrategy(ontology, schema_info={"total_nodes": 5000})
        system, _ = qs.render("test")
        assert "5000" in system

    def test_render_answer(self, ontology):
        qs = QueryStrategy(ontology)
        system, user = qs.render_answer("Who?", '[{"name": "Alice"}]')
        assert "test" in system  # ontology name
        assert "Alice" in user
        assert "Who?" in user

    def test_schema_info_sanitized(self, ontology):
        qs = QueryStrategy(ontology, schema_info={"bad\x00key": "val" * 1000})
        system, _ = qs.render("test")
        assert "\x00" not in system


class TestLinkingStrategy:
    def test_render_includes_ontology(self, ontology):
        ls = LinkingStrategy(ontology)
        system, user = ls.render('{"nodes": []}')
        assert "test" in system
        assert "Person" in system
        assert "canonical ID" in system.lower() or "linked_id" in system.lower()

    def test_render_user_has_entities(self, ontology):
        ls = LinkingStrategy(ontology)
        _, user = ls.render('{"nodes": [{"id": "p1"}]}')
        assert "p1" in user


class TestSanitize:
    def test_truncation(self):
        result = _sanitize_prompt_value("x" * 5000)
        assert len(result) < 2100
        assert "truncated" in result

    def test_control_chars_stripped(self):
        result = _sanitize_prompt_value("hello\x00\x01\x02world")
        assert "\x00" not in result
        assert "helloworld" in result

    def test_normal_text_unchanged(self):
        result = _sanitize_prompt_value("normal text 123")
        assert result == "normal text 123"

    def test_newlines_preserved(self):
        result = _sanitize_prompt_value("line1\nline2")
        assert "\n" in result
