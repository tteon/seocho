"""Canonical Cypher identifier validation and quoting.

Single source of truth for turning dynamic strings (labels, relationship
types, property keys) into Cypher fragments. Before this module the same
regex ``^[A-Za-z_][A-Za-z0-9_]*$`` was copied into three places
(:mod:`seocho.store.graph`, :mod:`seocho.ontology`,
``extraction.fulltext_index``) and :mod:`seocho.query.cypher_builder`
hand-rolled a lossy ``.replace("`", "")`` quote. Two policies live here:

* :func:`is_valid_identifier` / :data:`IDENT_RE` — strict whitelist. Use when
  a non-simple identifier should be *rejected* (schema probes, DDL,
  property keys read back from the graph).
* :func:`quote_identifier` / :func:`label_clause` — lossless backtick
  quoting. Use when the identifier is ontology-driven and may legitimately
  contain characters outside the simple set (e.g. RDF labels with
  namespaces). Injection-safe: inside a backtick-quoted identifier the only
  character with syntactic meaning is the backtick, which Cypher escapes by
  *doubling* — so this doubles it rather than stripping it (which would
  silently corrupt the identifier instead of preserving it).

This module imports only the stdlib so it can be pulled in from anywhere in
the package without risking an import cycle.
"""

from __future__ import annotations

import re

__all__ = ["IDENT_RE", "is_valid_identifier", "quote_identifier", "label_clause"]

# Neo4j simple identifier: a letter or underscore, then letters/digits/
# underscores. Anything else must go through ``quote_identifier`` (or be
# rejected up front with ``is_valid_identifier``).
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def is_valid_identifier(value: str) -> bool:
    """Return True if ``value`` is a simple Cypher identifier needing no quoting."""
    return bool(IDENT_RE.match(value))


def quote_identifier(value: str) -> str:
    """Backtick-quote ``value`` for safe interpolation into Cypher.

    Embedded backticks are escaped by doubling (Cypher's own escape rule), so
    the result cannot terminate the quoted identifier early — this is what
    makes raw interpolation of the result injection-safe. Accepts any
    non-empty string; raises :class:`ValueError` otherwise.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(
            f"Cypher identifier must be a non-empty string, got {value!r}"
        )
    return "`" + value.replace("`", "``") + "`"


def label_clause(label: str) -> str:
    """Return a quoted ``:`Label``` clause, or ``""`` when ``label`` is falsy."""
    if not label:
        return ""
    return ":" + quote_identifier(label)
