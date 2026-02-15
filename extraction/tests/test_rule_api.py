from rule_api import (
    RuleInferRequest,
    RuleExportCypherRequest,
    RuleProfileCreateRequest,
    RuleValidateRequest,
    create_rule_profile,
    export_rule_profile_to_cypher,
    infer_rule_profile,
    read_rule_profile,
    read_rule_profiles,
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


def test_rule_profile_store_roundtrip_via_api(tmp_path, monkeypatch):
    monkeypatch.setenv("RULE_PROFILE_DIR", str(tmp_path))
    infer_res = infer_rule_profile(RuleInferRequest(workspace_id="default", graph=_sample_graph()))

    created = create_rule_profile(
        RuleProfileCreateRequest(
            workspace_id="default",
            name="companies_v1",
            rule_profile=infer_res.rule_profile,
        )
    )
    listed = read_rule_profiles(workspace_id="default")
    fetched = read_rule_profile(workspace_id="default", profile_id=created.profile_id)

    assert created.name == "companies_v1"
    assert len(listed.profiles) == 1
    assert fetched.profile_id == created.profile_id
    assert fetched.rule_count == created.rule_count


def test_export_rule_profile_to_cypher_from_inline_profile():
    infer_res = infer_rule_profile(RuleInferRequest(workspace_id="default", graph=_sample_graph()))
    exported = export_rule_profile_to_cypher(
        RuleExportCypherRequest(
            workspace_id="default",
            rule_profile=infer_res.rule_profile,
        )
    )

    assert isinstance(exported.statements, list)
    assert exported.schema_version == "rules.v1"
