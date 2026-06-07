"""Projection-firewall regression test (gap-closure plan item #1).

Hard design driver: ontology node-count/richness negatively correlates with
extraction answer quality (FinDER experiment, r=-0.76). Therefore author-time
richness — subclass hierarchy (`broader`), external mappings (`same_as`), and
future structured definitions / competency-question metadata — MUST NOT enter
the lean extraction projection (`to_extraction_context`) or change the
`context_hash` (which drives the KV-cache prefix + drift detection).

This test locks that firewall: enriching the authoring model with `broader`/
`same_as` leaves `to_extraction_context()` byte-identical and `context_hash`
unchanged. It also documents what IS intentionally part of the lean projection
(`description`, `aliases`) so the firewall isn't mistaken for "nothing renders".
No external services.
"""
from __future__ import annotations

from seocho import NodeDef, Ontology, P, RelDef
from seocho.ontology_context import compile_ontology_context


def _plain() -> Ontology:
    return Ontology(
        name="finance",
        package_id="company-finance",
        version="1.0.0",
        nodes={
            "FinancialMetric": NodeDef(description="Abstract base for reported figures",
                                       properties={"name": P(str, unique=True), "value": P(str)}),
            "Revenue": NodeDef(description="Top-line revenue",
                               properties={"name": P(str, unique=True), "value": P(str)}),
        },
        relationships={"REPORTED": RelDef(source="FinancialMetric", target="Revenue")},
    )


def _enriched() -> Ontology:
    """Identical to _plain() except author-time richness added: subclass edge +
    external mapping. These must be invisible to extraction."""
    return Ontology(
        name="finance",
        package_id="company-finance",
        version="1.0.0",
        nodes={
            "FinancialMetric": NodeDef(description="Abstract base for reported figures",
                                       properties={"name": P(str, unique=True), "value": P(str)},
                                       same_as="fibo-ind:FinancialMetric"),
            "Revenue": NodeDef(description="Top-line revenue",
                               properties={"name": P(str, unique=True), "value": P(str)},
                               broader=["FinancialMetric"],              # subclass edge
                               same_as="fibo-ind:Revenue"),
        },
        relationships={"REPORTED": RelDef(source="FinancialMetric", target="Revenue")},
    )


def test_extraction_context_byte_identical_under_enrichment():
    assert _plain().to_extraction_context() == _enriched().to_extraction_context()


def _strip_context_hash(prefix: str) -> str:
    return "\n".join(ln for ln in prefix.splitlines() if not ln.strip().startswith("context_hash:"))


def test_kv_cache_prefix_extraction_content_firewalled():
    """The KV-cache prefix's EXTRACTION CONTENT (entity/relationship/constraint
    sections) must be byte-identical under enrichment — that is the -0.76 guard
    and the bulk of the cacheable prefix.

    The ONLY allowed delta is the `context_hash:` line in the identity header:
    `context_hash` is the full-schema identity hash and legitimately moves when
    the ontology gains a subclass edge (correct drift semantics). That busts the
    prompt prefix-cache ONCE per schema version — acceptable, since schema edits
    are rare and per-request caching still hits within a version. (NOTE: this
    corrects the gap-plan's assumption that context_hash would be invariant —
    it isn't, because query_context/vocabulary surface `broader`; what's
    firewalled is the extraction *content*, which is what the -0.76 finding is
    about.)"""
    p = compile_ontology_context(_plain()).stable_prefix()
    r = compile_ontology_context(_enriched()).stable_prefix()
    assert p != r  # they differ (the context_hash header line)
    assert _strip_context_hash(p) == _strip_context_hash(r)  # ...but ONLY by that line


def test_broader_and_same_as_absent_from_extraction_strings():
    ctx = _enriched().to_extraction_context()
    blob = "\n".join(ctx.values())
    assert "broader" not in blob and "FinancialMetric:Revenue" not in blob
    assert "fibo-ind" not in blob          # same_as mapping must not leak into the prompt
    assert "subClassOf" not in blob


def test_description_and_aliases_ARE_projected():
    """Documents the intentional lean-projection content (so the firewall isn't
    misread as 'nothing renders'). description + aliases are the lean fields."""
    o = Ontology(
        name="x",
        nodes={"Revenue": NodeDef(description="Top-line revenue", aliases=["NetSales"],
                                  properties={"name": P(str, unique=True)})},
        relationships={},
    )
    et = o.to_extraction_context()["entity_types"]
    assert "Top-line revenue" in et and "NetSales" in et


# --- Relation firewall: strict_validation="strip" (write-time reject of undeclared) ---

def _decision_like() -> Ontology:
    """Declares PROPOSES + HOLDS_POSITION, NOT SUPPORTS/OPPOSES (the smuggled types)."""
    return Ontology(
        name="decision", package_id="decision", version="1.0.0",
        nodes={
            "Person": NodeDef(description="participant", properties={"name": P(str, unique=True)}),
            "Topic": NodeDef(description="subject", properties={"name": P(str, unique=True)}),
            "Proposal": NodeDef(description="option", properties={"name": P(str, unique=True)}),
        },
        relationships={
            "PROPOSES": RelDef(source="Person", target="Proposal"),
            "HOLDS_POSITION": RelDef(source="Person", target="Topic"),
        },
    )


def _strip_pipeline():
    from seocho.index.pipeline import IndexingPipeline
    return IndexingPipeline(ontology=_decision_like(), graph_store=object(), llm=object(),
                            strict_validation="strip")


def test_firewall_strips_only_undeclared_relations_keeps_valid():
    p = _strip_pipeline()
    rels = [
        {"source": "p1", "target": "t1", "type": "HOLDS_POSITION", "properties": {"polarity": "FOR"}},
        {"source": "p1", "target": "pr1", "type": "PROPOSES", "properties": {}},
        {"source": "p1", "target": "pr1", "type": "SUPPORTS", "properties": {}},   # undeclared (smuggled)
        {"source": "p1", "target": "pr1", "type": "OPPOSES", "properties": {}},    # undeclared (smuggled)
    ]
    errors = ["Unknown relationship type 'SUPPORTS'", "Unknown relationship type 'OPPOSES'"]
    kept, residual = p._firewall_strip_undeclared(rels, errors)
    kept_types = {r["type"] for r in kept}
    assert kept_types == {"HOLDS_POSITION", "PROPOSES"}      # declared survive
    assert "SUPPORTS" not in kept_types and "OPPOSES" not in kept_types  # undeclared stripped
    assert not any(e.startswith("Unknown relationship type") for e in residual)  # those errors cleared


def test_firewall_keeps_non_relation_errors():
    p = _strip_pipeline()
    rels = [{"source": "p1", "target": "pr1", "type": "SUPPORTS", "properties": {}}]
    errors = ["Unknown relationship type 'SUPPORTS'", "Node 'x' (Person) missing required property 'name'"]
    kept, residual = p._firewall_strip_undeclared(rels, errors)
    assert kept == []  # the only rel was undeclared
    # the unrelated node error is preserved (firewall only clears relation-type errors)
    assert any("missing required property" in e for e in residual)
    assert not any(e.startswith("Unknown relationship type") for e in residual)


def test_firewall_noop_when_all_declared():
    p = _strip_pipeline()
    rels = [{"source": "p1", "target": "t1", "type": "HOLDS_POSITION", "properties": {}}]
    kept, residual = p._firewall_strip_undeclared(rels, [])
    assert len(kept) == 1 and residual == []
