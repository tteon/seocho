"""Composite entity identity — the cross-document distinguishing point (seocho-uxs)."""
from __future__ import annotations

from seocho import NodeDef, Ontology, P, RelDef
from seocho.index.identity import apply_identity_keys, compute_node_identity


def _metric_ontology() -> Ontology:
    return Ontology(
        name="finder",
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
            "FinancialMetric": NodeDef(
                properties={
                    "name": P(str, unique=True),
                    "company": P(str),
                    "year": P(str),
                    "value": P(str),
                },
                identity_keys=["name", "company", "year"],
            ),
        },
        relationships={"REPORTED": RelDef(source="Company", target="FinancialMetric")},
    )


# --- compute_node_identity ---------------------------------------------------

def test_compute_identity_distinguishes_homonyms():
    keys = ["name", "company", "year"]
    ptc = compute_node_identity("FinancialMetric", {"name": "Total revenue", "company": "PTC", "year": "2023"}, keys)
    tsla = compute_node_identity("FinancialMetric", {"name": "Total revenue", "company": "Tesla", "year": "2023"}, keys)
    assert ptc != tsla
    assert ptc == "financialmetric|total revenue|ptc|2023"


def test_compute_identity_is_normalized_and_stable():
    keys = ["name", "year"]
    a = compute_node_identity("M", {"name": "  Total   Revenue ", "year": "2023"}, keys)
    b = compute_node_identity("M", {"name": "total revenue", "year": "2023"}, keys)
    assert a == b  # whitespace + case normalized


def test_compute_identity_none_when_no_keys_or_all_empty():
    assert compute_node_identity("M", {"name": "x"}, []) is None
    assert compute_node_identity("M", {"name": "", "year": ""}, ["name", "year"]) is None


# --- apply_identity_keys -----------------------------------------------------

def test_apply_rewrites_ids_and_remaps_relationships():
    onto = _metric_ontology()
    nodes = [
        {"id": "c_ptc", "label": "Company", "properties": {"name": "PTC"}},
        {"id": "m1", "label": "FinancialMetric",
         "properties": {"name": "Total revenue", "company": "PTC", "year": "2023", "value": "2.1B"}},
    ]
    rels = [{"source": "c_ptc", "target": "m1", "type": "REPORTED", "properties": {}}]
    apply_identity_keys(onto, nodes, rels)

    metric = next(n for n in nodes if n["label"] == "FinancialMetric")
    assert metric["id"] == "financialmetric|total revenue|ptc|2023"
    assert metric["properties"]["id"] == metric["id"]
    # Company has no identity_keys -> id untouched; rel endpoint remapped.
    assert nodes[0]["id"] == "c_ptc"
    assert rels[0]["source"] == "c_ptc"
    assert rels[0]["target"] == "financialmetric|total revenue|ptc|2023"


def test_apply_is_noop_without_identity_keys():
    onto = Ontology(
        name="plain",
        nodes={"Company": NodeDef(properties={"name": P(str, unique=True)})},
        relationships={},
    )
    nodes = [{"id": "c1", "label": "Company", "properties": {"name": "ACME"}}]
    apply_identity_keys(onto, nodes, [])
    assert nodes[0]["id"] == "c1"


def test_two_mentions_of_same_entity_fold_to_one_id():
    onto = _metric_ontology()
    nodes = [
        {"id": "x1", "label": "FinancialMetric",
         "properties": {"name": "Total revenue", "company": "PTC", "year": "2023"}},
        {"id": "x2", "label": "FinancialMetric",
         "properties": {"name": "Total Revenue", "company": "ptc", "year": "2023"}},
    ]
    apply_identity_keys(onto, nodes, [])
    assert nodes[0]["id"] == nodes[1]["id"]  # same entity -> same id (store MERGE folds)


# --- ontology serialization + constraints ------------------------------------

def test_identity_keys_round_trip_to_dict():
    onto = _metric_ontology()
    restored = Ontology.from_dict(onto.to_dict())
    assert restored.nodes["FinancialMetric"].identity_keys == ["name", "company", "year"]
    assert restored.nodes["Company"].identity_keys == []


def test_composite_uniqueness_constraint_replaces_member_unique():
    onto = _metric_ontology()
    stmts = onto.to_cypher_constraints()
    joined = "\n".join(stmts)
    # one composite constraint over the identity tuple
    assert "REQUIRE (n.name, n.company, n.year) IS UNIQUE" in joined
    # the per-member UNIQUE on name is NOT emitted for the identity-keyed label
    assert "constraint_FinancialMetric_name_unique" not in joined
    # Company (no identity_keys) keeps its plain name uniqueness
    assert "constraint_Company_name_unique" in joined


def test_effective_identity_keys_falls_back_to_single_unique():
    nd = NodeDef(properties={"name": P(str, unique=True), "v": P(str)})
    assert nd.effective_identity_keys == ["name"]
    nd2 = NodeDef(properties={"name": P(str, unique=True)}, identity_keys=["name", "year"])
    assert nd2.effective_identity_keys == ["name", "year"]


def test_merge_preserves_identity_keys():
    left = _metric_ontology()
    right = Ontology(
        name="finder",
        nodes={"FinancialMetric": NodeDef(properties={"name": P(str, unique=True)})},
        relationships={},
    )
    merged = left.merge(right)
    assert merged.nodes["FinancialMetric"].identity_keys == ["name", "company", "year"]
