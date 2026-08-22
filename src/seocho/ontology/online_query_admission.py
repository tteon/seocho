"""Admission boundary for online ontology-governed query work.

An agent must consume a verified *active* query profile, not an arbitrary path
or a candidate ontology.  The lease is deliberately short-lived and its public
receipt carries identities only; filesystem paths and prompt content never cross
this boundary.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .lifecycle import OntologyLifecycleStore, load_agent_profile, verify_rdf_bundle


@dataclass(frozen=True)
class OnlineQueryAdmission:
    profile: dict[str, Any]
    admission: dict[str, Any]

    def receipt(self) -> dict[str, Any]:
        quality = self.profile.get("module_quality", {})
        return {
            "schema_version": "seocho.online_query_admission.v1",
            "rdf_bundle_sha256": self.profile["canonical_bundle_sha256"],
            "agent_profile_sha256": self.profile["profile_sha256"],
            "module_quality_disposition": quality.get("decision", {}).get(
                "disposition"
            ),
            **self.admission,
        }


@contextmanager
def admit_online_query(
    *,
    bundle_dir: str | Path,
    state_db: str | Path,
    workspace_id: str,
    package_id: str,
    owner: str,
    ttl_seconds: int = 60,
) -> Iterator[OnlineQueryAdmission]:
    """Yield a verified active query profile and release its lease afterwards."""
    checked = verify_rdf_bundle(bundle_dir)
    if not checked.get("ok"):
        raise ValueError("online query admission requires a verified RDF bundle")
    if checked.get("ontology", {}).get("package_id") != package_id:
        raise ValueError("bundle package_id does not match query admission package")
    profile = load_agent_profile(bundle_dir, "query")
    decision = profile.get("module_quality", {}).get("decision", {})
    if decision.get("disposition") == "reject":
        raise ValueError("query profile rejected by module-quality policy")
    store = OntologyLifecycleStore(state_db)
    active = store.status(workspace_id, package_id).get("active")
    if not active or active.get("fingerprint") != checked.get("bundle_sha256"):
        raise ValueError("query bundle is not the active ontology revision")
    lease = store.acquire(
        workspace_id, package_id, purpose="query", owner=owner, ttl_seconds=ttl_seconds
    )
    try:
        admission = store.admission(lease.lease_id)
        if admission["fingerprint"] != profile["canonical_bundle_sha256"]:
            raise ValueError("lease fingerprint does not match query profile")
        yield OnlineQueryAdmission(profile=profile, admission=admission)
    finally:
        store.release(lease.lease_id, owner=owner)
