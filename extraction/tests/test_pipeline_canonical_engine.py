import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
fake_pandas = types.ModuleType("pandas")
fake_pandas.DataFrame = object
fake_pandas.NA = object()
fake_pandas.read_csv = lambda *args, **kwargs: None
fake_pandas.read_json = lambda *args, **kwargs: None
fake_pandas.read_parquet = lambda *args, **kwargs: None
sys.modules.setdefault("pandas", fake_pandas)
fake_neo4j = types.ModuleType("neo4j")
fake_neo4j.GraphDatabase = object
fake_neo4j_exceptions = types.ModuleType("neo4j.exceptions")
fake_neo4j_exceptions.ServiceUnavailable = RuntimeError
fake_neo4j_exceptions.SessionExpired = RuntimeError
sys.modules.setdefault("neo4j", fake_neo4j)
sys.modules.setdefault("neo4j.exceptions", fake_neo4j_exceptions)

import schema_manager  # noqa: E402
import pipeline as pipeline_module  # noqa: E402
from seocho import NodeDef, Ontology, P, RelDef  # noqa: E402


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
        self.calls.append({"system": system, "user": user})
        return _FakeResponse(self.payloads.pop(0))


class _FakeGraphLoader:
    def __init__(self, *_args, **_kwargs):
        self.loaded = []

    def load_graph(self, graph_data, source_id, database):  # noqa: ANN001
        self.loaded.append((graph_data, source_id, database))

    def close(self):
        return None


class _FakeVectorStore:
    def __init__(self, *_args, **_kwargs):
        self.docs = []

    def add_document(self, doc_id, content):  # noqa: ANN001
        self.docs.append((doc_id, content))

    def save_index(self, _output_dir):
        return None


class _FakeDeduplicator:
    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        pass

    def deduplicate(self, extracted_data):
        return extracted_data


class _FakeSchemaManager:
    def __init__(self, *_args, **_kwargs):
        self.updated = []
        self.applied = []

    def update_schema_from_records(self, records, yaml_path):  # noqa: ANN001
        self.updated.append((records, yaml_path))

    def apply_schema(self, database, yaml_path):  # noqa: ANN001
        self.applied.append((database, yaml_path))

    def close(self):
        return None


def test_extraction_pipeline_uses_canonical_engine(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    ontology = Ontology(
        name="company_memory",
        package_id="org.example.company_memory",
        nodes={"Company": NodeDef(properties={"name": P(str)})},
        relationships={"ACQUIRED": RelDef(source="Company", target="Company")},
    )
    ontology_path = tmp_path / "ontology.yaml"
    ontology.to_yaml(ontology_path)

    fake_llm = _FakeLLM(
        [
            {
                "nodes": [
                    {"id": "1", "properties": {"name": "Acme"}},
                    {"id": "2", "properties": {"name": "Beta"}},
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

    monkeypatch.setattr(pipeline_module, "create_llm_backend", lambda **kwargs: fake_llm)
    monkeypatch.setattr(pipeline_module, "GraphLoader", _FakeGraphLoader)
    monkeypatch.setattr(pipeline_module, "VectorStore", _FakeVectorStore)
    monkeypatch.setattr(pipeline_module, "EntityDeduplicator", _FakeDeduplicator)
    monkeypatch.setattr(schema_manager, "SchemaManager", _FakeSchemaManager)

    cfg = SimpleNamespace(
        model="gpt-test",
        openai_api_key="test-key",
        mock_data=False,
        enable_rule_constraints=False,
        prompts=SimpleNamespace(
            system="Ontology={{ontology_name}}\nTypes={{entity_types}}",
            user="Extract {{text}}",
        ),
        linking_prompt=SimpleNamespace(linking="Link entities for {{category}}:\n{{entities}}"),
    )
    cfg.get = lambda key, default=None: getattr(cfg, key, default)

    pipeline = pipeline_module.ExtractionPipeline(
        cfg,
        ontology_path=str(ontology_path),
        target_database="kgnormal",
    )
    pipeline.process_item({"id": "doc-1", "content": "Acme acquired Beta.", "category": "finance"})

    assert "Ontology=company_memory" in fake_llm.calls[0]["system"]
    loaded_graph, source_id, database = pipeline.graph_loader.loaded[0]
    assert source_id == "doc-1"
    assert database == "kgnormal"
    assert loaded_graph["nodes"][0]["label"] == "Company"
    assert loaded_graph["relationships"][0]["type"] == "ACQUIRED"
    assert pipeline.vector_store.docs == [("doc-1", "Acme acquired Beta.")]
