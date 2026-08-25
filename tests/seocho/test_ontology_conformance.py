"""Conformance catches drift in both directions, and expands endpoint unions.

The measured incident this guards: five real, fully-populated graph properties were
undeclared, so the validator rejected correct queries (unknown_properties) while the
ontology-derived grammar made the same questions unrepresentable — the repair loop burned
generations on something no repair could fix. And the check's own first version treated
`Person|Company` as a literal label, reporting four real relationships as never occurring;
the union-expansion test keeps that fixed.
"""

from __future__ import annotations

from seocho.ontology import Ontology
from seocho.ontology.conformance import conformance_report


def _fake_graph(run_map):
    def run_query(cypher: str):
        for key, rows in run_map.items():
            if key in cypher:
                return rows
        return []
    return run_query


ONTO = Ontology.from_dict({
    "name": "t",
    "nodes": {
        "Account": {"identity_keys": ["acct_no"],
                    "properties": {"acct_no": {"type": "INTEGER"},
                                   "iban": {"type": "STRING"}}},
        "Person": {"properties": {"id": {"type": "STRING"}}},
        "Company": {"properties": {"id": {"type": "STRING"}}},
    },
    "relationships": {
        "OWN": {"source": "Person|Company", "target": "Account"},
    },
})

GRAPH = {
    "db.labels": [{"label": "Account"}, {"label": "Person"}, {"label": "Company"}],
    "db.relationshipTypes": [{"relationshipType": "OWN"}],
    "MATCH (n:`Account`)": [{"key": "acct_no"}, {"key": "iban"}, {"key": "age"},
                            {"key": "_workspace_id"}],
    "MATCH (n:`Person`)": [{"key": "id"}],
    "MATCH (n:`Company`)": [{"key": "id"}],
    "MATCH ()-[r:`OWN`]->()": [],
    "labels(a)": [{"src": "Person", "t": "OWN", "dst": "Account"},
                  {"src": "Company", "t": "OWN", "dst": "Account"}],
    "SHOW INDEXES": [{"labelsOrTypes": ["Account"], "properties": ["acct_no"]}],
}


def test_undeclared_property_is_reported_and_infrastructure_is_not() -> None:
    report = conformance_report(ONTO, _fake_graph(GRAPH))
    assert not report["conformant"]
    assert any("undeclared ['age']" in p for p in report["problems"])
    # _workspace_id is infrastructure, never demanded of the ontology.
    assert not any("_workspace_id" in p for p in report["problems"])


def test_union_endpoints_expand_instead_of_reporting_phantom_drift() -> None:
    report = conformance_report(ONTO, _fake_graph(GRAPH))
    assert report["endpoint_diff"]["declared_but_absent"] == []
    assert not any("endpoints" in p and "absent" in p for p in report["problems"])


def test_unindexed_identity_key_is_a_performance_finding() -> None:
    graph = dict(GRAPH)
    graph["SHOW INDEXES"] = []
    report = conformance_report(ONTO, _fake_graph(graph))
    assert any("NOT INDEXED" in p for p in report["problems"])
