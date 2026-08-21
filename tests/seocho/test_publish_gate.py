"""Publish-time compatibility gate (seocho-ia4.2)."""
from __future__ import annotations
import pytest
from seocho.ontology.core import NodeDef, Ontology, P
from seocho.ontology.snapshot_store import OntologySnapshotStore
from seocho.ontology.publish_gate import PublishCompatibilityError, check_publish_compatibility, derive_drift_policy


def _onto(version, *, nodes):
    return Ontology(name="fin", package_id="fin.pkg", version=version, nodes=nodes,
                    relationships={})


_V1 = {"Company": NodeDef(properties={"name": P(str, unique=True)}),
       "Regulator": NodeDef(properties={"name": P(str, unique=True)})}


def test_check_first_version_allowed():
    r = check_publish_compatibility(None, _onto("1.0.0", nodes=_V1))
    assert r["allowed"] and r["overall"] == "NONE"


def test_backward_compatible_add_optional_allowed():
    prior = _onto("1.0.0", nodes=_V1).to_dict()
    nodes2 = {"Company": NodeDef(properties={"name": P(str, unique=True), "hq": P(str)}),
              "Regulator": NodeDef(properties={"name": P(str, unique=True)})}
    r = check_publish_compatibility(prior, _onto("1.1.0", nodes=nodes2), mode="BACKWARD")
    assert r["allowed"] and r["overall"] == "BACKWARD"
    assert derive_drift_policy(r) == "warn"


def test_breaking_removal_refused_under_backward():
    prior = _onto("1.0.0", nodes=_V1).to_dict()
    nodes2 = {"Company": NodeDef(properties={"name": P(str, unique=True)})}   # dropped Regulator
    r = check_publish_compatibility(prior, _onto("2.0.0", nodes=nodes2), mode="BACKWARD")
    assert not r["allowed"] and r["overall"] == "BREAKING"
    assert derive_drift_policy(r) == "block"


def test_store_publish_gates_breaking(tmp_path):
    store = OntologySnapshotStore(str(tmp_path))
    store.publish(_onto("1.0.0", nodes=_V1))                              # first: ok
    nodes2 = {"Company": NodeDef(properties={"name": P(str, unique=True)})}  # drop Regulator = breaking
    with pytest.raises(PublishCompatibilityError):
        store.publish(_onto("2.0.0", nodes=nodes2), compatibility_mode="BACKWARD")
    # explicit acknowledgment bypasses
    snap, report = store.publish(_onto("2.0.0", nodes=nodes2), allow_breaking=True)
    assert snap.version == "2.0.0" and report["overall"] == "BREAKING"
    # NONE mode never refuses
    snap3, r3 = store.publish(_onto("3.0.0", nodes=_V1), compatibility_mode="NONE")
    assert snap3.version == "3.0.0" and r3["allowed"]
