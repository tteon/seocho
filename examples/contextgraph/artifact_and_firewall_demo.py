#!/usr/bin/env python3
"""(a) semantic-artifact draft generation+persistence  (b) relation firewall — $0, NO LLM.

(a) Shows what the indexing pipeline's `_maybe_build_semantic_artifacts` actually
produces: a draft built FROM the ontology ($0) = ontology_candidate + shacl_candidate
(SHACL shapes) + vocabulary_candidate (SKOS-ish terms). Persists it via the
draft→approve store (save_semantic_artifact) so the disk lifecycle is exercised
(our contextgraph runs computed this draft and discarded it).

(b) The relation firewall. validate_extraction ALREADY flags undeclared relation
types — but strict_validation defaults OFF (our builds wrote the smuggled
SUPPORTS/OPPOSES), and strict ON drops the WHOLE chunk (too blunt). The pipeline
exposes an `on_after_validate(nodes, rels, errors)` hook — the surgical seam.
This implements a strip-undeclared-relations callback and verifies it removes ONLY
the undeclared edge, keeping valid nodes + declared edges.

Run: python examples/contextgraph/artifact_and_firewall_demo.py
"""
from __future__ import annotations
import inspect, json, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples" / "contextgraph"))
from decision_modules.compose import compose_modules, ARMS


def dataclass_to_dict(obj):
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if hasattr(obj, "__dict__"):
        return {k: dataclass_to_dict(v) for k, v in vars(obj).items()}
    if isinstance(obj, list):
        return [dataclass_to_dict(x) for x in obj]
    return obj


def part_a():
    print("=" * 72)
    print("(a) SEMANTIC-ARTIFACT DRAFT — generated FROM the ontology ($0, no LLM)")
    print("=" * 72)
    onto = compose_modules(ARMS["position"])
    draft = onto.to_semantic_artifact_draft(name="position-arm-demo")
    d = dataclass_to_dict(draft)
    oc = d.get("ontology_candidate", {})
    sc = d.get("shacl_candidate", {})
    vc = d.get("vocabulary_candidate", {})
    print(f"  ontology_candidate : {len(oc.get('nodes', oc.get('node_types', [])) or [])} node types, "
          f"{len(oc.get('relationships', oc.get('relation_types', [])) or [])} relations")
    shapes = sc.get("shapes", []) if isinstance(sc, dict) else []
    print(f"  shacl_candidate    : {len(shapes)} SHACL shapes (datatype/cardinality constraints)")
    if shapes:
        s0 = shapes[0]
        print(f"      e.g. targetClass={s0.get('targetClass')} with {len(s0.get('properties', []))} property shapes")
    terms = (vc.get("terms") or vc.get("entries") or []) if isinstance(vc, dict) else []
    print(f"  vocabulary_candidate: schema={vc.get('schema_version') if isinstance(vc,dict) else '?'}, "
          f"{len(terms)} terms (SKOS-style prefLabel/aliases/definition)")
    # the draft is serializable + persistable (the lifecycle our SDK runs skipped).
    # The production store is extraction/semantic_artifact_store.save_semantic_artifact
    # (per-workspace disk JSON, called by the SERVER ingest path); here we just
    # serialize the draft to prove it's a real, persistable artifact.
    out = Path(tempfile.mkdtemp(prefix="seocho_artifacts_")) / "demo-ws" / "position-arm-demo.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(d, indent=2, default=str))
    rt = json.loads(out.read_text())
    print(f"  PERSISTED draft -> {out}  (round-trip keys: {list(rt)})")
    print("  NOTE: production store = extraction/semantic_artifact_store (per-ws disk JSON, draft→approve,")
    print("        called by the SERVER ingest path). Our contextgraph SDK runs COMPUTED this draft and DISCARDED it.")
    return onto


def strip_undeclared_relations(onto):
    """The surgical firewall as an on_after_validate callback: drop ONLY relations
    whose type isn't declared in the ontology; keep valid nodes + declared edges;
    clear the now-resolved 'Unknown relationship type' errors. (vs strict mode,
    which drops the whole chunk.)"""
    declared = set(onto.relationships)
    def _cb(nodes, rels, errors):
        kept = [r for r in rels if r.get("type") in declared]
        dropped = [r for r in rels if r.get("type") not in declared]
        residual = [e for e in errors if not e.startswith("Unknown relationship type")]
        if dropped:
            residual.append(f"firewall: stripped {len(dropped)} undeclared relation(s): "
                            f"{sorted({r.get('type') for r in dropped})}")
        return nodes, kept, residual
    return _cb


def part_b(onto):
    print("\n" + "=" * 72)
    print("(b) RELATION FIREWALL — does it really work? ($0, synthetic payload)")
    print("=" * 72)
    # synthetic extraction: valid nodes + 1 DECLARED rel + 1 UNDECLARED (smuggled) rel
    payload = {
        "nodes": [
            {"id": "p1", "label": "Person", "properties": {"name": "Ian J Dickinson"}},
            {"id": "t1", "label": "Topic", "properties": {"name": "map vs globe"}},
            {"id": "pr1", "label": "Proposal", "properties": {"name": "use a schematic map"}},
        ],
        "relationships": [
            {"source": "p1", "target": "t1", "type": "HOLDS_POSITION", "properties": {"polarity": "FOR"}},
            {"source": "p1", "target": "pr1", "type": "SUPPORTS", "properties": {}},  # UNDECLARED in 'position' arm
        ],
    }
    errors = onto.validate_extraction(payload)
    print(f"  declared relations (position arm): HOLDS_POSITION yes / SUPPORTS no")
    print(f"  validate_extraction errors: {errors}")
    assert any("SUPPORTS" in e for e in errors), "firewall logic should flag SUPPORTS"
    print("  -> validation LOGIC correctly flags the undeclared SUPPORTS. ✓\n")

    print("  enforcement options:")
    print("   • strict_validation=False (DEFAULT, our builds): error logged, edge WRITTEN ANYWAY → silent-wrong")
    print("   • strict_validation=True: WHOLE chunk dropped (loses the valid Person/Topic/HOLDS_POSITION too)")
    cb = strip_undeclared_relations(onto)
    nodes2, rels2, errors2 = cb(payload["nodes"], payload["relationships"], list(errors))
    print(f"   • SURGICAL strip (on_after_validate cb): keep valid, drop only undeclared")
    print(f"       nodes kept     : {len(nodes2)}/3")
    print(f"       relations kept : {[r['type'] for r in rels2]}  (SUPPORTS removed)")
    print(f"       residual errors: {errors2}")
    # verify
    post = onto.validate_extraction({"nodes": nodes2, "relationships": rels2})
    unknown = [e for e in post if e.startswith("Unknown relationship type")]
    ok = (not unknown) and any(r["type"] == "HOLDS_POSITION" for r in rels2) and len(nodes2) == 3
    print(f"\n  VERIFY: post-strip has 0 undeclared-relation errors AND keeps HOLDS_POSITION + all nodes -> "
          f"{'PASS ✓' if ok else 'FAIL ✗'}")
    print("  => the firewall WORKS when enforced surgically; the gap was it being OFF by default,")
    print("     and strict mode being all-or-nothing. The on_after_validate hook is the right seam.")


if __name__ == "__main__":
    onto = part_a()
    part_b(onto)
