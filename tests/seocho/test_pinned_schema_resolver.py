"""PinnedSchemaResolver — real per-request pinned-ontology delivery (B1 fix)."""

from __future__ import annotations

from seocho.ontology import NodeDef, Ontology, P
from seocho.ontology.run_context import OntologyRunContext
from seocho.ontology.snapshot_store import OntologySnapshotStore
from seocho.query.pinned_schema import PinnedSchemaResolver

PKG = "acme"


def _v1() -> Ontology:
    return Ontology("acme", package_id=PKG, version="1.0.0", nodes={
        "Company": NodeDef(description="c", properties={"name": P(str, unique=True)}),
    })


def _v2() -> Ontology:
    return Ontology("acme", package_id=PKG, version="2.0.0", nodes={
        "Company": NodeDef(description="c", properties={"name": P(str, unique=True)}),
        "Regulation": NodeDef(description="r", properties={"name": P(str, unique=True)}),
    })


def _store(tmp_path):
    s = OntologySnapshotStore(tmp_path / "snaps")
    s.save(_v1())
    s.save(_v2())
    return s


def test_resolves_frozen_schema_from_the_pinned_version(tmp_path):
    r = PinnedSchemaResolver(_store(tmp_path))
    rs1 = r.resolve(PKG, "1.0.0")
    rs2 = r.resolve(PKG, "2.0.0")
    assert set(rs1.ontology.nodes) == {"Company"}
    assert set(rs2.ontology.nodes) == {"Company", "Regulation"}
    # prompt schema and guardrail policy come from the SAME frozen snapshot
    assert "Regulation" in rs2.schema_text() and "Regulation" not in rs1.schema_text()
    assert rs1.policy is not None and rs2.policy is not None


def test_cache_is_by_fingerprint_and_tenant_agnostic(tmp_path):
    r = PinnedSchemaResolver(_store(tmp_path))
    a = r.resolve(PKG, "1.0.0")
    b = r.resolve(PKG, "1.0.0")
    assert a is b, "same (package, version, fingerprint) returns the cached block"
    assert r.stats() == {"entries": 1, "hits": 1, "misses": 1}


def test_pinned_older_version_is_stable_after_a_new_publish(tmp_path):
    """A request that pinned 1.0.0 resolves 1.0.0's schema even after 2.0.0 exists
    — the frozen-read guarantee that makes the mutation probe meaningful."""
    store = _store(tmp_path)
    r = PinnedSchemaResolver(store)
    pinned = r.resolve(PKG, "1.0.0")
    # a newer version is already published (2.0.0) — must not affect the pinned read
    assert set(pinned.ontology.nodes) == {"Company"}
    assert "Regulation" not in pinned.schema_text()


def test_resolve_for_run_context_reads_the_pin(tmp_path):
    r = PinnedSchemaResolver(_store(tmp_path))
    ctx = OntologyRunContext(workspace_id="acme", ontology_id=PKG).with_pinned_version(
        version="2.0.0", epoch=1, fingerprint="x")
    rs = r.resolve_for(ctx)
    assert rs is not None and rs.version == "2.0.0"
    # an unpinned context resolves nothing
    assert r.resolve_for(OntologyRunContext(workspace_id="acme", ontology_id=PKG)) is None


def test_unknown_version_resolves_none(tmp_path):
    r = PinnedSchemaResolver(_store(tmp_path))
    assert r.resolve(PKG, "9.9.9") is None
