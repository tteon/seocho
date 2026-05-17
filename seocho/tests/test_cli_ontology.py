from __future__ import annotations

import json

from seocho.cli import main
from seocho.ontology import NodeDef, Ontology, P, RelDef


def _write_schema(tmp_path) -> str:
    path = tmp_path / "schema.jsonld"
    Ontology(
        name="company_graph",
        graph_model="lpg",
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
            "Person": NodeDef(properties={"name": P(str, unique=True)}),
        },
        relationships={
            "WORKS_AT": RelDef(source="Person", target="Company", cardinality="MANY_TO_ONE"),
        },
    ).to_jsonld(path)
    return str(path)


def _write_ttl_schema(tmp_path) -> str:
    path = tmp_path / "schema.ttl"
    path.write_text(
        """
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix ex: <https://example.com/finance#> .

ex:FinanceOntology a owl:Ontology ;
    rdfs:label "Finance TTL" ;
    owl:versionInfo "1.2.0" .

ex:Company a owl:Class .
ex:FinancialMetric a owl:Class .

ex:reported a owl:ObjectProperty ;
    rdfs:domain ex:Company ;
    rdfs:range ex:FinancialMetric .

ex:name a owl:DatatypeProperty ;
    rdfs:domain ex:Company ;
    rdfs:range xsd:string .
""".strip(),
        encoding="utf-8",
    )
    return str(path)


def test_cli_ontology_check_json(tmp_path, capsys) -> None:
    schema_path = _write_schema(tmp_path)

    exit_code = main(["ontology", "check", "--schema", schema_path, "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["package_id"] == "company_graph"
    assert payload["stats"]["node_count"] == 2


def test_cli_ontology_export_shacl_to_output(tmp_path, capsys) -> None:
    schema_path = _write_schema(tmp_path)
    output_path = tmp_path / "shacl.json"

    exit_code = main(
        [
            "ontology",
            "export",
            "--schema",
            schema_path,
            "--format",
            "shacl",
            "--output",
            str(output_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "exported shacl" in captured.out
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert "shapes" in payload


def test_cli_ontology_diff_json(tmp_path, capsys) -> None:
    left_path = tmp_path / "left.jsonld"
    right_path = tmp_path / "right.jsonld"

    Ontology(
        name="finance",
        version="1.0.0",
        nodes={"Company": NodeDef(properties={"name": P(str, unique=True)})},
        relationships={},
    ).to_jsonld(left_path)
    Ontology(
        name="finance",
        version="1.1.0",
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
            "Metric": NodeDef(properties={"name": P(str, unique=True)}),
        },
        relationships={},
    ).to_jsonld(right_path)

    exit_code = main(
        [
            "ontology",
            "diff",
            "--left",
            str(left_path),
            "--right",
            str(right_path),
            "--json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["recommended_bump"] == "minor"
    assert payload["requires_migration"] is False
    assert "version" in payload["changes"]["metadata"]["changed"]
    assert "Metric" in payload["changes"]["nodes"]["added"]


def test_cli_ontology_report_json_supports_ttl(tmp_path, capsys) -> None:
    schema_path = _write_ttl_schema(tmp_path)

    exit_code = main(
        [
            "ontology",
            "report",
            "--schema",
            schema_path,
            "--skip-owl-inspection",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["context_descriptor"]["ontology_id"] == "FinanceOntology"
    assert payload["context_descriptor"]["context_hash"]
    assert payload["shacl_export"]["stats"]["node_shape_count"] == 2
