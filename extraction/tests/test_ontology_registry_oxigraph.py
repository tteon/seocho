from __future__ import annotations

from runtime.ontology_registry import RuntimeOntologyRegistry
from seocho.ontology import NodeDef, Ontology


def test_oxigraph_read_model_is_optional_and_failure_is_non_blocking(tmp_path):
    registry = RuntimeOntologyRegistry()
    ontology = Ontology(name="work", nodes={"Person": NodeDef()})

    registry.register("work", "work", ontology)
    assert registry.lookup_rdf_term("work", "Person") is None

    registry.register(
        "work", "work", ontology, oxigraph_socket=str(tmp_path / "missing.sock")
    )
    assert registry.lookup_rdf_term("work", "Person") is None
