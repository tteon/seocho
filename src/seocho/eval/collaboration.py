"""Trusted graph recipe boundary for the collaboration experiment."""
from __future__ import annotations

from typing import Any

from ..query.contracts import CypherPlan
from ..query.cypher_validator import CypherQueryValidator
from ..store.graph import validate_database_name

RECIPE_PATHS = {
    "incidents": "Company-REPORTED->Incident",
    "controls": "Company-REPORTED->Incident-MITIGATED_BY->Control-HAS_EVIDENCE->Attestation",
}
_PREFIX = (
    "MATCH (c:CollabCompany)-[r:REPORTED]->(i:CollabIncident) "
    "WHERE c.id = $node_id AND c._workspace_id = $workspace_id "
    "AND i._workspace_id = $workspace_id AND r._workspace_id = $workspace_id "
)
QUERIES = {
    "incidents": _PREFIX +
    "RETURN i.id AS evidence_id, c.id AS company, i.summary AS summary, "
    "i.date AS date, i.severity AS severity, i.source AS source ORDER BY i.id LIMIT 40",
    "controls": _PREFIX +
    "MATCH (i)-[m:MITIGATED_BY]->(k:CollabControl)-[h:HAS_EVIDENCE]->(a:CollabAttestation) "
    "WHERE k._workspace_id = $workspace_id AND a._workspace_id = $workspace_id "
    "AND m._workspace_id = $workspace_id AND h._workspace_id = $workspace_id "
    "RETURN a.id AS evidence_id, c.id AS company, i.id AS incident, k.id AS control, "
    "a.status AS status, a.date AS date, a.source AS source ORDER BY a.id LIMIT 40",
}


class RecipeRetriever:
    """Permit only fixed, bounded read queries and enforce workspace scope."""

    def __init__(self, graph_store: Any, *, workspace_id: str, database: str = "neo4j") -> None:
        if not workspace_id.strip():
            raise ValueError("workspace_id is required")
        self.graph_store = graph_store
        self.workspace_id = workspace_id
        self.database = database if database == "neo4j" else validate_database_name(database)

    def __call__(self, recipe_id: str, node_id: str) -> list[dict[str, Any]]:
        query = QUERIES[recipe_id]
        params = {"node_id": node_id, "workspace_id": self.workspace_id}
        plan = CypherPlan(database=self.database, query=query, params=params,
                          strategy="collaboration_recipe", anchor_entity=node_id)
        verdict = CypherQueryValidator().validate(plan, {
            "allowed_labels": ["CollabCompany", "CollabIncident", "CollabControl", "CollabAttestation"],
            "allowed_relationship_types": ["REPORTED", "MITIGATED_BY", "HAS_EVIDENCE"],
            "allowed_properties": ["id", "_workspace_id", "summary", "date", "severity", "source", "status"],
            "max_result_rows": 40, "max_graph_hops": 3,
        })
        if not verdict["ok"]:
            raise ValueError(f"Invalid host recipe: {verdict['violations']}")
        return self.graph_store.query(
            query, params=params, database=self.database, workspace_id=self.workspace_id,
            enforce_workspace_filter=True,
        )


def score_evidence(report: dict[str, Any], expected_refs: list[str]) -> dict[str, Any]:
    """Diagnostic evidence recall, deliberately not an answer-correctness score."""
    retrieved = {ref for packet in report["packets"] for ref in packet["evidence_refs"]}
    cited = {ref for claim in (report.get("answer") or {}).get("claims", [])
             for ref in claim["evidence_refs"]}
    expected = set(expected_refs)
    return {
        "expected_evidence_recall": len(retrieved & expected) / len(expected) if expected else None,
        "expected_citation_recall": len(cited & expected) / len(expected) if expected else None,
        "answer_correctness": None,
        "note": "Recall is a diagnostic; inspect claim entailment and temporal/conflict handling separately.",
    }
