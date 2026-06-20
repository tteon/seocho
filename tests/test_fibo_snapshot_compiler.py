from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.ontology.compile_fibo_snapshot import main as compile_main


def _write_sample_fibo(root: Path) -> None:
    module_dir = root / "BE" / "LegalEntities"
    module_dir.mkdir(parents=True)
    (module_dir / "LegalPersons.rdf").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF
  xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
  xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
  xmlns:owl="http://www.w3.org/2002/07/owl#"
  xmlns:skos="http://www.w3.org/2004/02/skos/core#"
  xmlns:dct="http://purl.org/dc/terms/">
  <owl:Ontology rdf:about="https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/">
    <rdfs:label>Legal Persons Ontology</rdfs:label>
    <dct:abstract>Business entity legal person terms.</dct:abstract>
    <owl:versionIRI rdf:resource="https://spec.edmcouncil.org/fibo/ontology/BE/20260614/LegalEntities/LegalPersons/"/>
    <owl:imports rdf:resource="https://spec.edmcouncil.org/fibo/ontology/FND/Parties/Parties/"/>
  </owl:Ontology>
  <owl:Class rdf:about="https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity">
    <rdfs:label>legal entity</rdfs:label>
    <skos:definition>a legal person that is an organization.</skos:definition>
    <skos:altLabel>company</skos:altLabel>
  </owl:Class>
  <owl:ObjectProperty rdf:about="https://spec.edmcouncil.org/fibo/ontology/BE/OwnershipAndControl/owns">
    <rdfs:label>owns</rdfs:label>
    <skos:definition>owns an asset or legal entity.</skos:definition>
    <rdfs:domain rdf:resource="https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity"/>
    <rdfs:range rdf:resource="https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity"/>
  </owl:ObjectProperty>
</rdf:RDF>
""",
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "unit@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Unit"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=root, check=True, stdout=subprocess.DEVNULL)


def test_compile_fibo_snapshot_outputs_manifest_catalog_and_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fibo_root = tmp_path / "fibo"
    fibo_root.mkdir()
    _write_sample_fibo(fibo_root)

    yaml_dir = tmp_path / "curated"
    yaml_dir.mkdir()
    (yaml_dir / "be.yaml").write_text(
        yaml.safe_dump(
            {
                "nodes": {
                    "LegalEntity": {
                        "aliases": ["company"],
                        "sameAs": "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity",
                    },
                    "SeochoOnlyExtension": {},
                },
                "relationships": {
                    "OWNS": {
                        "sameAs": "https://spec.edmcouncil.org/fibo/ontology/BE/OwnershipAndControl/owns",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    out = tmp_path / "out"
    monkeypatch.setattr(
        "sys.argv",
        [
            "compile_fibo_snapshot.py",
            "--source",
            str(fibo_root),
            "--curated-yaml-dir",
            str(yaml_dir),
            "--modules",
            "BE",
            "--out",
            str(out),
        ],
    )

    assert compile_main() == 0

    with open(out / "manifest.json", "r", encoding="utf-8") as f:
        manifest = json.load(f)
    with open(out / "catalog.json", "r", encoding="utf-8") as f:
        catalog = json.load(f)
    with open(out / "compatibility_report.json", "r", encoding="utf-8") as f:
        report = json.load(f)
    with open(out / "artifact_index.json", "r", encoding="utf-8") as f:
        index = json.load(f)

    assert manifest["schema_version"] == "seocho.fibo_snapshot.v1"
    assert manifest["source"]["commit"]
    assert manifest["stats"]["ontology_count"] == 1
    assert manifest["stats"]["module_counts"]["BE"] == 2
    assert catalog["modules"]["BE"]["label_index"]["legal entity"].endswith("LegalEntity")
    assert catalog["modules"]["BE"]["label_index"]["company"].endswith("LegalEntity")
    assert report["summary"]["matched_label_count"] >= 1
    assert "seochoonlyextension" in report["curated_extension_labels"]
    assert index["runtime_contract"]["runtime_dependency"] == "compiled catalog/artifact only"
