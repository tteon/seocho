from seocho import NodeDef, Ontology, P, RelDef
from seocho.index.extraction_engine import CanonicalExtractionEngine


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeLLM:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def complete(self, *, system, user, temperature, response_format=None):  # noqa: ANN001
        self.calls.append(
            {
                "system": system,
                "user": user,
                "temperature": temperature,
                "response_format": response_format,
            }
        )
        return _FakeResponse(self.payloads.pop(0))


def test_canonical_extraction_engine_normalizes_and_preserves_relationships():
    ontology = Ontology(
        name="companies",
        package_id="org.example.companies",
        nodes={"Company": NodeDef(properties={"name": P(str)})},
        relationships={"ACQUIRED": RelDef(source="Company", target="Company")},
    )
    llm = _FakeLLM(
        [
            {
                "nodes": [
                    {"id": "1", "properties": {"name": "Acme"}},
                    {"id": "2", "label": "Company", "properties": {"name": "Beta"}},
                ],
                "relationships": [{"source": "1", "target": "2", "type": "acquired"}],
            },
            {
                "nodes": [
                    {"id": "acme", "label": "Company", "properties": {"name": "Acme"}},
                    {"id": "beta", "label": "Company", "properties": {"name": "Beta"}},
                ]
            },
        ]
    )
    engine = CanonicalExtractionEngine(ontology=ontology, llm=llm)

    extracted = engine.extract("Acme acquired Beta in 2024.", category="general")
    assert extracted["nodes"][0]["label"] == "Company"
    assert extracted["nodes"][0]["id"] == "acme"
    assert extracted["relationships"][0]["type"] == "ACQUIRED"

    linked = engine.link(extracted, category="general")
    assert linked["relationships"] == extracted["relationships"]


def test_canonical_extraction_engine_uses_custom_prompt_templates_without_ontology():
    llm = _FakeLLM([{"nodes": [], "relationships": []}])
    engine = CanonicalExtractionEngine(
        ontology=None,
        llm=llm,
        custom_prompts={
            "system": "Category={{category}} Ontology={{ontology_name}}",
            "user": "Input={{text}}",
        },
    )

    engine.extract("hello world", category="finance")

    assert "Category=finance" in llm.calls[0]["system"]
    assert "Ontology=" in llm.calls[0]["system"]
    assert llm.calls[0]["user"] == "Input=hello world"


def test_canonical_extraction_engine_flattens_nested_relationship_properties():
    ontology = Ontology(
        name="companies",
        nodes={"Company": NodeDef(properties={"name": P(str)})},
        relationships={"ACQUIRED": RelDef(source="Company", target="Company")},
    )
    llm = _FakeLLM(
        [
            {
                "nodes": [
                    {"id": "1", "label": "Company", "properties": {"name": "Acme"}},
                    {"id": "2", "label": "Company", "properties": {"name": "Beta"}},
                ],
                "relationships": [
                    {
                        "source": "1",
                        "target": "2",
                        "type": "ACQUIRED",
                        "properties": {"year": 2024},
                    }
                ],
            }
        ]
    )
    engine = CanonicalExtractionEngine(ontology=ontology, llm=llm)

    extracted = engine.extract("Acme acquired Beta in 2024.", category="general")

    assert extracted["relationships"][0]["properties"] == {"year": 2024}
