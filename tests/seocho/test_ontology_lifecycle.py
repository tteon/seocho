from __future__ import annotations

from seocho.ontology.active_pointer import ActiveOntologyPointer
from seocho.ontology import NodeDef, Ontology, Property, build_rdf_ontology_bundle
from seocho.ontology.lifecycle import OntologyLifecycleStore, load_agent_profile, slice_agent_profile


def _manifest(bundle, package: str = "demo", version: str = "1.0.0") -> None:
    import hashlib
    import json

    bundle.mkdir()
    for name, content in {"ontology.jsonld": "{}", "ontology.ttl": "", "shapes.ttl": ""}.items():
        (bundle / name).write_text(content, encoding="utf-8")
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in bundle.iterdir()}
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    (bundle / "manifest.json").write_text(json.dumps({"ontology": {"package_id": package, "version": version}, "files": files, "bundle_sha256": digest}), encoding="utf-8")


def test_lifecycle_activate_and_exclusive_lease(tmp_path):
    bundle = tmp_path / "bundle"; _manifest(bundle)
    store = OntologyLifecycleStore(tmp_path / "state.sqlite")
    ok, active = store.activate("ws", "demo", bundle, fencing_token=1)
    assert ok and active and active.epoch == 0
    lease = store.acquire("ws", "demo", purpose="projection", owner="pid:1", ttl_seconds=30)
    assert store.status("ws", "demo")["live_leases"][0]["lease_id"] == lease.lease_id
    try:
        store.acquire("ws", "demo", purpose="projection", owner="pid:2", ttl_seconds=30)
    except ValueError as exc:
        assert "live lease" in str(exc)
    else:
        raise AssertionError("exclusive lease unexpectedly acquired")
    assert store.release(lease.lease_id, owner="pid:1")


def test_activation_requires_pointer_cas_and_valid_manifest(tmp_path):
    bundle = tmp_path / "bundle"; _manifest(bundle)
    store = OntologyLifecycleStore(tmp_path / "state.sqlite")
    assert store.activate("ws", "demo", bundle, fencing_token=1)[0]
    assert not store.activate("ws", "demo", bundle, fencing_token=2)[0]
    assert store.activate("ws", "demo", bundle, fencing_token=2, expected=(0, 0))[0]
    assert ActiveOntologyPointer(tmp_path / "state.sqlite").read("ws", "demo").epoch == 1


def test_admission_is_minimal_and_requires_a_live_lease(tmp_path):
    bundle = tmp_path / "bundle"; _manifest(bundle)
    store = OntologyLifecycleStore(tmp_path / "state.sqlite")
    assert store.activate("ws", "demo", bundle, fencing_token=1)[0]
    lease = store.acquire("ws", "demo", purpose="projection", owner="pid:1", ttl_seconds=30)
    assert store.admission(lease.lease_id) == {
        "lease_id": lease.lease_id,
        "fingerprint": lease.fingerprint,
        "generation": 0,
        "epoch": 0,
        "fencing_token": lease.fencing_token,
    }


def test_verified_profile_and_jit_slice_are_bounded(tmp_path):
    ontology = Ontology(name="people", namespace="https://example.test/people#", version="1.0.0", nodes={"Person": NodeDef(properties={"name": Property(str, required=True)})})
    bundle = build_rdf_ontology_bundle(ontology, tmp_path / "bundle")
    profile = load_agent_profile(bundle.directory, "query")
    assert profile["purpose"] == "query"
    sliced = slice_agent_profile(bundle.directory, "query", ["Person"], max_chars=4000)
    assert sliced["canonical_bundle_sha256"] == bundle.digest
    assert "Person" in sliced["allowed_node_labels"]
