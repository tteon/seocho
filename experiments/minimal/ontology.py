"""Ontology as JSON-LD: which classes are declared, and what that implies.

The harness needs exactly two things from an ontology, so it holds exactly two:
the set of declared classes, and whether a given entity carries one. That second
question is the decisive one, because entities with a declared type were measured
to overlap across views 4.5 times less often than untyped ones.

The FIBO modules in this repository are YAML; this converts them to JSON-LD once
and caches the result, so the ontology travels with a run as a single readable
artifact rather than as a code path.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

CONTEXT = {
    "@vocab": "https://spec.edmcouncil.org/fibo/ontology/",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "label": "rdfs:label",
    "subClassOf": {"@id": "rdfs:subClassOf", "@type": "@id"},
    "domain": {"@id": "rdfs:domain", "@type": "@id"},
    "range": {"@id": "rdfs:range", "@type": "@id"},
}
# The label an extractor falls back to when no declared class applies. Kept
# explicit because the declared-versus-generic split is a measured variable.
GENERIC_CLASS = "Entity"


@dataclass(frozen=True)
class Ontology:
    name: str
    classes: frozenset[str]
    relations: tuple[dict[str, str], ...]
    source: str

    def declares(self, labels: Iterable[str]) -> bool:
        """True when at least one label is a declared class, not the fallback."""
        return bool({l for l in labels if l != GENERIC_CLASS} & self.classes)

    def typing_of(self, labels: Iterable[str]) -> str:
        """Name the typing status so it can be grouped in results."""
        present = set(labels)
        if self.declares(present):
            return "declared"
        if GENERIC_CLASS in present:
            return "generic_fallback"
        return "undeclared_other"

    def as_jsonld(self) -> dict[str, Any]:
        return {
            "@context": CONTEXT,
            "@id": f"urn:ontology:{self.name}",
            "label": self.name,
            "@graph": (
                [{"@id": c, "@type": "rdfs:Class", "label": c} for c in sorted(self.classes)]
                + [{"@id": r["name"], "@type": "rdf:Property",
                    "domain": r.get("source", ""), "range": r.get("target", "")}
                   for r in self.relations]
            ),
        }


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml
    return yaml.safe_load(path.read_text()) or {}


def load(name: str, modules: list[str], module_dir: Path,
         cache_dir: Path | None = None) -> Ontology:
    """Compose the named modules into one ontology and emit it as JSON-LD."""
    classes: set[str] = set()
    relations: list[dict[str, str]] = []
    missing = [m for m in modules if not (module_dir / f"{m}.yaml").is_file()]
    if missing:
        raise SystemExit(f"ontology modules not found in {module_dir}: {missing}")

    for module in modules:
        spec = _load_yaml(module_dir / f"{module}.yaml")
        # The FIBO modules key their classes and relationships by name rather
        # than listing them, so read both shapes.
        nodes = spec.get("nodes") or {}
        if isinstance(nodes, dict):
            classes.update(str(k) for k in nodes)
        else:
            classes.update(str(n.get("label") or n.get("name")) for n in nodes
                           if n.get("label") or n.get("name"))
        rels = spec.get("relationships") or spec.get("relations") or {}
        if isinstance(rels, dict):
            for name_, body in rels.items():
                body = body or {}
                relations.append({"name": str(name_),
                                  "source": str(body.get("source", "")),
                                  "target": str(body.get("target", ""))})
        else:
            for rel in rels:
                name_ = rel.get("type") or rel.get("name")
                if name_:
                    relations.append({"name": str(name_),
                                      "source": str(rel.get("source", "")),
                                      "target": str(rel.get("target", ""))})

    ontology = Ontology(name=name, classes=frozenset(classes),
                        relations=tuple(relations),
                        source=f"{module_dir}:{'+'.join(modules)}")
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / f"{name}.jsonld").write_text(
            json.dumps(ontology.as_jsonld(), indent=2, ensure_ascii=False) + "\n")
    return ontology
