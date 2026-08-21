"""Offline candidate RDF staging before a governed canonical projection."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import quote

from .lifecycle import OntologyLifecycleStore, load_agent_profile
from .projection_receipt import validate_projection_receipt
from .rdf_governance import run_rdf_governance, write_rdf_governance_receipt


def graph_payload_to_turtle(nodes: Sequence[Mapping[str, Any]], relationships: Sequence[Mapping[str, Any]]) -> str:
    """Create deterministic RDF for the approved LPG projection payload."""
    lines = ["@prefix seocho: <https://seocho.dev/ontology/> .", ""]
    ids = {str(n.get("id", "")): f"<urn:seocho:candidate:{quote(str(n.get('id', '')), safe='')}>" for n in nodes}
    for node in sorted(nodes, key=lambda item: str(item.get("id", ""))):
        subject = ids[str(node.get("id", ""))]
        triples = [f"a seocho:{str(node.get('label', 'Entity'))}"]
        for key, value in sorted(dict(node.get("properties") or {}).items()):
            if not str(key).startswith("_") and not isinstance(value, (dict, list)):
                triples.append(f"seocho:{key} {json.dumps(str(value), ensure_ascii=False)}")
        lines.append(subject + " " + " ;\n  ".join(triples) + " .")
    for rel in sorted(relationships, key=lambda i: (str(i.get("source", "")), str(i.get("type", "")), str(i.get("target", "")))):
        source, target = ids.get(str(rel.get("source", ""))), ids.get(str(rel.get("target", "")))
        if source and target:
            lines.append(f"{source} seocho:{str(rel.get('type', 'RELATED_TO'))} {target} .")
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class StagedCandidate:
    semantic_receipt: dict[str, Any]
    admission: dict[str, Any]
    data_graph_sha256: str
    artifact_dir: str


class GovernedCandidateStager:
    """Creates immutable source-specific candidate+receipt artifacts offline."""

    def __init__(self, *, bundle_dir: str | Path, state_db: str | Path, lease_id: str, artifact_root: str | Path) -> None:
        self.bundle_dir, self.store, self.lease_id = Path(bundle_dir), OntologyLifecycleStore(state_db), lease_id
        self.artifact_root = Path(artifact_root)

    def stage(self, nodes: Sequence[Mapping[str, Any]], relationships: Sequence[Mapping[str, Any]]) -> StagedCandidate:
        from ..metrics import get_metrics
        from ..tracing import start_span
        started, outcome = time.perf_counter(), "ok"
        turtle = graph_payload_to_turtle(nodes, relationships)
        digest = hashlib.sha256(turtle.encode("utf-8")).hexdigest()
        with start_span("governance.candidate.stage", input_data={"nodes": len(nodes), "relationships": len(relationships), "payload_bytes": len(turtle.encode())}, metadata={"candidate.data_graph_sha256": digest}, tags=["governance", "candidate-stage"]) as span:
            try:
                directory = self.artifact_root / digest
                directory.mkdir(parents=True, exist_ok=True)
                data_path, receipt_path = directory / "candidate.ttl", directory / "governance-receipt.json"
                if not data_path.exists(): data_path.write_text(turtle, encoding="utf-8")
                receipt = run_rdf_governance(self.bundle_dir, data_path)
                if not receipt.promotable: raise ValueError("candidate RDF is not promotable")
                if not receipt_path.exists(): write_rdf_governance_receipt(receipt, receipt_path)
                semantic = validate_projection_receipt(receipt.to_dict(), load_agent_profile(self.bundle_dir, "projection"))
                span.set_output({"promotable": True, "data_graph_sha256": digest})
                return StagedCandidate(semantic, self.store.admission(self.lease_id), digest, str(directory))
            except Exception:
                outcome = "rejected"
                raise
            finally:
                elapsed = time.perf_counter() - started
                span.set_metadata({"outcome": outcome, "duration_ms": round(elapsed * 1000, 2)})
                metrics = get_metrics()
                metrics.record("seocho.governance.candidate.stage.duration", elapsed, {"outcome": outcome})
                metrics.add("seocho.governance.candidate.stage.count", attributes={"outcome": outcome})
                metrics.record("seocho.governance.candidate.payload_bytes", len(turtle.encode()))
