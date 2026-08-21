"""Cross-source convergence (seocho-zfe D2-2): the same real entity written from
different sources converges to ONE canonical id / one physical node.
"""

from __future__ import annotations

from seocho.index.identity import apply_identity_keys
from seocho.index.shared_intern import SharedInternTable
from seocho.ontology import NodeDef, Ontology, P


def _onto_with(label: str, *, cross_unique: bool):
    return Ontology("erb", package_id="erb", version="1.0.0", nodes={
        label: NodeDef(description="an org", properties={"name": P(str, unique=True)},
                       cross_source_unique=cross_unique),
    })


def _write(table, ws, label, name, *, cross_unique):
    onto = _onto_with(label, cross_unique=cross_unique)
    nodes = [{"label": label, "id": "tmp", "properties": {"name": name}}]
    apply_identity_keys(onto, nodes, [], intern_table=table, workspace_id=ws)
    return nodes[0]["id"]


def test_same_entity_across_sources_converges_to_one_canonical():
    """'Acme' as a Company (jira) and as an Organization (confluence) — both
    cross_source_unique — get the SAME source-agnostic canonical id, so the two
    graph nodes MERGE to one and a cross-source join lands on one node."""
    t = SharedInternTable()
    jira_id = _write(t, "acme", "Company", "Acme Corp", cross_unique=True)
    conf_id = _write(t, "acme", "Organization", "Acme Corp", cross_unique=True)
    assert jira_id == conf_id == "~xs|acme corp", "converged, label-agnostic id"
    assert t.resolve_one("acme", "Acme Corp") == jira_id


def test_non_flagged_multikey_homonyms_stay_separate():
    """A homonym-prone type (identity_keys, NOT cross_source_unique) keeps distinct
    composite ids and surfaces both as candidates — never fused."""
    t = SharedInternTable()
    onto = Ontology("fin", package_id="fin", version="1.0.0", nodes={
        "FinancialMetric": NodeDef(
            description="m", properties={"name": P(str), "company": P(str)},
            identity_keys=["name", "company"]),
    })

    def w(company):
        nodes = [{"label": "FinancialMetric", "id": "t",
                  "properties": {"name": "Total Revenue", "company": company}}]
        apply_identity_keys(onto, nodes, [], intern_table=t, workspace_id="acme")
        return nodes[0]["id"]

    ptc, tsla = w("PTC"), w("Tesla")
    assert ptc != tsla
    assert set(t.candidates("acme", "total revenue")) == {ptc, tsla}
    assert t.resolve_one("acme", "total revenue") == ""   # homonym: no guess


def test_convergence_is_workspace_scoped():
    t = SharedInternTable()
    a = _write(t, "acme", "Company", "Acme Corp", cross_unique=True)
    _write(t, "globex", "Organization", "Acme Corp", cross_unique=True)
    # same content -> same id string, but each tenant's alias is isolated
    assert t.candidates("acme", "acme corp") == (a,)
    assert t.candidates("globex", "widget co") == ()


def test_union_find_reconcile_merges_residual_fragments():
    """The explicit reconcile() (for fragments not caught at write, e.g. a
    concurrent race) unions candidates to one representative."""
    t = SharedInternTable()
    t.alias("acme", "Acme", "company|acme")
    t.alias("acme", "Acme", "organization|acme")
    assert len(t.candidates("acme", "acme")) == 2      # fragmented
    rep = t.reconcile("acme", "acme")
    assert rep == "company|acme"                       # min = deterministic rep
    assert t.candidates("acme", "acme") == (rep,)      # collapsed
    assert t.resolve_one("acme", "acme") == rep


def test_cross_source_unique_roundtrips_through_dict():
    onto = _onto_with("Company", cross_unique=True)
    d = onto.to_dict()
    assert d["nodes"]["Company"]["cross_source_unique"] is True
    back = Ontology.from_dict(d)
    assert back.nodes["Company"].cross_source_unique is True


def test_opt_in_default_off_leaves_composite_behavior():
    t = SharedInternTable()
    # not flagged, single unique name -> legacy: no identity_keys => untouched id
    onto = _onto_with("Company", cross_unique=False)
    nodes = [{"label": "Company", "id": "orig-1", "properties": {"name": "Acme"}}]
    apply_identity_keys(onto, nodes, [], intern_table=t, workspace_id="acme")
    assert nodes[0]["id"] == "orig-1", "no identity_keys + not cross_unique => id untouched"
    assert t.stats()["aliases"] == 0
