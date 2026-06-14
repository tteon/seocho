"""Build guardrail-candidate Ontologies from a compiled FIBO catalog (ADR-0133).

ADR-0132 added a pinned FIBO submodule + an offline compiler emitting
`catalog.json` (schema `seocho.fibo_catalog.v1`): per-module label/definition/IRI
indexes. This module turns that authoritative, version-pinned catalog into SEOCHO
``Ontology`` objects usable as guardrail candidates by
``guardrail_selector.select_guardrail`` and ``ontology_scorecard.score_ontology``
— replacing the hand-made ``examples/datasets/fibo_{minus,base,plus}.jsonld``.

Pure/offline: consumes the compiled JSON (a dict or a path); never reads raw
OWL/RDF (ADR-0132 boundary). The FIBO commit + snapshot hash from the catalog flow
into ``Ontology.package_id``/``version`` so a guardrail choice is version-pinned
provenance (composes with ``OntologySnapshotStore``, ADR-0117).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .ontology import NodeDef, Ontology, RelDef

_CLASS_KINDS = {"class"}
_PROP_KINDS = {"object_property", "datatype_property"}


def load_catalog(data: Union[str, Path, Dict[str, Any]]) -> Dict[str, Any]:
    """Load a compiled FIBO catalog from a path or accept a dict. Validates the
    schema marker."""
    if isinstance(data, (str, Path)):
        data = json.loads(Path(data).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "modules" not in data:
        raise ValueError("not a FIBO catalog (missing 'modules')")
    return data


def _local_name(iri: str) -> str:
    text = str(iri or "").rstrip("/")
    frag = re.split(r"[#/]", text)[-1] if text else ""
    return frag


def _label_for(resource: Dict[str, Any], iri: str) -> str:
    """A valid SEOCHO class label: prefer the IRI local name (usually PascalCase),
    else a sanitized human label."""
    ln = str(resource.get("local_name") or _local_name(iri)).strip()
    if ln:
        return re.sub(r"[^A-Za-z0-9]", "", ln) or ln
    return re.sub(r"[^A-Za-z0-9]", "", str(resource.get("label", "")).title()) or "Entity"


def catalog_module_to_ontology(
    catalog: Dict[str, Any],
    module_code: str,
    *,
    name: Optional[str] = None,
) -> Ontology:
    """Build one ``Ontology`` from a catalog module: classes → nodes (human label
    as alias, definition, ``same_as`` = IRI, ``broader`` from subClassOf within
    the module); object properties → relationships (domain/range mapped to class
    labels)."""
    catalog = load_catalog(catalog)
    module = catalog["modules"].get(module_code)
    if module is None:
        raise KeyError(f"module '{module_code}' not in catalog")

    resources: Dict[str, Dict[str, Any]] = module.get("resources", {})
    definitions: Dict[str, str] = module.get("definitions", {})
    iri_to_label: Dict[str, str] = {iri: _label_for(r, iri) for iri, r in resources.items()}

    nodes: Dict[str, NodeDef] = {}
    for iri, r in resources.items():
        if r.get("kind") not in _CLASS_KINDS:
            continue
        label = iri_to_label[iri]
        human = str(r.get("label", "")).strip()
        aliases = [human] if human and human != label else []
        broader = [iri_to_label[p] for p in (r.get("subclass_of") or []) if p in iri_to_label]
        nodes[label] = NodeDef(
            description=str(definitions.get(iri, "")).strip(),
            aliases=aliases, broader=sorted(set(broader)), same_as=iri,
        )

    relationships: Dict[str, RelDef] = {}
    for iri, r in resources.items():
        if r.get("kind") not in _PROP_KINDS:
            continue
        rtype = re.sub(r"[^A-Za-z0-9]", "_", iri_to_label[iri]).upper() or "RELATED_TO"
        src = iri_to_label.get(r.get("domain") or "", "Any")
        tgt = iri_to_label.get(r.get("range") or "", "Any")
        relationships[rtype] = RelDef(source=src or "Any", target=tgt or "Any",
                                      description=str(definitions.get(iri, "")).strip())

    onto = Ontology(
        name or f"fibo-{module_code}",
        package_id=f"fibo.{module_code}",
        version=str(catalog.get("fibo_commit", "0"))[:12] or "0.0.0",
        description=str(module.get("summary", "")).strip(),
        nodes=nodes, relationships=relationships,
    )
    return onto


def fibo_guardrail_candidates(
    catalog: Union[str, Path, Dict[str, Any]],
    *,
    modules: Optional[List[str]] = None,
) -> Dict[str, Ontology]:
    """Build a ``{module_code: Ontology}`` map of guardrail candidates from a
    compiled FIBO catalog — feed directly to ``guardrail_selector.select_guardrail``
    (e.g. select the FIBO module whose vocabulary best covers a corpus)."""
    catalog = load_catalog(catalog)
    codes = modules if modules is not None else list(catalog["modules"].keys())
    return {code: catalog_module_to_ontology(catalog, code) for code in codes if code in catalog["modules"]}


def catalog_provenance(catalog: Union[str, Path, Dict[str, Any]]) -> Dict[str, str]:
    """The version-pinning provenance to attach to a snapshot / run."""
    catalog = load_catalog(catalog)
    return {
        "schema_version": str(catalog.get("schema_version", "")),
        "fibo_commit": str(catalog.get("fibo_commit", "")),
        "snapshot_hash": str(catalog.get("snapshot_hash", "")),
    }
