from rule_export import export_ruleset_to_cypher, export_ruleset_to_shacl


def test_export_required_rule_to_cypher():
    profile = {
        "schema_version": "rules.v1",
        "rules": [
            {
                "label": "Company",
                "property_name": "name",
                "kind": "required",
                "params": {"minCount": 1},
            }
        ],
    }
    out = export_ruleset_to_cypher(profile)
    assert len(out["statements"]) == 1
    assert "IS NOT NULL" in out["statements"][0]
    assert out["unsupported_rules"] == []


def test_export_unsupported_rule_kind_marked():
    profile = {
        "schema_version": "rules.v1",
        "rules": [
            {
                "label": "Company",
                "property_name": "employees",
                "kind": "range",
                "params": {"minInclusive": 1, "maxInclusive": 1000},
            }
        ],
    }
    out = export_ruleset_to_cypher(profile)
    assert out["statements"] == []
    assert len(out["unsupported_rules"]) == 1
    assert out["unsupported_rules"][0]["kind"] == "range"


def test_export_ruleset_to_shacl_turtle_contains_constraints():
    profile = {
        "schema_version": "rules.v1",
        "rules": [
            {"label": "Company", "property_name": "name", "kind": "required", "params": {"minCount": 1}},
            {"label": "Company", "property_name": "name", "kind": "datatype", "params": {"datatype": "string"}},
            {
                "label": "Company",
                "property_name": "tier",
                "kind": "enum",
                "params": {"allowedValues": ["gold", "silver"]},
            },
            {
                "label": "Company",
                "property_name": "employees",
                "kind": "range",
                "params": {"minInclusive": 1, "maxInclusive": 1000},
            },
        ],
    }
    out = export_ruleset_to_shacl(profile)

    assert out["schema_version"] == "rules.v1"
    assert out["unsupported_rules"] == []
    assert len(out["shapes"]) == 1
    assert "sh:minCount 1" in out["turtle"]
    assert "sh:datatype xsd:string" in out["turtle"]
    assert 'sh:in ("gold" "silver")' in out["turtle"]
    assert "sh:minInclusive 1" in out["turtle"]
    assert "sh:maxInclusive 1000" in out["turtle"]
