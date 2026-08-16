"""Read-side canonical resolution via the name-alias index (seocho-t28/zfe, D2).

Closes the write/read address mismatch: a MULTI-key entity is interned under its
composite identity (label|name|company|year), which a bare mention cannot rebuild,
so the read always missed. The name-alias index lets a read that knows only the
mention text find the entity — surfacing homonyms as candidates, never guessing.
"""

from __future__ import annotations

from seocho.index.identity import apply_identity_keys
from seocho.index.shared_intern import SharedInternTable
from seocho.ontology import NodeDef, Ontology, P


def _multikey_onto():
    # FinancialMetric identified by (name, company, year) — the homonym-prone case.
    return Ontology("fin", package_id="fin", version="1.0.0", nodes={
        "FinancialMetric": NodeDef(
            description="a metric",
            properties={"name": P(str), "company": P(str), "year": P(str)},
            identity_keys=["name", "company", "year"],
        ),
    })


def _write(table, ws, name, company, year):
    onto = _multikey_onto()
    nodes = [{"label": "FinancialMetric", "id": "tmp",
              "properties": {"name": name, "company": company, "year": year}}]
    apply_identity_keys(onto, nodes, [], intern_table=table, workspace_id=ws)
    return nodes[0]["id"]     # the canonical id assigned


def test_alias_registered_on_write_and_stats():
    t = SharedInternTable()
    cid = _write(t, "acme", "Total Revenue", "PTC", "2024")
    assert t.stats()["aliases"] == 1
    assert t.candidates("acme", "total revenue") == (cid,)
    assert t.candidates("acme", "Total Revenue") == (cid,)   # normalized


def test_read_resolves_a_multikey_entity_by_bare_name():
    """t28: the write interned label|name|company|year; a bare 'PTC' mention could
    never rebuild that — the alias index makes the read land on the same canonical."""
    from seocho.query.intern_grounding import resolve_mentions
    t = SharedInternTable()
    cid = _write(t, "acme", "PTC", "PTC Inc", "2024")   # name is 'PTC'
    resolved, unresolved = resolve_mentions(
        "What was PTC?", intern_table=t, ontology=_multikey_onto(), workspace_id="acme")
    assert ("PTC", cid) in resolved


def test_homonym_surfaces_candidates_not_a_guess():
    t = SharedInternTable()
    ptc = _write(t, "acme", "Total Revenue", "PTC", "2024")
    tsla = _write(t, "acme", "Total Revenue", "Tesla", "2024")
    assert ptc != tsla, "distinct composite identities stay distinct nodes"
    cands = t.candidates("acme", "total revenue")
    assert set(cands) == {ptc, tsla} and len(cands) == 2
    # resolve_one refuses to guess a homonym
    assert t.resolve_one("acme", "total revenue") == ""


def test_workspace_isolation_of_alias():
    # The alias index is workspace-scoped: one tenant's names never appear as
    # another tenant's candidates. (Canonical ids are content-addressed, so an
    # IDENTICAL entity in two tenants legitimately shares the id STRING — isolation
    # is by the (workspace, name) key here and by _workspace_id + the filter in the
    # graph, not by mangling the id.)
    t = SharedInternTable()
    a = _write(t, "acme", "Acme Widget", "Acme", "2024")
    g = _write(t, "globex", "Globex Gadget", "Globex", "2024")
    assert t.candidates("acme", "acme widget") == (a,)
    assert t.candidates("acme", "globex gadget") == ()      # acme cannot see globex's
    assert t.candidates("globex", "globex gadget") == (g,)
    assert t.candidates("globex", "acme widget") == ()
    assert t.resolve_one("acme", "acme widget") == a


def test_additive_no_alias_calls_leaves_intern_unchanged():
    # A table used only via intern()/get() behaves exactly as before.
    t = SharedInternTable()
    t.intern("ws", "company|acme", "id-1")
    assert t.get("ws", "company|acme") == "id-1"
    assert t.stats()["aliases"] == 0
    assert t.candidates("ws", "acme") == ()
