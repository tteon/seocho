"""Env-guarded live gate against a running DataHub GMS (ADR-0150 follow-up).

Skipped unless ``SEOCHO_DATAHUB_GMS`` points at a live server — CI stays green
without Docker, and a developer with ``datahub docker quickstart`` up runs the
real thing:

    pip install "seocho[datahub]"
    datahub docker quickstart
    SEOCHO_DATAHUB_GMS=http://localhost:8080 \
        python -m pytest tests/seocho/test_datahub_live_gate.py -v

This is the gate that must pass before we claim "DataHub integration" works
against a given datahub version (the ADR-0121/0129 caveat). It exercises the
three things offline tests cannot: a real emit, the structuredProperty
definition bootstrap that live emit requires, and a GraphQL read-back.
"""
from __future__ import annotations

import os

import pytest

GMS = os.environ.get("SEOCHO_DATAHUB_GMS")
TOKEN = os.environ.get("SEOCHO_DATAHUB_TOKEN")

pytestmark = pytest.mark.skipif(
    not GMS,
    reason="set SEOCHO_DATAHUB_GMS=http://localhost:8080 (with `datahub docker quickstart` up) to run the live gate",
)


def _ontology():
    from seocho.ontology import NodeDef, Ontology, P
    return Ontology(
        name="livegate", package_id="demo.livegate", version="1.0.0",
        description="Live-gate ontology.",
        nodes={"Animal": NodeDef(description="A creature.",
                                 properties={"name": P(str, unique=True)}, identity_keys=["name"])},
    )


def test_emit_glossary_is_idempotent():
    """A real emit to the GMS succeeds, and a second emit is an idempotent
    UPSERT (deterministic URNs)."""
    from seocho.datahub_export import emit_to_datahub, ontology_to_glossary_mcps
    mcps = ontology_to_glossary_mcps(_ontology())
    first = emit_to_datahub(mcps, gms_server=GMS, token=TOKEN, dry_run=False)
    assert first["emitted"] is True, first
    second = emit_to_datahub(mcps, gms_server=GMS, token=TOKEN, dry_run=False)
    assert second["emitted"] is True
    assert second["sent"] == first["sent"]  # same aspect count, UPSERT semantics


@pytest.mark.xfail(reason="structuredProperty definitions must be bootstrapped first "
                          "(seocho-v6w.2) — GMS rejects values for undefined properties",
                   strict=False)
def test_scorecard_structured_properties_emit():
    """Phase C scorecard emit needs the structuredProperty *definitions* to exist
    on the GMS first; until the bootstrap (seocho-v6w.2) ships this is expected to
    fail against a fresh quickstart. Marks the dependency explicitly rather than
    hiding it."""
    from datahub.emitter.mcp import MetadataChangeProposalWrapper  # noqa: F401
    from datahub.emitter.rest_emitter import DatahubRestEmitter
    from datahub.metadata.schema_classes import StructuredPropertiesClass

    from seocho.datahub_export import _node_urn, scorecard_to_structured_properties
    scorecard = {"overall_score": 0.8, "grade": "B", "blocking": False,
                 "dimensions": [{"name": "taxonomy_health", "score": 0.7}]}
    mcps = scorecard_to_structured_properties(scorecard, target_urn=_node_urn("demo.livegate"))
    emitter = DatahubRestEmitter(gms_server=GMS, token=TOKEN)
    for m in mcps:
        emitter.emit_mcp(MetadataChangeProposalWrapper(
            entityUrn=m["entityUrn"],
            aspect=StructuredPropertiesClass.from_obj(m["aspect"]),
        ))


def test_glossary_read_back():
    """Read the emitted glossary back via GraphQL — the shape the live pull
    adapter (seocho-v6w.3) will normalize into term_records. Asserts our emitted
    term is findable; the normalization itself is that ticket's work."""
    import requests

    from seocho.datahub_export import _term_urn
    from seocho.datahub_export import emit_to_datahub, ontology_to_glossary_mcps
    emit_to_datahub(ontology_to_glossary_mcps(_ontology()),
                    gms_server=GMS, token=TOKEN, dry_run=False)
    urn = _term_urn("demo.livegate.Animal")
    query = """query($urn:String!){ glossaryTerm(urn:$urn){ urn properties{ name definition } } }"""
    headers = {"Content-Type": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    resp = requests.post(f"{GMS}/api/graphql", json={"query": query, "variables": {"urn": urn}},
                         headers=headers, timeout=30)
    resp.raise_for_status()
    term = (resp.json().get("data") or {}).get("glossaryTerm") or {}
    assert term.get("urn") == urn, resp.text
    assert (term.get("properties") or {}).get("name") == "Animal"
