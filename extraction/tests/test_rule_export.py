from rule_export import export_ruleset_to_cypher


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
