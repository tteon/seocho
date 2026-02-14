from rule_api import (
    RuleInferRequest,
    RuleValidateRequest,
    infer_rule_profile,
    validate_rule_profile,
)


def _sample_graph():
    return {
        "nodes": [
            {"id": "1", "label": "Company", "properties": {"name": "Acme", "employees": 100}},
            {"id": "2", "label": "Company", "properties": {"name": "Beta", "employees": 80}},
        ],
        "relationships": [],
    }


def test_infer_rule_profile_response():
    req = RuleInferRequest(workspace_id="default", graph=_sample_graph())
    res = infer_rule_profile(req)

    assert res.workspace_id == "default"
    assert "rules" in res.rule_profile
    assert "shapes" in res.shacl_like


def test_validate_rule_profile_with_inferred_rules():
    req = RuleValidateRequest(workspace_id="default", graph=_sample_graph())
    res = validate_rule_profile(req)

    assert res.validation_summary["total_nodes"] == 2
    assert "rule_profile" in res.model_dump()


def test_validate_rule_profile_with_given_rules():
    infer_res = infer_rule_profile(RuleInferRequest(workspace_id="default", graph=_sample_graph()))
    req = RuleValidateRequest(
        workspace_id="default",
        graph=_sample_graph(),
        rule_profile=infer_res.rule_profile,
    )
    res = validate_rule_profile(req)

    assert res.validation_summary["failed_nodes"] == 0
