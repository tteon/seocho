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
