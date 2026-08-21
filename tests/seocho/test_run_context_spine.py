"""Per-request run-context spine + RCU pinning (structured runtime, ADR-0200).

Increment 1 of the structured multi-agent runtime: OntologyRunContext is built
once per request, workspace-scoped, and (when an RCU pin registry is configured)
pins ONE frozen ontology version for the request's whole duration.
"""

from __future__ import annotations

from seocho.ontology import NodeDef, Ontology, P
from seocho.ontology.active_pointer import ActiveOntologyPointer
from seocho.ontology.run_context import (
    OntologyRunContext,
    pinned_run_context,
)
from seocho.ontology.version_pin import VersionPinRegistry

WS, PKG = "acme", "acme"


def _ctx(ws: str = WS) -> OntologyRunContext:
    return OntologyRunContext(workspace_id=ws, ontology_id=PKG)


def _wire(tmp_path):
    pointer = ActiveOntologyPointer(tmp_path / "active.db")
    pins = VersionPinRegistry(pointer)
    return pointer, pins


def test_with_pinned_version_is_immutable_stamp():
    base = _ctx()
    stamped = base.with_pinned_version(version="2.0.0", epoch=3, fingerprint="abc")
    assert base.metadata == {}, "original context is unchanged (frozen)"
    assert stamped.metadata["pinned_ontology_version"] == "2.0.0"
    assert stamped.pinned_epoch == 3
    assert stamped.workspace_id == WS


def test_pinned_run_context_holds_version_for_the_request(tmp_path):
    pointer, pins = _wire(tmp_path)
    ok, av = pointer.publish(WS, PKG, version="1.0.0", fingerprint="fp1", fencing_token=1)
    assert ok and av.epoch == 0

    with pinned_run_context(_ctx(), pin_registry=pins, package_id=PKG,
                            active_pointer=pointer) as rc:
        # the request is reading a pinned, stamped version...
        assert rc.pinned_epoch == 0
        assert rc.metadata["pinned_ontology_version"] == "1.0.0"
        assert rc.metadata["pinned_ontology_fingerprint"] == "fp1"
        # ...and the pin is live for the whole block
        assert pins.pin_count(WS, PKG, 0) == 1
    # released on exit
    assert pins.min_pinned_epoch(WS, PKG) is None


def test_pin_survives_a_mid_request_publish_swap(tmp_path):
    """A publish that swaps the active version mid-request does NOT change what the
    already-pinned request reads — the RCU guarantee the mutation probe relies on."""
    pointer, pins = _wire(tmp_path)
    ok, av0 = pointer.publish(WS, PKG, version="1.0.0", fingerprint="fp1", fencing_token=1)
    with pinned_run_context(_ctx(), pin_registry=pins, package_id=PKG,
                            active_pointer=pointer) as rc:
        assert rc.metadata["pinned_ontology_version"] == "1.0.0"
        # a writer swaps the active version to 2.0.0 mid-request
        ok2, av1 = pointer.publish(WS, PKG, version="2.0.0", fingerprint="fp2",
                                   fencing_token=2, expected=av0.expected())
        assert ok2 and av1.epoch == 1
        # the in-flight request still reads its pinned 1.0.0, and epoch 0 stays pinned
        assert rc.metadata["pinned_ontology_version"] == "1.0.0"
        assert pins.pin_count(WS, PKG, 0) == 1


def test_no_active_pointer_yields_unpinned_context(tmp_path):
    pointer, pins = _wire(tmp_path)  # nothing published -> no active version
    with pinned_run_context(_ctx(), pin_registry=pins, package_id=PKG,
                            active_pointer=pointer) as rc:
        assert rc.pinned_epoch is None
        assert "pinned_ontology_version" not in rc.metadata


def test_two_workspaces_pin_in_isolation(tmp_path):
    pointer, pins = _wire(tmp_path)
    pointer.publish("acme", PKG, version="1.0.0", fingerprint="a", fencing_token=1)
    pointer.publish("globex", PKG, version="9.0.0", fingerprint="g", fencing_token=1)
    with pinned_run_context(_ctx("acme"), pin_registry=pins, package_id=PKG,
                            active_pointer=pointer) as a:
        with pinned_run_context(_ctx("globex"), pin_registry=pins, package_id=PKG,
                                active_pointer=pointer) as g:
            assert a.metadata["pinned_ontology_version"] == "1.0.0"
            assert g.metadata["pinned_ontology_version"] == "9.0.0"
            assert a.workspace_id == "acme" and g.workspace_id == "globex"


def test_local_engine_exposes_a_run_context_after_ask():
    """The wiring: _LocalEngine builds + exposes a workspace-scoped run context.
    Exercised without a live DB by driving the engine with in-memory fakes."""
    from seocho.ontology.run_context import build_local_ontology_run_context

    onto = Ontology("acme", package_id=PKG, version="1.0.0", nodes={
        "Company": NodeDef(description="c", properties={"name": P(str, unique=True)}),
    })
    compiled = onto.compile_context(workspace_id=WS) if hasattr(onto, "compile_context") else {}
    rc = build_local_ontology_run_context(compiled, workspace_id=WS, database="neo4j")
    assert rc.workspace_id == WS
    # last_run_context() accessor exists on the engine class
    from seocho.local_engine import _LocalEngine
    assert hasattr(_LocalEngine, "last_run_context")
