"""Tests for the canonical Cypher identifier helper (seocho-91s).

Pins the two policies so the three former copies of the regex can't drift
back apart, and proves backtick quoting can't be broken out of.
"""

import pytest

from seocho.cypher_ident import (
    IDENT_RE,
    is_valid_identifier,
    label_clause,
    quote_identifier,
)


@pytest.mark.parametrize("name", ["Person", "_x", "Foo123", "rel_type", "A"])
def test_valid_simple_identifiers(name):
    assert is_valid_identifier(name)
    assert IDENT_RE.match(name)


@pytest.mark.parametrize(
    "name",
    ["1abc", "has space", "drop`table", "a-b", "", "n.prop", "foo;bar", "ünïcode"],
)
def test_rejected_by_strict_policy(name):
    assert not is_valid_identifier(name)


def test_quote_identifier_wraps_in_backticks():
    assert quote_identifier("Person") == "`Person`"


def test_quote_identifier_escapes_embedded_backtick_by_doubling():
    # Cypher escapes a backtick inside a quoted identifier by doubling it,
    # so the quoted form cannot terminate early. This is the injection guard.
    assert quote_identifier("a`b") == "`a``b`"


def test_quote_identifier_neutralizes_injection_attempt():
    # A label crafted to break out stays trapped inside one quoted identifier:
    # exactly one opening and one closing backtick at the ends, everything
    # between them doubled.
    hostile = "Foo`) DETACH DELETE (n) //"
    quoted = quote_identifier(hostile)
    assert quoted.startswith("`") and quoted.endswith("`")
    assert quoted == "`" + hostile.replace("`", "``") + "`"
    # No lone backtick survives to close the identifier prematurely.
    assert "``) DETACH" in quoted


def test_quote_identifier_is_lossless_unlike_strip():
    # The old cypher_builder did ``.replace("`", "")`` which silently merged
    # ``a`b`` into ``ab``; doubling preserves the original on un-escape.
    quoted = quote_identifier("a`b")
    inner = quoted[1:-1]
    assert inner.replace("``", "`") == "a`b"


@pytest.mark.parametrize("bad", ["", None, 0, []])
def test_quote_identifier_rejects_empty_or_nonstr(bad):
    with pytest.raises(ValueError):
        quote_identifier(bad)


def test_label_clause_quotes_and_prefixes():
    assert label_clause("Person") == ":`Person`"


@pytest.mark.parametrize("empty", ["", None])
def test_label_clause_empty_is_blank(empty):
    assert label_clause(empty) == ""
