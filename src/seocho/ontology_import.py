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

Formats: ``arrows`` (Arrows.app JSON), ``cypher`` (DDL as SHOW INDEXES /
SHOW CONSTRAINTS dump it), ``graphql`` (SDL object types), ``linkml``
(classes/attributes; ``is_a`` becomes the native ``broader`` taxonomy),
``data-importer`` (Neo4j Data Importer model JSON, tolerantly walked),
``native`` (this library's own JSON/YAML), ``auto`` (content sniffing).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .ontology import NodeDef, Ontology, P, RelDef

SUPPORTED_FORMATS = ("arrows", "cypher", "native", "graphql", "linkml", "data-importer", "auto")
DETECT_ONLY_FORMATS: tuple = ()


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


# ---------------------------------------------------------------------------
# GraphQL SDL — object types as labels, object-typed fields as relationships
# ---------------------------------------------------------------------------

_GQL_TYPE = re.compile(r"\btype\s+(\w+)[^{]*\{([^}]*)\}", re.S)
# No line anchor: SDL allows several fields per line, and arguments in
# parentheses are stripped before matching so `field(arg: X): Y` keeps Y.
_GQL_FIELD = re.compile(r"(\w+)\s*:\s*(\[?)\s*(\w+)\s*\]?\s*(!?)")
_GQL_SCALARS = {
    "String": str, "ID": str, "Int": int, "Float": float, "Boolean": bool,
    "DateTime": "DATETIME", "Date": "DATE",
}
_GQL_ROOTS = {"Query", "Mutation", "Subscription"}


def import_graphql(content: str) -> OntologyImportResult:
    result = OntologyImportResult(detected_format="graphql")
    type_blocks = {name: body for name, body in _GQL_TYPE.findall(content)
                   if name not in _GQL_ROOTS}
    if not type_blocks:
        result.warnings.append("no object type definitions found (Query/Mutation "
                               "roots are intentionally skipped)")
        return result
    if re.search(r"\b(interface|union)\s+\w+", content):
        result.warnings.append(
            "interface/union definitions have no native equivalent; skipped — "
            "model shared fields on each concrete type")

    node_defs: Dict[str, NodeDef] = {}
    rel_defs: Dict[str, RelDef] = {}
    for name, body in type_blocks.items():
        props: Dict[str, P] = {}
        body = re.sub(r"\([^)]*\)", "", body)  # drop argument lists
        for field_name, is_list, field_type, required in _GQL_FIELD.findall(body):
            if field_type in _GQL_SCALARS:
                props[field_name] = P(_GQL_SCALARS[field_type],
                                      required=required == "!",
                                      unique=field_type == "ID")
            elif field_type in type_blocks:
                rtype = re.sub(r"(?<!^)(?=[A-Z])", "_", field_name).upper()
                existing = rel_defs.get(rtype)
                if existing and (existing.source, existing.target) != (name, field_type):
                    result.warnings.append(
                        f"relationship {rtype} (field {field_name!r}) maps to two "
                        f"endpoint pairs; kept the first — rename one field")
                    continue
                rel_defs[rtype] = RelDef(source=name, target=field_type)
                if is_list != "[":
                    result.warnings.append(
                        f"{name}.{field_name} is single-valued; cardinality "
                        f"defaulted to MANY_TO_MANY — tighten it manually")
            else:
                result.warnings.append(
                    f"{name}.{field_name}: unknown type {field_type!r} "
                    f"(enum or missing definition); property skipped")
        node_defs[name] = NodeDef(properties=props)

    onto = Ontology(name="imported_graphql", graph_model="lpg",
                    nodes=node_defs, relationships=rel_defs)
    result.document = onto.to_dict()
    result.suggested_name = "imported_graphql"
    return result


# ---------------------------------------------------------------------------
# LinkML — classes/attributes; is_a becomes the native `broader` taxonomy
# ---------------------------------------------------------------------------

_LINKML_SCALARS = {
    "string": str, "str": str, "uri": str, "uriorcurie": str,
    "integer": int, "int": int, "float": float, "double": float,
    "decimal": float, "boolean": bool, "date": "DATE", "datetime": "DATETIME",
}


def import_linkml(content: str) -> OntologyImportResult:
    import yaml

    result = OntologyImportResult(detected_format="linkml")
    payload = yaml.safe_load(content)
    if not isinstance(payload, dict) or not isinstance(payload.get("classes"), dict):
        result.warnings.append("no `classes:` mapping found; not a LinkML schema?")
        return result
    classes: Dict[str, Any] = payload["classes"]
    slot_index: Dict[str, Any] = payload.get("slots") or {}

    def norm(name: str) -> str:
        return "".join(part.capitalize() for part in re.split(r"[\s_-]+", name))

    node_defs: Dict[str, NodeDef] = {}
    rel_defs: Dict[str, RelDef] = {}
    class_names = {norm(c) for c in classes}
    for raw_name, spec in classes.items():
        spec = spec or {}
        label = norm(raw_name)
        props: Dict[str, P] = {}
        attrs: Dict[str, Any] = dict(spec.get("attributes") or {})
        for slot in spec.get("slots") or []:
            attrs.setdefault(slot, slot_index.get(slot) or {})
        for attr_name, attr_spec in attrs.items():
            attr_spec = attr_spec or {}
            rng = str(attr_spec.get("range", "string")).strip()
            if norm(rng) in class_names:
                rtype = attr_name.upper().replace("-", "_").replace(" ", "_")
                rel_defs[rtype] = RelDef(source=label, target=norm(rng))
            elif rng.lower() in _LINKML_SCALARS:
                props[attr_name] = P(
                    _LINKML_SCALARS[rng.lower()],
                    required=bool(attr_spec.get("required")),
                    unique=bool(attr_spec.get("identifier")))
            else:
                result.warnings.append(
                    f"{raw_name}.{attr_name}: range {rng!r} is neither a scalar "
                    f"nor a class in this schema; property skipped")
        broader = []
        if spec.get("is_a"):
            parent = norm(str(spec["is_a"]))
            if parent in class_names:
                broader = [parent]
            else:
                result.warnings.append(
                    f"{raw_name}: is_a {spec['is_a']!r} not defined here; "
                    f"hierarchy link dropped")
        node_defs[label] = NodeDef(properties=props, broader=broader)

    name = re.sub(r"[^a-z0-9_]+", "_", str(payload.get("name", "imported_linkml")).lower())
    onto = Ontology(name=name or "imported_linkml", graph_model="lpg",
                    nodes=node_defs, relationships=rel_defs)
    result.document = onto.to_dict()
    result.suggested_name = name or "imported_linkml"
    return result


# ---------------------------------------------------------------------------
# Neo4j Data Importer — tolerant walk over its versioned model JSON
# ---------------------------------------------------------------------------

_DI_TYPES = {"string": str, "integer": int, "float": float, "boolean": bool,
             "datetime": "DATETIME", "date": "DATE"}


def import_data_importer(content: str) -> OntologyImportResult:
    result = OntologyImportResult(detected_format="data-importer")
    payload = json.loads(content)
    model = payload.get("dataModel") or payload
    graph = (model.get("graphSchema") or model.get("graphModel") or model)

    def walk_collect(obj: Any, key_names: tuple) -> List[Dict[str, Any]]:
        found: List[Dict[str, Any]] = []
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in key_names and isinstance(value, (list, dict)):
                    found.extend(value.values() if isinstance(value, dict) else value)
                else:
                    found.extend(walk_collect(value, key_names))
        elif isinstance(obj, list):
            for item in obj:
                found.extend(walk_collect(item, key_names))
        return found

    node_specs = walk_collect(graph, ("nodeLabels", "nodeSchemas", "nodeObjectTypes"))
    rel_specs = walk_collect(graph, ("relationshipTypes", "relationshipSchemas",
                                     "relationshipObjectTypes"))
    if not node_specs:
        result.warnings.append("no node label definitions recognized in the model "
                               "JSON; the Data Importer export format may have "
                               "changed — file an issue with the file attached")
        return result

    label_by_ref: Dict[str, str] = {}
    node_defs: Dict[str, NodeDef] = {}
    for spec in node_specs:
        token = str(spec.get("token") or spec.get("label")
                    or (spec.get("labels") or [""])[0] or "").strip()
        if not token:
            continue
        if ref := spec.get("$id") or spec.get("id"):
            label_by_ref[str(ref)] = token
        props: Dict[str, P] = {}
        for prop in spec.get("properties") or []:
            pname = str(prop.get("token") or prop.get("name") or "").strip()
            ptype = str(prop.get("type", {}).get("type")
                        if isinstance(prop.get("type"), dict)
                        else prop.get("type", "string")).lower()
            if pname:
                props[pname] = P(_DI_TYPES.get(ptype, str))
        node_defs[token] = NodeDef(properties=props)

    def endpoint(spec: Dict[str, Any], side: str) -> str:
        value = spec.get(side) or spec.get(f"{side}NodeLabel") or {}
        if isinstance(value, dict):
            value = value.get("$ref") or value.get("nodeSchema") or value.get("label") or ""
        label = label_by_ref.get(str(value).lstrip("#"), str(value).lstrip("#"))
        return label if label in node_defs else "Any"

    rel_defs: Dict[str, RelDef] = {}
    for spec in rel_specs:
        token = str(spec.get("token") or spec.get("type") or "").strip()
        if not token:
            continue
        source, target = endpoint(spec, "from"), endpoint(spec, "to")
        if "Any" in (source, target):
            result.warnings.append(
                f"relationship {token}: endpoint reference not resolved; "
                f"left as 'Any' — edit before use")
        rel_defs[token] = RelDef(source=source, target=target)

    onto = Ontology(name="imported_data_importer", graph_model="lpg",
                    nodes=node_defs, relationships=rel_defs)
    result.document = onto.to_dict()
    result.suggested_name = "imported_data_importer"
    result.warnings.append(
        "Data Importer models vary by app version; verify every property type "
        "against your source data")
    return result


_IMPORTERS.update({
    "graphql": import_graphql,
    "linkml": import_linkml,
    "data-importer": import_data_importer,
})
