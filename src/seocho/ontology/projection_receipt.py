"""Hash-pinned admission receipt for canonical Rust graph projection."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


class ProjectionReceiptError(ValueError):
    """A governance/profile receipt is not safe to project canonically."""


def load_projection_receipt_from_env() -> dict[str, Any] | None:
    """Load the optional offline-governance receipt without exposing raw RDF.

    ``SEOCHO_RDF_GOVERNANCE_RECEIPT`` points to a JSON receipt emitted by
    ``seocho ontology rdf-governance``. ``SEOCHO_AGENT_ONTOLOGY_PROFILE``
    points to one purpose-specific profile from the same immutable bundle.
    Both are required together when either is configured.
    """
    receipt_path = os.getenv("SEOCHO_RDF_GOVERNANCE_RECEIPT", "").strip()
    profile_path = os.getenv("SEOCHO_AGENT_ONTOLOGY_PROFILE", "").strip()
    if not receipt_path and not profile_path:
        return None
    if not receipt_path or not profile_path:
        raise ProjectionReceiptError(
            "SEOCHO_RDF_GOVERNANCE_RECEIPT and SEOCHO_AGENT_ONTOLOGY_PROFILE must be set together"
        )
    try:
        governance = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
        profile = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectionReceiptError(f"invalid projection receipt files: {exc}") from exc
    return validate_projection_receipt(governance, profile)


def validate_projection_receipt(
    governance: Mapping[str, Any], profile: Mapping[str, Any]
) -> dict[str, Any]:
    """Return a minimal transport receipt after cross-artifact validation."""
    if governance.get("schema_version") != "seocho.rdf_governance_receipt.v1":
        raise ProjectionReceiptError("unsupported RDF governance receipt schema")
    if profile.get("schema_version") != "seocho.agent_ontology_profile.v1":
        raise ProjectionReceiptError("unsupported agent ontology profile schema")
    bundle = str(governance.get("bundle_sha256", ""))
    data_graph = str(governance.get("data_graph_sha256", ""))
    profile_hash = str(profile.get("profile_sha256", ""))
    if not governance.get("promotable"):
        raise ProjectionReceiptError("RDF governance receipt is not promotable")
    if not all(
        len(value) == 64 and all(char in "0123456789abcdef" for char in value.lower())
        for value in (bundle, data_graph, profile_hash)
    ):
        raise ProjectionReceiptError("projection receipt hashes must be SHA-256 digests")
    if profile.get("canonical_bundle_sha256") != bundle:
        raise ProjectionReceiptError("agent profile is not derived from the governed RDF bundle")
    if profile.get("purpose") != "projection":
        raise ProjectionReceiptError("canonical projection requires the projection agent profile")
    canonical_profile = dict(profile)
    canonical_profile.pop("profile_sha256", None)
    computed_profile_hash = hashlib.sha256(
        json.dumps(canonical_profile, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if profile_hash != computed_profile_hash:
        raise ProjectionReceiptError("agent profile hash does not match its contents")
    payload = {
        "schema_version": "seocho.canonical_projection_receipt.v1",
        "rdf_bundle_sha256": bundle,
        "rdf_data_graph_sha256": data_graph,
        "agent_profile_sha256": profile_hash,
        "agent_profile_purpose": str(profile.get("purpose", "")),
        "ontology_context_hash": str(profile.get("ontology_context_hash", "")),
    }
    payload["projection_receipt_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload
