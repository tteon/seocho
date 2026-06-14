#!/usr/bin/env python3
"""Compile a pinned EDM Council FIBO checkout into SEOCHO runtime artifacts.

The official FIBO repository is the source snapshot. Runtime code should not
read the full OWL/RDF tree directly; this offline compiler emits small JSON
artifacts that carry version, import, label, definition, and compatibility
signals for governance and benchmark gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / "third_party" / "fibo"
DEFAULT_YAML = ROOT / "examples" / "finder" / "datasets" / "fibo_modules"
DEFAULT_OUT = ROOT / "outputs" / "semantic_artifacts" / "fibo" / "latest"

RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
RDFS = "{http://www.w3.org/2000/01/rdf-schema#}"
OWL = "{http://www.w3.org/2002/07/owl#}"
SKOS = "{http://www.w3.org/2004/02/skos/core#}"
DCT = "{http://purl.org/dc/terms/}"


@dataclass(slots=True)
class OntologyHeader:
    iri: str
    version_iri: str = ""
    label: str = ""
    abstract: str = ""
    imports: list[str] = field(default_factory=list)
    path: str = ""


@dataclass(slots=True)
class ResourceRecord:
    iri: str
    local_name: str
    kind: str
    module: str
    path: str
    label: str = ""
    definition: str = ""
    aliases: list[str] = field(default_factory=list)
    domain: list[str] = field(default_factory=list)
    range: list[str] = field(default_factory=list)
    subclass_of: list[str] = field(default_factory=list)


def _text(elem: ET.Element | None) -> str:
    if elem is None or elem.text is None:
        return ""
    return " ".join(elem.text.split())


def _attr(elem: ET.Element, local: str) -> str:
    return str(elem.attrib.get(RDF + local, "") or "").strip()


def _local_name(iri: str) -> str:
    text = str(iri or "").rstrip("/")
    if not text:
        return ""
    return text.rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def _module_for(path: Path, source: Path) -> str:
    try:
        rel = path.relative_to(source)
    except ValueError:
        return ""
    return rel.parts[0] if rel.parts else ""


def _resource_values(elem: ET.Element, tag: str) -> list[str]:
    values: list[str] = []
    for child in elem.findall(tag):
        resource = _attr(child, "resource")
        if resource:
            values.append(resource)
    return values


def _literal_values(elem: ET.Element, tag: str) -> list[str]:
    return [value for child in elem.findall(tag) if (value := _text(child))]


def _parse_rdf_file(path: Path, source: Path) -> tuple[list[OntologyHeader], list[ResourceRecord], list[str]]:
    warnings: list[str] = []
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        return [], [], [f"{path}: XML parse failed: {exc}"]

    root = tree.getroot()
    module = _module_for(path, source)
    rel_path = str(path.relative_to(source))
    headers: list[OntologyHeader] = []
    resources: list[ResourceRecord] = []

    for elem in root.iter(OWL + "Ontology"):
        iri = _attr(elem, "about")
        headers.append(
            OntologyHeader(
                iri=iri,
                version_iri=next(iter(_resource_values(elem, OWL + "versionIRI")), ""),
                label=next(iter(_literal_values(elem, RDFS + "label")), ""),
                abstract=next(iter(_literal_values(elem, DCT + "abstract")), ""),
                imports=_resource_values(elem, OWL + "imports"),
                path=rel_path,
            )
        )

    resource_specs = (
        (OWL + "Class", "class"),
        (OWL + "ObjectProperty", "object_property"),
        (OWL + "DatatypeProperty", "datatype_property"),
        (OWL + "NamedIndividual", "individual"),
    )
    for tag, kind in resource_specs:
        for elem in root.iter(tag):
            iri = _attr(elem, "about")
            if not iri:
                continue
            label = next(iter(_literal_values(elem, RDFS + "label")), "")
            aliases = [
                value
                for tag_name in (SKOS + "altLabel", SKOS + "prefLabel")
                for value in _literal_values(elem, tag_name)
                if value and value != label
            ]
            resources.append(
                ResourceRecord(
                    iri=iri,
                    local_name=_local_name(iri),
                    kind=kind,
                    module=module,
                    path=rel_path,
                    label=label,
                    definition=next(iter(_literal_values(elem, SKOS + "definition")), ""),
                    aliases=sorted(set(aliases)),
                    domain=_resource_values(elem, RDFS + "domain"),
                    range=_resource_values(elem, RDFS + "range"),
                    subclass_of=_resource_values(elem, RDFS + "subClassOf"),
                )
            )

    return headers, resources, warnings


def _git_commit(source: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return ""


def _snapshot_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _scan_fibo(source: Path, modules: set[str]) -> tuple[list[OntologyHeader], list[ResourceRecord], list[str]]:
    headers: list[OntologyHeader] = []
    resources: list[ResourceRecord] = []
    warnings: list[str] = []
    for path in sorted(source.rglob("*.rdf")):
        module = _module_for(path, source)
        if modules and module not in modules:
            continue
        h, r, w = _parse_rdf_file(path, source)
        headers.extend(h)
        resources.extend(r)
        warnings.extend(w)
    return headers, resources, warnings


def _build_manifest(source: Path, headers: list[OntologyHeader], resources: list[ResourceRecord], warnings: list[str]) -> dict[str, Any]:
    module_counts = Counter(record.module for record in resources)
    kind_counts = Counter(record.kind for record in resources)
    import_edges = []
    for header in headers:
        for imported in header.imports:
            import_edges.append({"from": header.iri, "to": imported, "path": header.path})
    payload = {
        "schema_version": "seocho.fibo_snapshot.v1",
        "source": {
            "kind": "git_submodule",
            "path": str(source),
            "remote": "https://github.com/edmcouncil/fibo.git",
            "commit": _git_commit(source),
        },
        "stats": {
            "ontology_count": len(headers),
            "resource_count": len(resources),
            "module_counts": dict(sorted(module_counts.items())),
            "kind_counts": dict(sorted(kind_counts.items())),
            "import_count": len(import_edges),
            "warning_count": len(warnings),
        },
        "ontologies": [asdict(header) for header in headers],
        "imports": import_edges,
        "warnings": warnings,
    }
    payload["snapshot_hash"] = _snapshot_hash(payload)
    return payload


def _build_catalog(manifest: dict[str, Any], resources: list[ResourceRecord]) -> dict[str, Any]:
    modules: dict[str, dict[str, Any]] = {}
    for record in resources:
        if record.kind not in {"class", "object_property", "datatype_property"}:
            continue
        module = modules.setdefault(
            record.module,
            {
                "code": record.module,
                "iri_prefix": f"https://spec.edmcouncil.org/fibo/ontology/{record.module}/",
                "summary": f"Official FIBO {record.module} module compiled from pinned snapshot.",
                "label_index": {},
                "definitions": {},
                "resources": {},
            },
        )
        labels = [record.label, record.local_name, *record.aliases]
        for label in labels:
            label = str(label or "").strip()
            if label:
                module["label_index"].setdefault(label, record.iri)
        if record.definition:
            module["definitions"][record.iri] = record.definition
        module["resources"][record.iri] = {
            "kind": record.kind,
            "local_name": record.local_name,
            "label": record.label,
            "path": record.path,
            "domain": record.domain,
            "range": record.range,
            "subclass_of": record.subclass_of,
        }
    return {
        "schema_version": "seocho.fibo_catalog.v1",
        "snapshot_hash": manifest["snapshot_hash"],
        "fibo_commit": manifest["source"]["commit"],
        "modules": dict(sorted(modules.items())),
    }


def _load_curated_yaml(yaml_dir: Path) -> dict[str, Any]:
    payload: dict[str, Any] = {"modules": {}, "labels": {}, "same_as": {}}
    if not yaml_dir.exists():
        return payload
    for path in sorted(yaml_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        code = path.stem.upper()
        labels: set[str] = set()
        same_as: dict[str, str] = {}
        for label, node in (data.get("nodes") or {}).items():
            labels.add(str(label))
            for alias in node.get("aliases") or []:
                labels.add(str(alias))
            if node.get("sameAs"):
                same_as[str(label)] = str(node["sameAs"])
        for label, rel in (data.get("relationships") or {}).items():
            labels.add(str(label))
            for alias in rel.get("aliases") or []:
                labels.add(str(alias))
            if rel.get("sameAs"):
                same_as[str(label)] = str(rel["sameAs"])
        payload["modules"][code] = {
            "path": str(path),
            "label_count": len(labels),
            "same_as_count": len(same_as),
        }
        for label in labels:
            payload["labels"].setdefault(label.casefold(), []).append(code)
        payload["same_as"].update(same_as)
    return payload


def _build_compatibility_report(
    manifest: dict[str, Any],
    catalog: dict[str, Any],
    yaml_dir: Path,
) -> dict[str, Any]:
    curated = _load_curated_yaml(yaml_dir)
    official_labels: dict[str, list[str]] = defaultdict(list)
    for code, module in catalog["modules"].items():
        for label, iri in module["label_index"].items():
            official_labels[label.casefold()].append(iri)

    curated_labels = set(curated["labels"])
    official_label_set = set(official_labels)
    matched = sorted(curated_labels & official_label_set)
    extension = sorted(curated_labels - official_label_set)
    missing_curated = sorted(official_label_set - curated_labels)
    same_as_matches = {
        label: iri
        for label, iri in curated["same_as"].items()
        if iri in {item for values in official_labels.values() for item in values}
    }

    return {
        "schema_version": "seocho.fibo_compat_report.v1",
        "snapshot_hash": manifest["snapshot_hash"],
        "fibo_commit": manifest["source"]["commit"],
        "curated_yaml_dir": str(yaml_dir),
        "summary": {
            "curated_label_count": len(curated_labels),
            "official_label_count": len(official_label_set),
            "matched_label_count": len(matched),
            "curated_extension_label_count": len(extension),
            "official_labels_not_in_curated_count": len(missing_curated),
            "same_as_count": len(curated["same_as"]),
            "same_as_official_match_count": len(same_as_matches),
        },
        "matched_labels": matched[:500],
        "curated_extension_labels": extension[:500],
        "official_labels_not_in_curated_sample": missing_curated[:500],
        "curated_modules": curated["modules"],
        "notes": [
            "Curated YAML modules are SEOCHO LPG runtime slices, not a faithful FIBO OWL round-trip.",
            "Use sameAs mappings and this report to decide whether a curated label is official FIBO-aligned or a SEOCHO extension.",
            "Runtime should consume compiled catalog/artifacts, not the full FIBO submodule tree.",
        ],
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(DEFAULT_SOURCE), help="FIBO checkout/submodule path")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="output directory")
    ap.add_argument("--curated-yaml-dir", default=str(DEFAULT_YAML))
    ap.add_argument(
        "--modules",
        default="BE,FBC,FND,SEC",
        help="comma-separated top-level FIBO modules to compile; empty means all",
    )
    args = ap.parse_args()

    source = Path(args.source)
    if not source.exists():
        raise FileNotFoundError(f"FIBO source path not found: {source}")
    modules = {m.strip() for m in args.modules.split(",") if m.strip()}

    headers, resources, warnings = _scan_fibo(source, modules)
    manifest = _build_manifest(source, headers, resources, warnings)
    catalog = _build_catalog(manifest, resources)
    compat = _build_compatibility_report(manifest, catalog, Path(args.curated_yaml_dir))

    out = Path(args.out)
    _write_json(out / "manifest.json", manifest)
    _write_json(out / "catalog.json", catalog)
    _write_json(out / "compatibility_report.json", compat)
    _write_json(
        out / "artifact_index.json",
        {
            "schema_version": "seocho.fibo_artifact_index.v1",
            "snapshot_hash": manifest["snapshot_hash"],
            "fibo_commit": manifest["source"]["commit"],
            "files": {
                "manifest": "manifest.json",
                "catalog": "catalog.json",
                "compatibility_report": "compatibility_report.json",
            },
            "runtime_contract": {
                "source_snapshot": "third_party/fibo git submodule",
                "runtime_dependency": "compiled catalog/artifact only",
                "reasoning_boundary": "offline governance only",
            },
        },
    )
    print(json.dumps({
        "out": str(out),
        "snapshot_hash": manifest["snapshot_hash"],
        "fibo_commit": manifest["source"]["commit"],
        "modules": sorted(catalog["modules"]),
        "resource_count": manifest["stats"]["resource_count"],
        "matched_curated_labels": compat["summary"]["matched_label_count"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
