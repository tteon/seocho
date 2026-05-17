"""PyPI-vs-dev compatibility helpers for teaching-resource notebooks.

Some symbols added in the local development branch of ``seocho`` have not
shipped to PyPI yet (currently 0.3.2). This module provides drop-in shims
so the chapter notebooks run unchanged against either:

- a fresh ``pip install seocho`` (PyPI 0.3.2)
- ``pip install -e /path/to/seocho`` (local dev / latest features)

Exports
-------

``slice_ontology(ontology, intent, *, expand_neighbours=True)``
    Same signature and ``OntologySlice`` return shape as
    ``seocho.ontology_slice.slice_ontology``. Uses the real implementation
    when present; otherwise falls back to an inline copy.

``slice_summary(ontology, sl) -> dict``
    Convert an ``OntologySlice`` into a printable / prompt-ready dict:
    ``{"classes": [{"name", "description"}], "relationships": [...]}``. Useful
    in notebook prompts where we'd previously read ``slice.classes[0].name``
    (which the actual SDK API never had).

``format_ontology_block(ontology, sl, *, max_classes=10) -> str``
    Render the slice as a "CLASSES: ... \\n RELATIONSHIPS: ..." string for
    extraction prompt Block 1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


# ---------------------------------------------------------------------------
# slice_ontology  —  use SDK when available, otherwise inline
# ---------------------------------------------------------------------------


try:  # PyPI 0.3.2 ships without this module; local dev does
    from seocho.ontology_slice import (  # type: ignore
        OntologySlice,
        slice_ontology,
    )

except ImportError:  # pragma: no cover — fallback path

    @dataclass
    class OntologySlice:  # type: ignore[no-redef]
        matched_labels: Set[str] = field(default_factory=set)
        related_labels: Set[str] = field(default_factory=set)
        matched_relationships: Set[str] = field(default_factory=set)
        intent_terms: List[str] = field(default_factory=list)
        fallback_to_full: bool = False

        @property
        def all_labels(self) -> Set[str]:
            return self.matched_labels | self.related_labels

        def to_dict(self) -> Dict[str, Any]:
            return {
                "matched_labels": sorted(self.matched_labels),
                "related_labels": sorted(self.related_labels),
                "matched_relationships": sorted(self.matched_relationships),
                "intent_terms": list(self.intent_terms),
                "fallback_to_full": self.fallback_to_full,
            }

    def _tokenize_intent(intent: str) -> List[str]:
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9_]+", (intent or "").lower())
        return [t for t in tokens if len(t) >= 3]

    def _label_matches_term(label: str, term: str) -> bool:
        if term in label.lower():
            return True
        parts = re.findall(r"[A-Z][a-z]*", label)
        return any(term == p.lower() for p in parts)

    def slice_ontology(  # type: ignore[no-redef]
        ontology: Any,
        intent: str,
        *,
        expand_neighbours: bool = True,
    ) -> OntologySlice:
        sl = OntologySlice()
        tokens = _tokenize_intent(intent)
        sl.intent_terms = list(tokens)
        if not tokens:
            sl.fallback_to_full = True
            return sl

        nodes = getattr(ontology, "nodes", {}) or {}
        rels = getattr(ontology, "relationships", {}) or {}

        for label in nodes.keys():
            if any(_label_matches_term(label, t) for t in tokens):
                sl.matched_labels.add(label)

        for rtype in rels.keys():
            if any(_label_matches_term(rtype, t) for t in tokens):
                sl.matched_relationships.add(rtype)

        if expand_neighbours and sl.matched_labels:
            for rtype, rel in rels.items():
                src = getattr(rel, "source", None)
                tgt = getattr(rel, "target", None)
                if not (src and tgt):
                    continue
                if src in sl.matched_labels or tgt in sl.matched_labels:
                    sl.matched_relationships.add(rtype)
                    if src not in sl.matched_labels:
                        sl.related_labels.add(src)
                    if tgt not in sl.matched_labels:
                        sl.related_labels.add(tgt)

        if not sl.matched_labels and not sl.matched_relationships:
            sl.fallback_to_full = True
        return sl


# ---------------------------------------------------------------------------
# Convenience: convert OntologySlice → printable / prompt-ready structures
# ---------------------------------------------------------------------------


def _node_record(ontology: Any, label: str) -> Dict[str, str]:
    """Return ``{name, description}`` for the labelled class in *ontology*."""
    nodes = getattr(ontology, "nodes", {}) or {}
    node = nodes.get(label)
    name = label
    description = ""
    if node is not None:
        description = (
            getattr(node, "description", None)
            or getattr(node, "comment", None)
            or ""
        )
    return {"name": name, "description": str(description).strip()}


def _rel_record(ontology: Any, rtype: str) -> Dict[str, str]:
    rels = getattr(ontology, "relationships", {}) or {}
    rel = rels.get(rtype)
    src = getattr(rel, "source", "?") if rel else "?"
    tgt = getattr(rel, "target", "?") if rel else "?"
    desc = getattr(rel, "description", "") if rel else ""
    return {"name": rtype, "source": str(src), "target": str(tgt), "description": str(desc).strip()}


def slice_summary(ontology: Any, sl: "OntologySlice") -> Dict[str, List[Dict[str, str]]]:
    """Return a JSON-able view of a slice.

    Falls back to the full ontology when ``sl.fallback_to_full`` is True.
    """
    if sl.fallback_to_full:
        nodes = getattr(ontology, "nodes", {}) or {}
        rels = getattr(ontology, "relationships", {}) or {}
        labels: List[str] = list(nodes.keys())
        rel_types: List[str] = list(rels.keys())
    else:
        labels = sorted(sl.all_labels)
        rel_types = sorted(sl.matched_relationships)

    return {
        "classes": [_node_record(ontology, lbl) for lbl in labels],
        "relationships": [_rel_record(ontology, r) for r in rel_types],
        "fallback_to_full": bool(sl.fallback_to_full),
    }


def format_ontology_block(
    ontology: Any,
    sl: "OntologySlice",
    *,
    max_classes: int = 10,
    max_relationships: int = 12,
) -> str:
    """Render the slice as the "Block 1" prompt fragment used in ch01/ch03.

    Output shape::

        CLASSES (use ONLY these labels):
          - Company: A legal entity...
          - Risk: ...
        RELATIONSHIPS:
          - HAS_RISK: (Company) -> (Risk)
    """
    summary = slice_summary(ontology, sl)
    cls_lines = [
        f"  - {c['name']}: {(c['description'] or '').strip()[:80]}"
        for c in summary["classes"][:max_classes]
    ]
    rel_lines = [
        f"  - {r['name']}: ({r['source']}) -> ({r['target']})"
        for r in summary["relationships"][:max_relationships]
    ]
    body = "CLASSES (use ONLY these labels):\n" + "\n".join(cls_lines)
    if rel_lines:
        body += "\n\nRELATIONSHIPS:\n" + "\n".join(rel_lines)
    return body


__all__ = [
    "OntologySlice",
    "slice_ontology",
    "slice_summary",
    "format_ontology_block",
    "load_ontology",
    "load_ontology_from_ttl",
]


# ---------------------------------------------------------------------------
# Ontology loading — TTL fallback (seocho.Ontology only ships YAML/JSON-LD)
# ---------------------------------------------------------------------------


def load_ontology_from_ttl(path: str | Any) -> Any:
    """Load an OWL/SKOS TTL file into a ``seocho.ontology.Ontology``.

    ``seocho.Ontology`` does not expose a TTL loader (only YAML / JSON-LD /
    dict / artifact). This helper parses the Turtle with ``rdflib``, extracts
    classes (``owl:Class``), object properties (``owl:ObjectProperty``) with
    domain/range, and constructs an in-memory Ontology via
    :py:meth:`seocho.ontology.Ontology.from_dict`.

    Only ``rdfs:label`` / ``rdfs:comment`` / ``skos:definition`` /
    ``rdfs:domain`` / ``rdfs:range`` are read. Subclass hierarchy and most
    annotations are dropped — that's enough for the teaching prompts (which
    only need name + description + relationship endpoints).
    """
    try:
        import rdflib
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "load_ontology_from_ttl requires rdflib. Install via `pip install rdflib`."
        ) from exc

    from seocho.ontology import Ontology

    g = rdflib.Graph()
    g.parse(str(path), format="turtle")

    RDF = rdflib.namespace.RDF
    RDFS = rdflib.namespace.RDFS
    OWL = rdflib.namespace.OWL
    SKOS = rdflib.Namespace("http://www.w3.org/2004/02/skos/core#")

    def _local(uri: Any) -> str:
        s = str(uri)
        return s.rsplit("#", 1)[-1].rsplit("/", 1)[-1]

    def _label(uri: Any) -> str:
        for lit in g.objects(uri, RDFS.label):
            return str(lit)
        return _local(uri)

    def _description(uri: Any) -> str:
        for lit in g.objects(uri, SKOS.definition):
            return str(lit)
        for lit in g.objects(uri, RDFS.comment):
            return str(lit)
        return ""

    # Collect classes — nodes is a dict keyed by class name (seocho contract)
    nodes: Dict[str, Dict[str, Any]] = {}
    for cls in set(g.subjects(RDF.type, OWL.Class)) | set(g.subjects(RDF.type, RDFS.Class)):
        if isinstance(cls, rdflib.BNode):
            continue
        name = _local(cls)
        nodes.setdefault(
            name,
            {
                "description": _description(cls),
                "properties": {},
                "aliases": [_label(cls)] if _label(cls) != name else [],
            },
        )

    # Collect object properties with domain/range — keyed by relation name
    relationships: Dict[str, Dict[str, Any]] = {}
    for prop in set(g.subjects(RDF.type, OWL.ObjectProperty)):
        domains = [_local(d) for d in g.objects(prop, RDFS.domain) if not isinstance(d, rdflib.BNode)]
        ranges = [_local(r) for r in g.objects(prop, RDFS.range) if not isinstance(r, rdflib.BNode)]
        if not (domains and ranges):
            continue
        rname = _local(prop)
        relationships.setdefault(
            rname,
            {
                "source": domains[0],
                "target": ranges[0],
                "description": _description(prop),
            },
        )
        for endpoint in (domains[0], ranges[0]):
            nodes.setdefault(
                endpoint,
                {"description": "", "properties": {}, "aliases": []},
            )

    ontology_dict: Dict[str, Any] = {
        "name": "fibo_be_minimal",
        "version": "1",
        "nodes": nodes,
        "relationships": relationships,
    }
    return Ontology.from_dict(ontology_dict)


def load_ontology(path: str | Any) -> Any:
    """Load an Ontology from a path — auto-detects extension.

    Supports ``.ttl`` (via rdflib + from_dict), ``.jsonld``, ``.yaml`` / ``.yml``.
    """
    s = str(path)
    if s.lower().endswith(".ttl") or s.lower().endswith(".turtle"):
        return load_ontology_from_ttl(s)
    from seocho.ontology import Ontology

    if s.lower().endswith(".jsonld") or s.lower().endswith(".json-ld"):
        return Ontology.from_jsonld(s)
    if s.lower().endswith((".yaml", ".yml")):
        return Ontology.from_yaml(s)
    raise ValueError(
        f"Unrecognized ontology file extension: {path}. "
        "Expected .ttl, .jsonld, .yaml, or .yml."
    )
