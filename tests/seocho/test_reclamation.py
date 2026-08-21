"""EBR safe-reclamation gate (seocho-ia4.4, RCU B3) — pins gate reclamation."""

from __future__ import annotations

from seocho.ontology import NodeDef, Ontology, P
from seocho.ontology.active_pointer import ActiveOntologyPointer
from seocho.ontology.reclamation import SafeReclamationGate
from seocho.ontology.snapshot_store import OntologySnapshotStore
from seocho.ontology.version_pin import VersionPinRegistry

WS, PKG = "acme", "acme"


def _onto(version: str, extra: bool = False) -> Ontology:
    nodes = {"Company": NodeDef(description="A company.", properties={"name": P(str, unique=True)})}
    if extra:
        nodes["Person"] = NodeDef(description="A person.", properties={"name": P(str, unique=True)})
    return Ontology("acme", package_id=PKG, version=version, nodes=nodes)


def _wire(tmp_path):
    store = OntologySnapshotStore(tmp_path / "snaps")
    pointer = ActiveOntologyPointer(tmp_path / "active.db")
    pins = VersionPinRegistry(pointer)
    gate = SafeReclamationGate(pin_registry=pins, snapshot_store=store, path=tmp_path / "retired.db")
    return store, pointer, pins, gate


def _publish_v1_then_v2(store, pointer, gate):
    v1, v2 = _onto("1.0.0"), _onto("2.0.0", extra=True)
    s1 = store.save(v1)
    s2 = store.save(v2)
    ok, av1 = pointer.publish(WS, PKG, version="1.0.0", fingerprint=s1.schema_fingerprint, fencing_token=1)
    assert ok and av1.epoch == 0
    return s1, s2, av1


def test_reclaims_retired_version_when_no_readers(tmp_path):
    store, pointer, pins, gate = _wire(tmp_path)
    s1, s2, av1 = _publish_v1_then_v2(store, pointer, gate)
    # swap active v1 -> v2 (epoch 0 -> 1), retire v1 at the new epoch
    ok, av2 = pointer.publish(WS, PKG, version="2.0.0", fingerprint=s2.schema_fingerprint,
                              fencing_token=2, expected=av1.expected())
    assert ok and av2.epoch == 1
    gate.retire(WS, PKG, "1.0.0", fingerprint=s1.schema_fingerprint,
                retirement_epoch=av2.epoch, generation=av2.generation)

    assert store.get(PKG, "1.0.0") is not None
    result = gate.reclaim(WS, PKG)                    # no readers pinned
    assert result.reclaimed_versions == ["1.0.0"]
    assert store.get(PKG, "1.0.0") is None, "retired, unreferenced version is freed"
    assert store.get(PKG, "2.0.0") is not None, "active version is untouched"


def test_pin_holds_retired_version_until_reader_leaves(tmp_path):
    store, pointer, pins, gate = _wire(tmp_path)
    s1, s2, av1 = _publish_v1_then_v2(store, pointer, gate)

    # a reader pins the active version (v1, epoch 0) BEFORE the swap
    pinned_epoch = pins.pin(WS, PKG)
    assert pinned_epoch == 0

    ok, av2 = pointer.publish(WS, PKG, version="2.0.0", fingerprint=s2.schema_fingerprint,
                              fencing_token=2, expected=av1.expected())
    assert ok and av2.epoch == 1
    gate.retire(WS, PKG, "1.0.0", fingerprint=s1.schema_fingerprint,
                retirement_epoch=av2.epoch, generation=av2.generation)

    # the reader still holds epoch 0 < retirement_epoch 1 -> v1 is HELD, not freed
    result = gate.reclaim(WS, PKG)
    assert result.min_pinned_epoch == 0
    assert result.held_versions == ["1.0.0"] and result.reclaimed_versions == []
    assert store.get(PKG, "1.0.0") is not None, "a pinned reader gates reclamation"

    # reader leaves -> now safe to reclaim
    pins.unpin(WS, PKG, pinned_epoch)
    result2 = gate.reclaim(WS, PKG)
    assert result2.min_pinned_epoch is None
    assert result2.reclaimed_versions == ["1.0.0"]
    assert store.get(PKG, "1.0.0") is None


def test_newer_reader_does_not_hold_older_retired_version(tmp_path):
    """A reader that pinned AFTER the swap (epoch 1) cannot see v1, so it does
    not gate v1's reclamation — the grace period is epoch-precise, not a
    blanket 'any reader blocks everything'."""
    store, pointer, pins, gate = _wire(tmp_path)
    s1, s2, av1 = _publish_v1_then_v2(store, pointer, gate)
    ok, av2 = pointer.publish(WS, PKG, version="2.0.0", fingerprint=s2.schema_fingerprint,
                              fencing_token=2, expected=av1.expected())
    gate.retire(WS, PKG, "1.0.0", fingerprint=s1.schema_fingerprint,
                retirement_epoch=av2.epoch, generation=av2.generation)

    pinned_epoch = pins.pin(WS, PKG)                  # pins the NEW active, epoch 1
    assert pinned_epoch == 1
    result = gate.reclaim(WS, PKG)
    assert result.min_pinned_epoch == 1               # 1 >= retirement_epoch 1 -> safe
    assert result.reclaimed_versions == ["1.0.0"]
    assert store.get(PKG, "1.0.0") is None
    pins.unpin(WS, PKG, pinned_epoch)


def test_dry_run_reports_without_mutating(tmp_path):
    store, pointer, pins, gate = _wire(tmp_path)
    s1, s2, av1 = _publish_v1_then_v2(store, pointer, gate)
    ok, av2 = pointer.publish(WS, PKG, version="2.0.0", fingerprint=s2.schema_fingerprint,
                              fencing_token=2, expected=av1.expected())
    gate.retire(WS, PKG, "1.0.0", fingerprint=s1.schema_fingerprint,
                retirement_epoch=av2.epoch, generation=av2.generation)
    result = gate.reclaim(WS, PKG, dry_run=True)
    assert result.reclaimed_versions == ["1.0.0"]
    assert store.get(PKG, "1.0.0") is not None, "dry run must not delete"
    assert gate.retired(WS, PKG), "dry run must not forget the retirement record"
