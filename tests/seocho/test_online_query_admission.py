from __future__ import annotations

import pytest

from seocho.ontology import NodeDef, Ontology, P, RelDef
from seocho.ontology.lifecycle import OntologyLifecycleStore, build_bundle_atomically
from seocho.ontology.online_query_admission import admit_online_query


def _ontology() -> Ontology:
    return Ontology(
        name="events",
        version="1.0.0",
        nodes={
            "ExchangeIntent": NodeDef(properties={"id": P(str), "workspace": P(str)}),
            "ExchangeMemoryEvent": NodeDef(
                properties={"step": P(str), "workspace": P(str)}
            ),
        },
        relationships={
            "HAS_EVENT": RelDef(source="ExchangeIntent", target="ExchangeMemoryEvent")
        },
    )


def test_online_query_admission_requires_active_matching_bundle_and_releases_lease(
    tmp_path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    built = build_bundle_atomically(_ontology(), bundle_dir)
    state_db = tmp_path / "state.sqlite"
    store = OntologyLifecycleStore(state_db)
    assert store.activate("ws", "events", bundle_dir, fencing_token=1)[0]

    with admit_online_query(
        bundle_dir=bundle_dir,
        state_db=state_db,
        workspace_id="ws",
        package_id="events",
        owner="test-owner",
    ) as admitted:
        receipt = admitted.receipt()
        assert receipt["rdf_bundle_sha256"] == built["bundle_sha256"]
        assert receipt["agent_profile_sha256"]
        assert receipt["lease_id"]
        assert receipt["module_quality_disposition"] in {"ready", "needs_reasoning"}
    assert store.status("ws", "events")["live_leases"] == []


def test_online_query_admission_rejects_non_active_bundle(tmp_path) -> None:
    bundle_dir = tmp_path / "bundle"
    build_bundle_atomically(_ontology(), bundle_dir)
    with pytest.raises(ValueError, match="not the active"):
        with admit_online_query(
            bundle_dir=bundle_dir,
            state_db=tmp_path / "state.sqlite",
            workspace_id="ws",
            package_id="events",
            owner="test-owner",
        ):
            pass
