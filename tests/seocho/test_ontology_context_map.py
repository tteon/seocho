"""Tests for the DDD bounded-context / context map (Eric Evans).

Demonstrates the benefit over post-hoc OntologyDriftError: boundary problems
(blurred ownership, broken anti-corruption translations) are caught proactively
by validate(), before any data flows.
"""

from __future__ import annotations

from seocho.ontology_context_map import BoundedContext, ContextMap


def _bc(name, concepts, translations=None):
    return BoundedContext(name=name, concepts=frozenset(concepts), translations=translations or {})


def test_disjoint_contexts_are_consistent():
    mm = ContextMap()
    mm.register(_bc("sales", ["Lead", "Opportunity", "Account"]))
    mm.register(_bc("finance", ["Invoice", "Payment", "Account".replace("Account", "Ledger")]))
    assert mm.validate() == []
    assert mm.is_consistent()


def test_shared_ownership_is_flagged():
    mm = ContextMap()
    mm.register(_bc("sales", ["Account", "Lead"]))
    mm.register(_bc("finance", ["Account", "Invoice"]))  # both own "Account"
    violations = mm.validate()
    kinds = {v.kind for v in violations}
    assert "shared_ownership" in kinds
    assert any("Account" in v.detail for v in violations)


def test_valid_cross_context_translation_passes():
    mm = ContextMap()
    mm.register(_bc("finance", ["Ledger"]))
    # sales' Account translates to finance's Ledger (which finance owns) -> clean
    mm.register(_bc("sales", ["Account"], {"Account": "finance:Ledger"}))
    assert mm.validate() == []
    assert mm.translate("Account", from_context="sales") == "finance:Ledger"


def test_dangling_translation_to_unowned_concept_is_flagged():
    mm = ContextMap()
    mm.register(_bc("finance", ["Ledger"]))
    mm.register(_bc("sales", ["Account"], {"Account": "finance:Invoice"}))  # finance doesn't own Invoice
    violations = mm.validate()
    assert any(v.kind == "dangling_translation" for v in violations)


def test_translation_to_external_vocab_is_not_a_boundary_violation():
    mm = ContextMap()
    # schema: is an external vocabulary, not a registered context -> left alone
    mm.register(_bc("sales", ["Account"], {"Account": "schema:Organization"}))
    assert mm.validate() == []


def test_owners_of_lists_all_owning_contexts():
    mm = ContextMap()
    mm.register(_bc("sales", ["Account"]))
    mm.register(_bc("finance", ["Account"]))
    assert mm.owners_of("Account") == ["finance", "sales"]


def test_from_ontology_extracts_concepts_and_translations():
    # duck-typed ontology: .nodes maps label -> object with .same_as
    class _Node:
        def __init__(self, same_as=None):
            self.same_as = same_as

    class _Onto:
        name = "sales"
        nodes = {"Account": _Node(same_as="finance:Ledger"), "Lead": _Node()}

    ctx = BoundedContext.from_ontology("sales", _Onto())
    assert ctx.concepts == frozenset({"Account", "Lead"})
    assert ctx.translations == {"Account": "finance:Ledger"}
