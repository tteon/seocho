"""Import external schema formats into a draft SEOCHO ontology document.

The adoption funnel's first step: a user arrives with a schema that already
exists somewhere — an Arrows.app sketch, the Cypher DDL of a live database,
a native document from another workspace — and leaves with a reviewable
draft. Two rules shape everything here:

1. **Import never persists.** The result is a document plus warnings; the
   user reads both and then explicitly runs ``ontology check`` / creates a
   file. An importer that silently writes is how a mis-detected format
   becomes a corrupted ontology.
2. **Warnings carry the lossy parts.** Every place a source format says less
   than the native document needs (DDL knows a relationship's type but not
   its endpoints; a diagram value's type is a guess), the gap is stated in a
   warning instead of silently defaulted — the draft is a starting point,
   and the warnings are its review checklist.

Formats (P1 slice): ``arrows`` (Arrows.app JSON), ``cypher`` (DDL —
CREATE CONSTRAINT / CREATE INDEX statements), ``native`` (this library's
own JSON/YAML), ``auto`` (content sniffing). GraphQL SDL / LinkML /
Data Importer are detected and reported as not-yet-supported rather than
mis-parsed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .ontology import NodeDef, Ontology, P, RelDef

SUPPORTED_FORMATS = ("arrows", "cypher", "native", "auto")
DETECT_ONLY_FORMATS = ("graphql", "linkml", "data-importer")


@dataclass(slots=True)
class OntologyImportResult:
    """A non-persisted draft: nothing is saved until the user acts on it."""

    document: Optional[Dict[str, Any]] = None
    warnings: List[str] = field(default_factory=list)
    detected_format: Optional[str] = None
    suggested_name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document": self.document,
            "warnings": list(self.warnings),
            "detected_format": self.detected_format,
            "suggested_name": self.suggested_name,
        }


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

_CYPHER_DDL = re.compile(
    r"\bCREATE\s+(?:(?:RANGE|TEXT|POINT|FULLTEXT|VECTOR)\s+)?(CONSTRAINT|INDEX)\b", re.I)
_GRAPHQL = re.compile(r"\btype\s+\w+\s*(@\w+[^{]*)?\{", re.M)
_LINKML = re.compile(r"^\s*(classes|slots|prefixes)\s*:", re.M)


def detect_format(content: str) -> Optional[str]:
    stripped = content.strip()
    if not stripped:
        return None
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            return None
        if isinstance(payload, dict):
            rels = payload.get("relationships")
            if (isinstance(payload.get("nodes"), list) and isinstance(rels, list)
                    and any("fromId" in r for r in rels if isinstance(r, dict))):
                return "arrows"
            if isinstance(payload.get("dataModel"), dict) or "graphSchema" in payload:
                return "data-importer"
            if isinstance(payload.get("nodes"), dict) or "graph_type" in payload:
                return "native"
        return None
    if _CYPHER_DDL.search(stripped):
        return "cypher"
    if _GRAPHQL.search(stripped):
        return "graphql"
    if _LINKML.search(stripped):
        return "linkml"
    # YAML native is the remaining text format we own.
    try:
        import yaml

        payload = yaml.safe_load(stripped)
    except Exception:
        return None
    if isinstance(payload, dict) and ("nodes" in payload or "graph_type" in payload):
        return "native"
    return None


# ---------------------------------------------------------------------------
# Arrows.app — schema inference from a diagram
# ---------------------------------------------------------------------------

def _infer_ptype(values: List[Any]) -> type:
    """Best-effort scalar type from observed diagram values (all strings in
    Arrows exports); ambiguity stays STRING — widening later is cheap,
    narrowing a wrong INTEGER is not."""
    seen = set()
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        try:
            int(text)
            seen.add(int)
            continue
        except ValueError:
            pass
        try:
            float(text)
            seen.add(float)
            continue
        except ValueError:
            pass
        return str
    if seen == {int}:
        return int
    if seen <= {int, float} and seen:
        return float
    return str


def import_arrows(content: str) -> OntologyImportResult:
    result = OntologyImportResult(detected_format="arrows")
    payload = json.loads(content)
    nodes = payload.get("nodes") or []
    relationships = payload.get("relationships") or []

    label_of: Dict[str, str] = {}
    props_by_label: Dict[str, Dict[str, List[Any]]] = {}
    for node in nodes:
        labels = node.get("labels") or []
        if not labels:
            result.warnings.append(
                f"node {node.get('id')!r} has no label; skipped — a schema entry "
                f"needs a label to exist")
            continue
        label = labels[0]
        if len(labels) > 1:
            result.warnings.append(
                f"node {node.get('id')!r} carries labels {labels}; using {label!r} "
                f"(multi-label nodes have no native equivalent)")
        label_of[str(node.get("id"))] = label
        bucket = props_by_label.setdefault(label, {})
        for key, value in (node.get("properties") or {}).items():
            bucket.setdefault(key, []).append(value)

    node_defs = {
        label: NodeDef(properties={
            key: P(_infer_ptype(values)) for key, values in sorted(props.items())
        })
        for label, props in sorted(props_by_label.items())
    }

    rel_defs: Dict[str, RelDef] = {}
    for rel in relationships:
        rtype = (rel.get("type") or "").strip().upper().replace(" ", "_")
        if not rtype:
            result.warnings.append(
                f"relationship {rel.get('id')!r} has no type; skipped")
            continue
        source = label_of.get(str(rel.get("fromId")))
        target = label_of.get(str(rel.get("toId")))
        if source is None or target is None:
            result.warnings.append(
                f"relationship {rtype}: endpoint node missing/unlabeled; "
                f"endpoints left as 'Any' — edit before use")
            source, target = source or "Any", target or "Any"
        existing = rel_defs.get(rtype)
        if existing and (existing.source, existing.target) != (source, target):
            result.warnings.append(
                f"relationship {rtype} appears with endpoints "
                f"({existing.source}->{existing.target}) and ({source}->{target}); "
                f"kept the first — split the type if both are real")
            continue
        rel_defs[rtype] = RelDef(source=source, target=target)

    title = (payload.get("title") or "imported_arrows").strip() or "imported_arrows"
    name = re.sub(r"[^a-z0-9_]+", "_", title.lower()).strip("_") or "imported_arrows"
    onto = Ontology(name=name, graph_model="lpg", nodes=node_defs,
                    relationships=rel_defs)
    result.document = onto.to_dict()
    result.suggested_name = name
    result.warnings.append(
        "property types were inferred from diagram values; review before "
        "trusting UNIQUE/typing decisions downstream")
    return result


# ---------------------------------------------------------------------------
# Cypher DDL — constraints and indexes from a live database's schema dump
# ---------------------------------------------------------------------------

_NODE_CONSTRAINT = re.compile(
    r"CREATE\s+CONSTRAINT(?:\s+\S+)?(?:\s+IF\s+NOT\s+EXISTS)?\s+FOR\s*"
    r"\(\s*\w+\s*:\s*`?(\w+)`?\s*\)\s*REQUIRE\s*\(?\s*\w+\.(?:`)?(\w+)(?:`)?\s*\)?\s*"
    r"IS\s+(UNIQUE|NOT\s+NULL|NODE\s+KEY)", re.I)
_REL_CONSTRAINT = re.compile(
    r"CREATE\s+CONSTRAINT(?:\s+\S+)?(?:\s+IF\s+NOT\s+EXISTS)?\s+FOR\s*"
    r"\(\s*\)\s*-\s*\[\s*\w+\s*:\s*`?(\w+)`?\s*\]\s*-\s*\(\s*\)\s*"
    r"REQUIRE\s*\(?\s*\w+\.(?:`)?(\w+)(?:`)?", re.I)
_INDEX = re.compile(
    r"CREATE\s+(?:RANGE\s+|TEXT\s+|POINT\s+)?INDEX(?:\s+\S+)?(?:\s+IF\s+NOT\s+EXISTS)?"
    r"\s+FOR\s*\(\s*\w+\s*:\s*`?(\w+)`?\s*\)\s*ON\s*\(([^)]+)\)", re.I)


def import_cypher_ddl(content: str) -> OntologyImportResult:
    result = OntologyImportResult(detected_format="cypher")
    node_props: Dict[str, Dict[str, P]] = {}

    for label, prop, kind in _NODE_CONSTRAINT.findall(content):
        bucket = node_props.setdefault(label, {})
        kind_upper = re.sub(r"\s+", " ", kind.upper())
        unique = kind_upper in ("UNIQUE", "NODE KEY")
        required = kind_upper in ("NOT NULL", "NODE KEY")
        prior = bucket.get(prop)
        bucket[prop] = P(str,
                         unique=unique or bool(prior and prior.unique),
                         required=required or bool(prior and prior.required))

    for label, props in _INDEX.findall(content):
        bucket = node_props.setdefault(label, {})
        for raw in props.split(","):
            prop = raw.strip().split(".")[-1].strip("` ")
            if prop and prop not in bucket:
                bucket[prop] = P(str, index=True)
            elif prop:
                existing = bucket[prop]
                bucket[prop] = P(str, unique=existing.unique,
                                 required=existing.required, index=True)

    rel_defs: Dict[str, RelDef] = {}
    for rtype, prop in _REL_CONSTRAINT.findall(content):
        rel_defs.setdefault(rtype, RelDef(source="Any", target="Any",
                                          properties={prop: P(str)}))
        result.warnings.append(
            f"relationship {rtype}: DDL declares its existence but not its "
            f"endpoints; source/target set to 'Any' — edit before use")

    if not node_props and not rel_defs:
        result.warnings.append(
            "no CREATE CONSTRAINT / CREATE INDEX statements recognized; "
            "nothing to import")
        return result

    onto = Ontology(
        name="imported_cypher", graph_model="lpg",
        nodes={label: NodeDef(properties=props)
               for label, props in sorted(node_props.items())},
        relationships=rel_defs)
    result.document = onto.to_dict()
    result.suggested_name = "imported_cypher"
    result.warnings.append(
        "DDL carries constraints, not value types: every property defaulted to "
        "STRING — set real types before strict-mode use")
    result.warnings.append(
        "relationships that exist without constraints are invisible to DDL; "
        "expect this draft to under-declare the edge set")
    return result


# ---------------------------------------------------------------------------
# Native — this library's own document, validated by round-trip
# ---------------------------------------------------------------------------

def import_native(content: str) -> OntologyImportResult:
    result = OntologyImportResult(detected_format="native")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        import yaml

        payload = yaml.safe_load(content)
    if not isinstance(payload, dict):
        result.warnings.append("native document must be a mapping")
        return result
    onto = Ontology.from_dict(payload)  # validation is the round-trip
    result.document = onto.to_dict()
    result.suggested_name = getattr(onto, "name", None) or payload.get("graph_type")
    return result


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_IMPORTERS = {
    "arrows": import_arrows,
    "cypher": import_cypher_ddl,
    "native": import_native,
}


def import_document(content: str, *, format: str = "auto") -> OntologyImportResult:
    """Convert external schema content into a draft document. Never persists."""
    chosen = (format or "auto").strip().lower()
    if chosen in ("yaml", "yml", "json"):
        chosen = "native"
    if chosen == "auto":
        detected = detect_format(content)
        if detected is None:
            return OntologyImportResult(warnings=[
                "could not detect the format; pass --format explicitly "
                f"(supported: {', '.join(f for f in SUPPORTED_FORMATS if f != 'auto')})"])
        chosen = detected
    if chosen in DETECT_ONLY_FORMATS:
        return OntologyImportResult(detected_format=chosen, warnings=[
            f"{chosen} detected but not yet supported; convert it manually or "
            f"track seocho-5bg for converter coverage"])
    importer = _IMPORTERS.get(chosen)
    if importer is None:
        return OntologyImportResult(warnings=[
            f"unknown format {format!r}; supported: "
            f"{', '.join(SUPPORTED_FORMATS + DETECT_ONLY_FORMATS)}"])
    return importer(content)


__all__ = [
    "OntologyImportResult",
    "SUPPORTED_FORMATS",
    "detect_format",
    "import_arrows",
    "import_cypher_ddl",
    "import_document",
    "import_native",
]
