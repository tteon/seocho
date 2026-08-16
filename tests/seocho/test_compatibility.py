"""Typed ontology-change compatibility classifier (seocho-ia4.2) — fixes the
diff_ontologies false-major (add-optional flagged breaking)."""

from __future__ import annotations

from seocho.ontology.compatibility import classify_ontology_change, semver_distance

_BASE = {"nodes": {"Company": {"properties": {"name": {"type": "STRING", "constraint": "UNIQUE"},
                                              "hq": {"type": "STRING"}}},
                   "Team": {"properties": {"name": {"type": "STRING"}}}},
         "relationships": {"OWNS": {"source": "Company", "target": "Team", "cardinality": "MANY_TO_MANY"}}}


def _new(**edits):
    import json
    d = json.loads(json.dumps(_BASE))
    edits.get("mutate", lambda x: None)(d)
    return d


def test_add_optional_is_backward_not_breaking():
    d = _new(mutate=lambda d: d["nodes"]["Team"]["properties"].__setitem__("slug", {"type": "STRING"}))
    r = classify_ontology_change(_BASE, d)
    assert r.overall == "BACKWARD"
    assert "Team" not in r.breaking_labels          # the false-major fix
    assert not r.is_breaking


def test_add_required_is_breaking():
    d = _new(mutate=lambda d: d["nodes"]["Company"]["properties"].__setitem__(
        "owner_id", {"type": "STRING", "constraint": "REQUIRED"}))
    r = classify_ontology_change(_BASE, d)
    assert r.is_breaking and ("Company", "owner_id") in r.breaking_properties


def test_retype_is_breaking():
    d = _new(mutate=lambda d: d["nodes"]["Company"]["properties"].__setitem__("hq", {"type": "INTEGER"}))
    r = classify_ontology_change(_BASE, d)
    assert r.is_breaking and ("Company", "hq") in r.breaking_properties


def test_remove_prop_invalidates():
    d = _new(mutate=lambda d: d["nodes"]["Company"]["properties"].pop("hq"))
    r = classify_ontology_change(_BASE, d)
    assert ("Company", "hq") in r.breaking_properties


def test_node_add_is_backward():
    d = _new(mutate=lambda d: d["nodes"].__setitem__("Vendor", {"properties": {"name": {"type": "STRING"}}}))
    r = classify_ontology_change(_BASE, d)
    assert r.overall == "BACKWARD" and "Vendor" not in r.breaking_labels


def test_cardinality_tighten_is_breaking_loosen_is_backward():
    tight = _new(mutate=lambda d: d["relationships"]["OWNS"].__setitem__("cardinality", "ONE_TO_ONE"))
    assert classify_ontology_change(_BASE, tight).is_breaking
    loose = _new(mutate=lambda d: d["relationships"]["OWNS"].__setitem__("cardinality", "MANY_TO_MANY"))
    assert classify_ontology_change(_BASE, loose).overall == "NONE"  # unchanged


def test_node_removed_is_breaking():
    d = _new(mutate=lambda d: d["nodes"].pop("Team"))
    r = classify_ontology_change(_BASE, d)
    assert r.is_breaking and "Team" in r.breaking_labels


def test_semver_distance():
    assert semver_distance("1.0.0", "1.0.0") == 0
    assert semver_distance("1.0.0", "1.0.5") == 1     # patch only
    assert semver_distance("1.2.0", "1.5.0") == 3     # minor delta
    assert semver_distance("1.0.0", "3.0.0") == 2000  # major delta dominates
