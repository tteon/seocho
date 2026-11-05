"""Offline, hash-pinned governance for RDF ontology bundles."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict

from .core import Ontology
from .governance import reason_consistency, validate_rdf_with_pyshacl

_ARTIFACTS = ("ontology.jsonld", "ontology.ttl", "shapes.ttl")


@dataclass(frozen=True)
class RdfGovernanceReceipt:
    """A promotable offline validation receipt tied to one immutable bundle."""

    schema_version: str
    bundle_sha256: str
    data_graph_sha256: str
    shacl: Dict[str, Any]
    owl_consistency: Dict[str, Any]
    promotable: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def verify_rdf_ontology_bundle(bundle_dir: str | Path) -> Dict[str, Any]:
    """Verify all artifact and aggregate hashes before offline governance."""
    directory = Path(bundle_dir)
    manifest_path = directory / "manifest.json"
    try:
        with manifest_path.open("r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid RDF ontology bundle manifest: {exc}") from exc
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != set(_ARTIFACTS):
        raise ValueError("RDF ontology bundle manifest has an invalid artifact set")
    actual: Dict[str, str] = {}
    for name in _ARTIFACTS:
        expected = files.get(name)
        if not isinstance(expected, str) or len(expected) != 64:
            raise ValueError(f"RDF ontology bundle has invalid hash for {name}")
        path = directory / name
        actual[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual[name] != expected:
            raise ValueError(f"RDF ontology bundle digest mismatch for {name}")
    aggregate = hashlib.sha256(
        json.dumps(actual, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if aggregate != manifest.get("bundle_sha256"):
        raise ValueError("RDF ontology bundle aggregate digest mismatch")
    return manifest


def run_rdf_governance(
    bundle_dir: str | Path,
    data_graph: str | Path,
    *,
    data_format: str = "turtle",
    run_reasoner: bool = False,
) -> RdfGovernanceReceipt:
    """Validate RDF data offline and return a receipt safe to promote.

    Oxigraph is intentionally not called here: this is the offline governance
    boundary, whereas Oxigraph remains a read-only vocabulary model. Pellet is
    used only for ontology consistency, never to mutate graph facts.
    """
    manifest = verify_rdf_ontology_bundle(bundle_dir)
    directory = Path(bundle_dir)
    data_path = Path(data_graph)
    data_digest = hashlib.sha256(data_path.read_bytes()).hexdigest()
    shacl = validate_rdf_with_pyshacl(
        data_path, directory / "shapes.ttl", data_format=data_format
    ).to_dict()
    ontology = Ontology.from_jsonld(directory / "ontology.jsonld")
    consistency = (
        reason_consistency(ontology)
        if run_reasoner
        else {"consistent": None, "available": False, "reasoner": None,
              "error": "reasoner skipped (run_reasoner=False)", "unsatisfiable_classes": []}
    )
    # A missing optional reasoner is not a failure; a reasoner that proves an
    # inconsistency is. pySHACL itself must have actually run and conformed.
    promotable = bool(shacl.get("available") and shacl.get("ok")) and consistency.get("consistent") is not False
    return RdfGovernanceReceipt(
        schema_version="seocho.rdf_governance_receipt.v1",
        bundle_sha256=str(manifest["bundle_sha256"]),
        data_graph_sha256=data_digest,
        shacl=shacl,
        owl_consistency=consistency,
        promotable=promotable,
    )


def write_rdf_governance_receipt(receipt: RdfGovernanceReceipt, output: str | Path) -> Path:
    """Persist a receipt without ever overwriting an ontology artifact."""
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
