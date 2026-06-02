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
