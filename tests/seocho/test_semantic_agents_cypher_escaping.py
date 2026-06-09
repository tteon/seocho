"""Regression for #136 — LPGAgent's Cypher builders must backtick-quote
ontology-derived labels and relation types. anchor_label / relation_types come
from semantic artifacts derived from uploaded-document ontology candidates, so
an unescaped backtick/space/Cypher fragment could break out of the clause.
(Fix #25 only hardened cypher_builder.py; these three builders were left open.)
"""

from __future__ import annotations

from seocho.query.semantic_agents import LPGAgent

# A label that tries to close the clause and append a destructive statement.
_EVIL_LABEL = "Foo`) DETACH DELETE n //"
_EVIL_REL = "REL`]->() DETACH DELETE m //"


def _assert_safely_quoted(query: str, evil: str) -> None:
    # The raw payload (with a single backtick) must NOT appear verbatim...
    assert evil not in query
    # ...because every backtick is doubled and the whole thing is wrapped, so
    # the escaped form is what's present.
    assert "`" + evil.replace("`", "``") + "`" in query


def test_relationship_query_escapes_label_and_relations() -> None:
    query = LPGAgent._relationship_query(
        anchor_label=_EVIL_LABEL, relation_types=[_EVIL_REL]
    )
    _assert_safely_quoted(query, _EVIL_LABEL)
    _assert_safely_quoted(query, _EVIL_REL)


def test_responsibility_query_escapes_label_and_relations() -> None:
    query = LPGAgent._responsibility_query(
        anchor_label=_EVIL_LABEL, relation_types=[_EVIL_REL]
    )
    _assert_safely_quoted(query, _EVIL_LABEL)
    _assert_safely_quoted(query, _EVIL_REL)


def test_entity_summary_query_escapes_label() -> None:
    query = LPGAgent._entity_summary_query(anchor_label=_EVIL_LABEL)
    _assert_safely_quoted(query, _EVIL_LABEL)


def test_clean_label_still_renders_simple_clause() -> None:
    query = LPGAgent._relationship_query(anchor_label="Company", relation_types=["REPORTED"])
    assert ":`Company`" in query
    assert ":`REPORTED`" in query


def test_empty_label_and_relations_render_no_clause() -> None:
    query = LPGAgent._relationship_query(anchor_label="", relation_types=[])
    assert "MATCH (n)" in query  # no stray colon
    assert "[r]" in query
