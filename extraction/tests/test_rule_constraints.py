from rule_constraints import (
    apply_rules_to_graph,
    infer_rules_from_graph,
)


def test_infer_rules_from_graph_generates_datatype_and_required():
    extracted = {
        "nodes": [
            {"id": "1", "label": "Company", "properties": {"name": "Acme", "employees": 100, "industry": "Tech"}},
            {"id": "2", "label": "Company", "properties": {"name": "Beta", "employees": 80, "industry": "Tech"}},
            {"id": "3", "label": "Company", "properties": {"name": "Gamma", "employees": 120, "industry": "Finance"}},
        ],
        "relationships": [],
    }

    ruleset = infer_rules_from_graph(extracted)
    rules = {(r.label, r.property_name, r.kind) for r in ruleset.rules}

    assert ("Company", "name", "required") in rules
    assert ("Company", "name", "datatype") in rules
    assert ("Company", "employees", "range") in rules


def test_apply_rules_to_graph_marks_violations():
    extracted = {
        "nodes": [
            {"id": "1", "label": "Company", "properties": {"name": "Acme", "employees": 100, "industry": "Tech"}},
            {"id": "2", "label": "Company", "properties": {"name": "", "employees": "many", "industry": "Unknown"}},
        ],
        "relationships": [],
    }

    ruleset = infer_rules_from_graph(
        {
            "nodes": [
                {"id": "10", "label": "Company", "properties": {"name": "Acme", "employees": 100, "industry": "Tech"}},
                {"id": "11", "label": "Company", "properties": {"name": "Beta", "employees": 80, "industry": "Finance"}},
                {"id": "12", "label": "Company", "properties": {"name": "Gamma", "employees": 120, "industry": "Tech"}},
            ],
            "relationships": [],
        }
    )
    output = apply_rules_to_graph(extracted, ruleset)

    assert output["rule_validation_summary"]["failed_nodes"] == 1
    violations = output["nodes"][1]["rule_validation"]["violations"]
    assert len(violations) >= 2


def test_ruleset_shacl_like_export_shape_structure():
    extracted = {
        "nodes": [
            {"id": "1", "label": "Person", "properties": {"name": "Jane", "age": 30}},
            {"id": "2", "label": "Person", "properties": {"name": "John", "age": 40}},
        ],
        "relationships": [],
    }
    ruleset = infer_rules_from_graph(extracted)
    shacl_like = ruleset.to_shacl_like()

    assert "shapes" in shacl_like
    assert shacl_like["shapes"][0]["targetClass"] == "Person"
