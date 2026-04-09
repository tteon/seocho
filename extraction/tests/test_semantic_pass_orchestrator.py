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


def test_semantic_orchestrator_injects_graph_metadata_and_developer_prompt_context():
    fake_extractor = _FakeExtractor()

    def _runner(system_prompt: str, user_prompt: str):
        if "ontology candidates" in system_prompt:
            return {"ontology_name": "", "classes": [], "relationships": []}
        return {"shapes": []}

    orchestrator = SemanticPassOrchestrator(
        api_key="test-key",
        model="gpt-test",
        extractor=fake_extractor,
        json_runner=_runner,
    )
    out = orchestrator.run_three_pass(
        text="ACME acquired Beta in 2024.",
        category="finance",
        record_metadata={
            "semantic_prompt_context": {
                "instructions": ["Use the enterprise vocabulary profile."],
                "known_entities": ["ACME"],
                "vocabulary_candidate": {
                    "terms": [{"pref_label": "Acquisition", "alt_labels": ["M&A"]}]
                },
            }
        },
        approved_artifacts={
            "ontology_candidate": {
                "ontology_name": "approved_finance",
                "classes": [{"name": "Company", "description": "Corp", "properties": []}],
                "relationships": [],
            }
        },
        graph_metadata={
            "graph_id": "kgfinance",
            "database": "kgfinance",
            "ontology_id": "finance",
            "vocabulary_profile": "vocabulary.v2",
            "description": "Finance graph",
            "workspace_scope": "default",
        },
    )

    prompt_context = out["prompt_context"]
    assert "Graph ID: kgfinance" in prompt_context["graph_context"]
    assert "Use the enterprise vocabulary profile." in prompt_context["developer_instructions"]
    assert "Company" in prompt_context["entity_types"]
    assert "Acquisition" in prompt_context["vocabulary_terms"]
    assert "ACME" in prompt_context["entity_guidance"]
    assert "Graph ID: kgfinance" in fake_extractor.last_extra_context.get("graph_context", "")
