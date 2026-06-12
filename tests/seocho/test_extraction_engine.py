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

    def complete(
        self,
        *,
        system,
        user,
        temperature,
        response_format=None,
        reasoning_mode=None,
        task_hint=None,
    ):  # noqa: ANN001
        self.calls.append(
            {
                "system": system,
                "user": user,
                "temperature": temperature,
                "response_format": response_format,
                "reasoning_mode": reasoning_mode,
                "task_hint": task_hint,
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
    assert llm.calls[0]["reasoning_mode"] is False
    assert llm.calls[0]["task_hint"] == "json_extraction"

    linked = engine.link(extracted, category="general")
    assert linked["relationships"] == extracted["relationships"]
    assert llm.calls[1]["reasoning_mode"] is False
    assert llm.calls[1]["task_hint"] == "entity_linking"


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


def test_canonical_extraction_engine_retries_in_relaxed_mode_after_empty_ontology_guided_pass():
    ontology = Ontology(
        name="companies",
        nodes={"Company": NodeDef(properties={"name": P(str)})},
        relationships={"ACQUIRED": RelDef(source="Company", target="Company")},
    )
    llm = _FakeLLM(
        [
            {"nodes": [], "relationships": []},
            {
                "nodes": [
                    {"id": "acme", "label": "Company", "properties": {"name": "Acme"}},
                ],
                "relationships": [],
            },
        ]
    )
    engine = CanonicalExtractionEngine(ontology=ontology, llm=llm)

    extracted = engine.extract("Acme expanded into Asia.", category="general")

    assert len(llm.calls) == 2
    assert extracted["nodes"][0]["label"] == "Company"
    assert extracted["_retry"]["attempted"] is True
    assert extracted["_retry"]["succeeded"] is True
    assert "Retry once in relaxed mode" in llm.calls[1]["system"]
    assert llm.calls[1]["reasoning_mode"] is False
    assert llm.calls[1]["task_hint"] == "json_extraction_retry"


def test_normalize_payload_coerces_bare_node_list():
    # Some models (e.g. MiniMax) return a bare JSON array of entities instead of
    # the {nodes, relationships} object. It must not crash into the heuristic
    # fallback (§18) — coerce a node-shaped list to nodes.
    engine = CanonicalExtractionEngine(ontology=None, llm=None)
    out = engine.normalize_payload(
        [
            {"name": "Basal cell carcinoma", "label": "Disease"},
            {"name": "skin", "label": "Anatomy"},
        ]
    )
    assert len(out["nodes"]) == 2
    assert out["relationships"] == []


def test_normalize_payload_coerces_bare_triple_list():
    engine = CanonicalExtractionEngine(ontology=None, llm=None)
    out = engine.normalize_payload(
        [{"subject": "BCC", "predicate": "IS_A", "object": "skin cancer"}]
    )
    assert len(out["relationships"]) == 1
    assert out["relationships"][0]["type"] == "IS_A"


def test_normalize_payload_tolerates_non_mapping():
    engine = CanonicalExtractionEngine(ontology=None, llm=None)
    assert engine.normalize_payload(None) == {"nodes": [], "relationships": []}
    assert engine.normalize_payload("garbage") == {"nodes": [], "relationships": []}
