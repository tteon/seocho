from semantic_pass_orchestrator import SemanticPassOrchestrator


class _FakeExtractor:
    def __init__(self):
        self.last_extra_context = None

    def extract_entities(self, text: str, category: str = "general", extra_context=None):
        self.last_extra_context = extra_context or {}
        return {
            "nodes": [
                {"id": "n1", "label": "Company", "properties": {"name": "ACME"}},
                {"id": "n2", "label": "Company", "properties": {"name": "Beta"}},
            ],
            "relationships": [{"source": "n1", "target": "n2", "type": "ACQUIRED", "properties": {}}],
        }


def test_semantic_orchestrator_runs_three_passes_and_injects_context():
    fake_extractor = _FakeExtractor()

    def _runner(system_prompt: str, user_prompt: str):
        if "ontology candidates" in system_prompt:
            return {
                "ontology_name": "finance_runtime",
                "classes": [
                    {
                        "name": "Company",
                        "description": "Corporate entity",
                        "properties": [{"name": "name", "datatype": "string"}],
                    }
                ],
                "relationships": [
                    {"type": "ACQUIRED", "source": "Company", "target": "Company", "description": ""}
                ],
            }
        return {
            "shapes": [
                {
                    "target_class": "Company",
                    "properties": [{"path": "name", "constraint": "required", "params": {"minCount": 1}}],
                }
            ]
        }

    orchestrator = SemanticPassOrchestrator(
        api_key="test-key",
        model="gpt-test",
        extractor=fake_extractor,
        json_runner=_runner,
    )
    out = orchestrator.run_three_pass(text="ACME acquired Beta in 2024.", category="finance")

    assert out["ontology_candidate"]["ontology_name"] == "finance_runtime"
    assert out["shacl_candidate"]["shapes"][0]["target_class"] == "Company"
    assert len(out["entity_graph"]["nodes"]) == 2
    assert "Company" in fake_extractor.last_extra_context.get("entity_types", "")
    assert "Company.name: required" in fake_extractor.last_extra_context.get("shacl_constraints", "")
