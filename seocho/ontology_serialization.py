from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional, Union

if TYPE_CHECKING:
    from .ontology import Ontology


def ontology_from_jsonld_path(
    ontology_cls: type["Ontology"],
    path: Union[str, Path],
) -> "Ontology":
    with open(path, "r") as fh:
        data = json.load(fh)
    return ontology_from_jsonld_dict(ontology_cls, data)


def ontology_from_jsonld_dict(
    ontology_cls: type["Ontology"],
    data: Dict[str, Any],
) -> "Ontology":
    from .ontology import NodeDef, P, PropertyType, RelDef

    nodes: Dict[str, NodeDef] = {}
    for label, nd in (data.get("nodes") or {}).items():
        props: Dict[str, P] = {}
        for pname, pd in (nd.get("properties") or {}).items():
            if isinstance(pd, dict):
                raw_type = pd.get("type", "string").upper()
                ptype = (
                    PropertyType[raw_type]
                    if raw_type in PropertyType.__members__
                    else PropertyType.STRING
                )
                props[pname] = P(
                    type=ptype,
                    unique=pd.get("unique", False),
                    index=pd.get("index", False),
                    required=pd.get("required", False),
                    description=pd.get("description", ""),
                    aliases=pd.get("aliases", []),
                )
            else:
                raw = str(pd).upper()
                ptype = (
                    PropertyType[raw]
                    if raw in PropertyType.__members__
                    else PropertyType.STRING
                )
                props[pname] = P(type=ptype)

        nodes[label] = NodeDef(
            description=nd.get("description", ""),
            properties=props,
            aliases=nd.get("aliases", []),
            broader=nd.get("broader", []),
            same_as=nd.get("sameAs") or nd.get("same_as"),
        )

    rels: Dict[str, RelDef] = {}
    for rtype, rd in (data.get("relationships") or {}).items():
        rprops: Dict[str, P] = {}
        for pname, pd in (rd.get("properties") or {}).items():
            if isinstance(pd, dict):
                raw_type = pd.get("type", "string").upper()
                ptype = (
                    PropertyType[raw_type]
                    if raw_type in PropertyType.__members__
                    else PropertyType.STRING
                )
                rprops[pname] = P(type=ptype, description=pd.get("description", ""))
            else:
                raw = str(pd).upper()
                ptype = (
                    PropertyType[raw]
                    if raw in PropertyType.__members__
                    else PropertyType.STRING
                )
                rprops[pname] = P(type=ptype)

        rels[rtype] = RelDef(
            source=rd.get("source", "Any"),
            target=rd.get("target", "Any"),
            description=rd.get("description", ""),
            cardinality=rd.get("cardinality", "MANY_TO_MANY"),
            properties=rprops,
            aliases=rd.get("aliases", []),
            same_as=rd.get("sameAs") or rd.get("same_as"),
        )

    return ontology_cls(
        name=data.get("name") or data.get("graph_type") or "Unnamed",
        package_id=(
            data.get("package_id", "")
            or data.get("packageId", "")
            or data.get("name")
            or data.get("graph_type")
            or "Unnamed"
        ),
        version=data.get("version", "1.0.0"),
        description=data.get("description", ""),
        graph_model=data.get("graph_model", "lpg"),
        namespace=data.get("namespace", ""),
        nodes=nodes,
        relationships=rels,
    )


def ontology_to_jsonld(
    ontology: "Ontology",
    path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    type_to_jsonld: Dict[str, str] = {
        "STRING": "string",
        "INTEGER": "integer",
        "FLOAT": "float",
        "BOOLEAN": "boolean",
        "DATETIME": "dateTime",
        "DATE": "date",
        "POINT": "point",
        "LIST": "list",
    }

    nodes_out: Dict[str, Any] = {}
    for label, nd in ontology.nodes.items():
        node_entry: Dict[str, Any] = {"@type": "seocho:NodeType"}
        if nd.description:
            node_entry["description"] = nd.description
        if nd.same_as:
            node_entry["sameAs"] = nd.same_as
        if nd.aliases:
            node_entry["aliases"] = list(nd.aliases)
        if nd.broader:
            node_entry["broader"] = list(nd.broader)

        props_out: Dict[str, Any] = {}
        for pname, prop in nd.properties.items():
            pentry: Dict[str, Any] = {
                "type": type_to_jsonld.get(prop.property_type.value, "string"),
            }
            if prop.unique:
                pentry["unique"] = True
            if prop.index:
                pentry["index"] = True
            if prop.required:
                pentry["required"] = True
            if prop.description:
                pentry["description"] = prop.description
            if prop.aliases:
                pentry["aliases"] = list(prop.aliases)
            props_out[pname] = pentry
        if props_out:
            node_entry["properties"] = props_out
        nodes_out[label] = node_entry

    rels_out: Dict[str, Any] = {}
    for rtype, rd in ontology.relationships.items():
        rel_entry: Dict[str, Any] = {
            "source": rd.source,
            "target": rd.target,
        }
        if rd.description:
            rel_entry["description"] = rd.description
        if rd.cardinality != "MANY_TO_MANY":
            rel_entry["cardinality"] = rd.cardinality
        if rd.same_as:
            rel_entry["sameAs"] = rd.same_as
        if rd.aliases:
            rel_entry["aliases"] = list(rd.aliases)
        if rd.properties:
            props: Dict[str, Any] = {}
            for pname, prop in rd.properties.items():
                props[pname] = {
                    "type": type_to_jsonld.get(prop.property_type.value, "string")
                }
                if prop.description:
                    props[pname]["description"] = prop.description
            rel_entry["properties"] = props
        rels_out[rtype] = rel_entry

    doc: Dict[str, Any] = {
        "@context": dict(ontology.JSONLD_CONTEXT),
        "@id": f"seocho:{ontology.name}",
        "@type": "seocho:Ontology",
        "name": ontology.name,
        "packageId": ontology.package_id,
        "version": ontology.version,
    }
    if ontology.description:
        doc["description"] = ontology.description
    doc["nodes"] = nodes_out
    doc["relationships"] = rels_out

    if path is not None:
        with open(path, "w") as fh:
            json.dump(doc, fh, indent=2, ensure_ascii=False)

    return doc


# ---------------------------------------------------------------------------
# Turtle (TTL / OWL) — published format for FIBO and most public ontologies
# ---------------------------------------------------------------------------

import re as _re

_TTL_LOCAL_RE = _re.compile(r"[^A-Za-z0-9_]")


def _safe_local(uri: str, *, fallback: str = "Entity") -> str:
    """Take the last fragment / path segment of a URI as a Python-friendly name."""
    text = str(uri)
    if "#" in text:
        local = text.rsplit("#", 1)[-1]
    elif "/" in text:
        local = text.rstrip("/").rsplit("/", 1)[-1]
    else:
        local = text
    cleaned = _TTL_LOCAL_RE.sub("_", local).strip("_")
    if not cleaned:
        return fallback
    if cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return cleaned


def _xsd_to_propertytype(xsd_iri: Any, property_type_cls: Any) -> Any:
    iri = str(xsd_iri).lower()
    if "integer" in iri or iri.endswith("int") or iri.endswith("long"):
        return property_type_cls.INTEGER
    if "float" in iri or "double" in iri or "decimal" in iri:
        return property_type_cls.FLOAT
    if "boolean" in iri:
        return property_type_cls.BOOLEAN
    if "datetime" in iri or "date" in iri:
        return property_type_cls.DATETIME
    return property_type_cls.STRING


def ontology_from_ttl(
    ontology_cls: type["Ontology"],
    path: Union[str, Path],
    *,
    name: Optional[str] = None,
    namespace: Optional[str] = None,
) -> "Ontology":
    """Load an OWL/Turtle file into a seocho ``Ontology``.

    Maps:
    - ``owl:Class`` -> ``NodeDef`` keyed by the URI's local name
    - ``owl:ObjectProperty`` with ``rdfs:domain``/``rdfs:range`` -> ``RelDef``
    - ``owl:DatatypeProperty`` with ``rdfs:domain`` -> property on that class
    - ``skos:altLabel`` -> aliases on the corresponding NodeDef / RelDef
    - ``rdfs:label`` -> description (when present)

    Requires ``rdflib`` (ships in the ``seocho[ontology]`` extra).
    """
    try:
        import rdflib
        from rdflib import OWL, RDF, RDFS, SKOS, URIRef
    except ImportError as exc:
        raise ImportError(
            "Ontology.from_ttl requires 'rdflib'. "
            "Install it with: pip install seocho[ontology]"
        ) from exc

    from .ontology import NodeDef, P, PropertyType, RelDef

    g = rdflib.Graph()
    g.parse(str(path), format="turtle")

    if namespace is None:
        ns_map = dict(g.namespaces())
        namespace = str(ns_map.get("priv") or next(iter(ns_map.values()), ""))

    nodes: Dict[str, NodeDef] = {}
    relationships: Dict[str, RelDef] = {}

    for cls_uri in g.subjects(RDF.type, OWL.Class):
        if not isinstance(cls_uri, URIRef):
            continue
        local = _safe_local(cls_uri)
        label = next(g.objects(cls_uri, RDFS.label), None)
        aliases = [str(a) for a in g.objects(cls_uri, SKOS.altLabel)]
        nodes[local] = NodeDef(
            description=str(label) if label is not None else "",
            properties={},
            aliases=aliases,
            same_as=str(cls_uri),
        )

    for prop_uri in g.subjects(RDF.type, OWL.DatatypeProperty):
        domain = next(g.objects(prop_uri, RDFS.domain), None)
        rng = next(g.objects(prop_uri, RDFS.range), None)
        local_prop = _safe_local(prop_uri)
        ptype = _xsd_to_propertytype(rng, PropertyType) if rng is not None else PropertyType.STRING
        if domain is None:
            continue
        domain_local = _safe_local(domain)
        if domain_local not in nodes:
            nodes[domain_local] = NodeDef(
                description="", properties={}, aliases=[], same_as=str(domain),
            )
        nodes[domain_local].properties[local_prop] = P(
            type=ptype,
            description=str(next(g.objects(prop_uri, RDFS.label), "") or ""),
        )

    for prop_uri in g.subjects(RDF.type, OWL.ObjectProperty):
        domain = next(g.objects(prop_uri, RDFS.domain), None)
        rng = next(g.objects(prop_uri, RDFS.range), None)
        local = _safe_local(prop_uri)
        relationships[local] = RelDef(
            source=_safe_local(domain) if domain is not None else "Any",
            target=_safe_local(rng) if rng is not None else "Any",
            description=str(next(g.objects(prop_uri, RDFS.label), "") or ""),
            cardinality="MANY_TO_MANY",
            properties={},
            aliases=[str(a) for a in g.objects(prop_uri, SKOS.altLabel)],
            same_as=str(prop_uri),
        )

    # Default a `name` property where missing so seocho writes don't choke.
    for node in nodes.values():
        if "name" not in node.properties:
            node.properties["name"] = P(type=PropertyType.STRING, unique=True, description="")

    chosen_name = name or _safe_local(str(path).rsplit("/", 1)[-1].rsplit(".", 1)[0])
    return ontology_cls(
        name=chosen_name,
        package_id=chosen_name,
        version="1.0.0",
        description=f"Loaded from {path}",
        graph_model="rdf",
        namespace=namespace or "",
        nodes=nodes,
        relationships=relationships,
    )


def ontology_to_ttl(
    ontology: "Ontology",
    path: Union[str, Path],
) -> Path:
    """Write an ``Ontology`` out as Turtle.

    Inverse of :func:`ontology_from_ttl` for the OWL subset we map.
    Requires ``rdflib``.
    """
    try:
        import rdflib
        from rdflib import Literal, Namespace, OWL, RDF, RDFS, SKOS, URIRef, XSD
    except ImportError as exc:
        raise ImportError(
            "Ontology.to_ttl requires 'rdflib'. "
            "Install it with: pip install seocho[ontology]"
        ) from exc

    from .ontology import PropertyType

    ns_str = ontology.namespace or "https://seocho.dev/ontology/"
    if not (ns_str.endswith("/") or ns_str.endswith("#")):
        ns_str = ns_str + "/"
    ns = Namespace(ns_str)

    g = rdflib.Graph()
    g.bind("owl", OWL)
    g.bind("rdfs", RDFS)
    g.bind("skos", SKOS)
    g.bind("priv", ns)

    onto_iri = URIRef(ns + _safe_local(ontology.name))
    g.add((onto_iri, RDF.type, OWL.Ontology))
    if ontology.description:
        g.add((onto_iri, RDFS.label, Literal(ontology.description)))

    xsd_for = {
        PropertyType.STRING: XSD.string,
        PropertyType.INTEGER: XSD.integer,
        PropertyType.FLOAT: XSD.decimal,
        PropertyType.BOOLEAN: XSD.boolean,
        PropertyType.DATETIME: XSD.dateTime,
    }

    for label, node in ontology.nodes.items():
        cls_iri = URIRef(node.same_as) if node.same_as else URIRef(ns + _safe_local(label))
        g.add((cls_iri, RDF.type, OWL.Class))
        if node.description:
            g.add((cls_iri, RDFS.label, Literal(node.description)))
        for alias in node.aliases or []:
            g.add((cls_iri, SKOS.altLabel, Literal(alias)))
        for pname, p in node.properties.items():
            prop_iri = URIRef(ns + _safe_local(pname))
            g.add((prop_iri, RDF.type, OWL.DatatypeProperty))
            g.add((prop_iri, RDFS.domain, cls_iri))
            g.add((prop_iri, RDFS.range, xsd_for.get(p.property_type, XSD.string)))
            if p.description:
                g.add((prop_iri, RDFS.label, Literal(p.description)))

    for rtype, rel in ontology.relationships.items():
        prop_iri = URIRef(rel.same_as) if rel.same_as else URIRef(ns + _safe_local(rtype))
        g.add((prop_iri, RDF.type, OWL.ObjectProperty))
        if rel.description:
            g.add((prop_iri, RDFS.label, Literal(rel.description)))
        if rel.source and rel.source != "Any":
            g.add((prop_iri, RDFS.domain, URIRef(ns + _safe_local(rel.source))))
        if rel.target and rel.target != "Any":
            g.add((prop_iri, RDFS.range, URIRef(ns + _safe_local(rel.target))))
        for alias in rel.aliases or []:
            g.add((prop_iri, SKOS.altLabel, Literal(alias)))

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    g.serialize(destination=str(out), format="turtle")
    return out


def ontology_subtract(left: "Ontology", right: "Ontology") -> "Ontology":
    """Return a copy of ``left`` with everything declared in ``right`` removed.

    Removes:
    - any node label present in ``right.nodes``
    - any relationship type present in ``right.relationships``
    - any relationship whose ``source`` or ``target`` references a removed node
    """
    from copy import deepcopy

    result = deepcopy(left)
    drop_labels = set(right.nodes.keys())
    drop_rels = set(right.relationships.keys())

    for label in drop_labels:
        result.nodes.pop(label, None)
        result._allowed_labels.discard(label)
    for rtype in drop_rels:
        result.relationships.pop(rtype, None)
    for rtype, reldef in list(result.relationships.items()):
        if reldef.source in drop_labels or reldef.target in drop_labels:
            result.relationships.pop(rtype, None)
    result.name = f"{left.name}-minus-{right.name}"
    result.package_id = result.name
    return result
