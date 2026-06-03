from __future__ import annotations

from seocho.ontology_control_plane import (
    OntologyControlPlane,
    OntologyProfile,
    OntologyProfileRegistry,
)


def _finance_profile(*, status: str = "approved", judge_score: float = 0.62) -> OntologyProfile:
    return OntologyProfile(
        profile_id="finance-core",
        workspace_id="acme",
        ontology_id="finance",
        version="v1",
        status=status,
        ontology_candidate={
            "ontology_name": "finance",
            "classes": [
                {
                    "name": "Company",
                    "aliases": ["issuer", "registrant"],
                    "properties": [{"name": "name", "aliases": ["company name"]}],
                },
                {
                    "name": "FinancialMetric",
                    "aliases": ["metric", "financial result"],
                    "properties": [
                        {"name": "value", "aliases": ["amount", "revenue"]},
                        {"name": "year", "aliases": ["fiscal year"]},
                    ],
                },
            ],
            "relationships": [
                {
                    "type": "REPORTED",
                    "source": "Company",
                    "target": "FinancialMetric",
                    "aliases": ["reported revenue", "filed metric"],
                }
            ],
        },
        vocabulary_candidate={
            "terms": [
                {
                    "pref_label": "Data and Access Solutions revenue",
                    "alt_labels": ["DAS revenue", "data access rev"],
                }
            ]
        },
        shacl_candidate={
            "shapes": [
                {
                    "target_class": "FinancialMetric",
                    "properties": [
                        {"path": "value", "constraint": "minCount", "params": {"value": 1}},
                        {"path": "year", "constraint": "minCount", "params": {"value": 1}},
                    ],
                }
            ]
        },
        route_hints={"route_classes": ["R4_GRAPH_JOIN"]},
        answer_shapes={"financial_metric_delta": {"required_slots": ["company", "value", "year"]}},
        metrics={"judge_score": judge_score, "slot_coverage": 0.9, "latency_ms": 1200.0, "token_cost": 900.0},
    )


def test_control_plane_compiles_hot_path_profile() -> None:
    profile = _finance_profile()
    control = OntologyControlPlane(OntologyProfileRegistry([profile]))

    compiled = control.compile_profile("finance-core", workspace_id="acme")

    assert compiled.schema_version == "ontology_control_profile.v1"
    assert compiled.label_aliases["das revenue"] == "Data and Access Solutions revenue"
    assert compiled.label_aliases["revenue"] == "FinancialMetric"
    assert compiled.relation_aliases["reported revenue"] == "REPORTED"
    assert compiled.required_slots == ["FinancialMetric.value", "FinancialMetric.year"]


def test_control_plane_selects_profile_from_query_and_indexing_signals() -> None:
    finance = _finance_profile()
    legal = OntologyProfile(
        profile_id="legal-core",
        workspace_id="acme",
        ontology_id="legal",
        version="v1",
        status="approved",
        ontology_candidate={
            "ontology_name": "legal",
            "classes": [{"name": "LegalIssue", "aliases": ["litigation"]}],
            "relationships": [{"type": "INVOLVED_IN", "aliases": ["faces litigation"]}],
        },
        metrics={"judge_score": 0.7, "slot_coverage": 0.7},
    )
    control = OntologyControlPlane(OntologyProfileRegistry([finance, legal]))
    control.collect_signal(
        {
            "source": "indexing",
            "kind": "alias_candidate",
            "workspace_id": "acme",
            "profile_id": "finance-core",
            "canonical": "Data and Access Solutions revenue",
            "observed": "DAS revenue",
            "confidence": 0.9,
        }
    )

    selection = control.select_profile(
        "What was CBOE DAS revenue delta?",
        workspace_id="acme",
        route_profile={"route_class": "R4_GRAPH_JOIN"},
    )

    assert selection.profile_id == "finance-core"
    assert selection.score > 0.5
    assert "alias_match" in selection.reasons
    assert "route_class_match" in selection.reasons
    assert selection.compiled_profile["profile_id"] == "finance-core"


def test_control_plane_evaluates_candidate_for_user_review() -> None:
    baseline = _finance_profile(judge_score=0.62)
    candidate = _finance_profile(status="draft", judge_score=0.69)
    candidate.profile_id = "finance-core-candidate"
    candidate.metrics["slot_coverage"] = 0.96
    candidate.metrics["latency_ms"] = 1180.0
    candidate.metrics["token_cost"] = 840.0
    control = OntologyControlPlane(OntologyProfileRegistry([baseline, candidate]))

    evaluation = control.evaluate_profile(
        "finance-core-candidate",
        baseline="finance-core",
        workspace_id="acme",
    )

    assert evaluation.decision == "promote_candidate"
    assert evaluation.expected_effect["quality_delta"] == 0.07
    assert evaluation.expected_effect["latency_ms_delta"] == -20.0
    assert evaluation.expected_effect["cost_delta"] == -60.0
    assert {"approve_profile", "edit_aliases", "rerun_regression"} <= set(evaluation.user_controls)
