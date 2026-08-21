"""A relationship endpoint that references a sequential id survives normalization.

The measured symptom was "47 relationships extracted, 0 domain edges persisted",
non-deterministically. Root cause, pinned by per-stage survival census: when a
model emits sequential node ids (`"1"`, `"2"`) plus names, `_normalize_node`
replaces the sequential id with the entity name (cross-document collision
avoidance). But the relationship endpoints still reference the *original*
sequential id, and the node lookup was built only from the new id / name / uri --
never the original. So `source: "2"` resolved to nothing, the edge orphaned, and
it was silently dropped before the write.

These tests lock the endpoint remap deterministically: no LLM, no graph, no live
cost -- just the payload contract that `normalize_payload` must honor.
"""

from __future__ import annotations

from seocho.index.extraction_engine import CanonicalExtractionEngine


def _engine() -> CanonicalExtractionEngine:
    # normalize_payload never touches the llm; a bare object is enough.
    return CanonicalExtractionEngine(ontology=None, llm=object())


def test_sequential_id_endpoints_resolve_to_name_ids():
    """The exact live shape: integer ids + names, edge by integer id."""
    payload = {
        "nodes": [
            {"id": "1", "label": "Entity", "properties": {"name": "Cornwall"}},
            {"id": "2", "label": "Entity", "properties": {"name": "Goonhilly Down"}},
        ],
        "relationships": [
            {"source": "2", "target": "1", "type": "RELATED_TO", "properties": {}},
        ],
    }
    out = _engine().normalize_payload(payload)
    node_ids = {n["id"] for n in out["nodes"]}
    assert len(out["relationships"]) == 1, "the edge must survive normalization"
    rel = out["relationships"][0]
    assert rel["source"] in node_ids and rel["target"] in node_ids, (
        "both endpoints must resolve to real node ids, not orphaned integers"
    )
    # And specifically to the name-based ids the node normalization produced.
    assert rel["source"] == "goonhilly_down" and rel["target"] == "cornwall"


def test_name_referenced_endpoints_still_resolve():
    """A model that references endpoints by name (not id) keeps working."""
    payload = {
        "nodes": [
            {"id": "1", "label": "Entity", "properties": {"name": "Cornwall"}},
            {"id": "2", "label": "Entity", "properties": {"name": "Goonhilly Down"}},
        ],
        "relationships": [
            {"source": "Goonhilly Down", "target": "Cornwall", "type": "RELATED_TO"},
        ],
    }
    out = _engine().normalize_payload(payload)
    rel = out["relationships"][0]
    node_ids = {n["id"] for n in out["nodes"]}
    assert rel["source"] in node_ids and rel["target"] in node_ids


def test_unknown_endpoint_is_left_untouched_not_invented():
    """An endpoint referencing no node is passed through, not silently mapped."""
    payload = {
        "nodes": [{"id": "1", "label": "Entity", "properties": {"name": "Cornwall"}}],
        "relationships": [
            {"source": "1", "target": "99", "type": "RELATED_TO"},
        ],
    }
    out = _engine().normalize_payload(payload)
    rel = out["relationships"][0]
    assert rel["source"] == "cornwall", "the known endpoint resolves"
    assert rel["target"] == "99", "an unknown endpoint is not fabricated"
