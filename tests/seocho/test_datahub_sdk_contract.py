"""SDK contract test: our pure-dict MCP aspects deserialize into acryl-datahub's
generated aspect classes (schema_classes), turning the ADR-0121/0129 manual
caveat ("verify aspect field names against the target datahub version") into an
automatic drift gate.

Offline and dependency-optional: skips when ``acryl-datahub`` is not installed
(the base ``pip install seocho`` case), runs whenever the ``[datahub]`` extra is
present. It does NOT need a live GMS — it validates shape, not connectivity.
The live gate (test_datahub_live_gate.py) covers the running server.
"""
from __future__ import annotations

import pytest

from seocho.datahub_export import ontology_to_glossary_mcps
from seocho.ontology import NodeDef, Ontology, P

schema_classes = pytest.importorskip(
    "datahub.metadata.schema_classes",
    reason="acryl-datahub not installed; `pip install seocho[datahub]` to run this gate",
)

# aspectName (what datahub_export emits) -> the SDK's generated aspect class.
# If DataHub renames or restructures an aspect, from_obj below raises and this
# test fails, flagging the drift before a live emit would.
_ASPECT_CLASSES = {
    "glossaryNodeInfo": "GlossaryNodeInfoClass",
    "glossaryTermInfo": "GlossaryTermInfoClass",
    "glossaryRelatedTerms": "GlossaryRelatedTermsClass",
}


def _ontology() -> Ontology:
    return Ontology(
        name="contract", package_id="demo.contract", version="1.0.0",
        description="Contract-test ontology.",
        nodes={
            "Animal": NodeDef(description="A creature.",
                              properties={"name": P(str, unique=True), "species": P(str)},
                              identity_keys=["name"]),
            "Breed": NodeDef(description="A breed.",
                             properties={"name": P(str, unique=True)},
                             identity_keys=["name"], broader=["Animal"]),
        },
    )


def test_glossary_aspects_match_sdk_schema():
    mcps = ontology_to_glossary_mcps(_ontology())
    seen = set()
    for m in mcps:
        aspect_name = m["aspectName"]
        cls_name = _ASPECT_CLASSES.get(aspect_name)
        assert cls_name is not None, (
            f"emitted an aspect '{aspect_name}' with no SDK class mapping in this "
            f"contract test — add it to _ASPECT_CLASSES and re-verify")
        cls = getattr(schema_classes, cls_name)
        # from_obj validates field names/types against the PDL-generated schema;
        # a drifted key raises here rather than silently at live-emit time.
        obj = cls.from_obj(m["aspect"])
        # round-trip: no field we sent is dropped by the schema
        round_tripped = obj.to_obj()
        for key in m["aspect"]:
            assert key in round_tripped, (
                f"aspect '{aspect_name}' field '{key}' was dropped by the SDK schema "
                f"(class {cls_name}) — likely a renamed/removed field in this "
                f"acryl-datahub version")
        seen.add(aspect_name)
    # we exercised every glossary aspect the exporter produces
    assert seen == set(_ASPECT_CLASSES)


def test_mcp_wrapper_accepts_our_aspects():
    """The REST emitter wraps each aspect in a MetadataChangeProposalWrapper;
    confirm our dicts survive that construction (the exact path emit_to_datahub
    takes)."""
    mcp_mod = pytest.importorskip("datahub.emitter.mcp")
    mcps = ontology_to_glossary_mcps(_ontology())
    for m in mcps:
        cls = getattr(schema_classes, _ASPECT_CLASSES[m["aspectName"]])
        wrapper = mcp_mod.MetadataChangeProposalWrapper(
            entityUrn=m["entityUrn"], aspect=cls.from_obj(m["aspect"]),
        )
        assert wrapper.entityUrn == m["entityUrn"]


def test_live_emit_import_block_is_importable():
    """Regression (found live): emit_to_datahub's guarded import block must not
    reference names absent from the installed SDK — a stale unused import made
    every live emit silently fall back to mode='unavailable'. With the SDK
    installed and an empty MCP list, a non-dry-run emit must reach mode='live'
    (no network is touched for zero MCPs)."""
    from seocho.datahub_export import emit_to_datahub
    res = emit_to_datahub([], gms_server="http://localhost:1", dry_run=False)
    assert res["mode"] == "live", res.get("error", res)
