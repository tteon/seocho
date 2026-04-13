"""
Ontology — first-class primitive of the SEOCHO SDK.

An Ontology defines the schema that governs both knowledge-graph construction
(extraction/indexing) and querying.  It is the single source of truth that
feeds into system prompts, dynamic prompts, Cypher constraint generation,
and post-extraction validation.

Quick start::

    from seocho import Ontology, NodeDef, RelDef, P

    onto = Ontology(
        name="company_graph",
        nodes={
            "Company": NodeDef(
                description="A registered business entity",
                properties={"name": P(str, unique=True), "ticker": P(str, index=True)},
            ),
            "Person": NodeDef(
                description="An individual",
                properties={"name": P(str, unique=True), "role": P(str)},
            ),
        },
        relationships={
            "WORKS_AT": RelDef(source="Person", target="Company", cardinality="MANY_TO_ONE"),
        },
    )

Or load from YAML / JSON-LD::

    onto = Ontology.from_yaml("schema.yaml")
    onto = Ontology.from_jsonld("schema.jsonld")

Canonical storage is **JSON-LD**; SHACL shapes are derived for validation::

    onto.to_jsonld("schema.jsonld")   # persist
    shacl = onto.to_shacl()           # derive validation shapes
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Type, Union

import yaml

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_PY_TO_GRAPH_TYPE: Dict[type, str] = {
    str: "STRING",
    int: "INTEGER",
    float: "FLOAT",
    bool: "BOOLEAN",
}


class PropertyType(Enum):
    STRING = "STRING"
    INTEGER = "INTEGER"
    FLOAT = "FLOAT"
    BOOLEAN = "BOOLEAN"
    DATETIME = "DATETIME"
    DATE = "DATE"
    POINT = "POINT"
    LIST = "LIST"


class ConstraintType(Enum):
    UNIQUE = "UNIQUE"
    NODE_KEY = "NODE_KEY"
    EXISTS = "EXISTS"


class Cardinality(Enum):
    ONE_TO_ONE = "ONE_TO_ONE"
    ONE_TO_MANY = "ONE_TO_MANY"
    MANY_TO_ONE = "MANY_TO_ONE"
    MANY_TO_MANY = "MANY_TO_MANY"


# ---------------------------------------------------------------------------
# Property shorthand — ``P(str, unique=True)``
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class P:
    """Concise property definition for the builder API.

    Parameters
    ----------
    type:
        Python type (``str``, ``int``, ``float``, ``bool``) or a
        :class:`PropertyType` enum value.
    unique:
        Whether this property carries a UNIQUE constraint.
    index:
        Whether this property should have a standalone index.
    required:
        Whether the property is mandatory (maps to EXISTS constraint).
    description:
        Human-readable description shown in prompts.
    aliases:
        Alternative names an LLM might use for this property.
    """

    type: Union[type, PropertyType, str] = str
    unique: bool = False
    index: bool = False
    required: bool = False
    description: str = ""
    aliases: List[str] = field(default_factory=list)

    @property
    def property_type(self) -> PropertyType:
        if isinstance(self.type, PropertyType):
            return self.type
        if isinstance(self.type, builtins_type) and self.type in _PY_TO_GRAPH_TYPE:
            return PropertyType[_PY_TO_GRAPH_TYPE[self.type]]
        if isinstance(self.type, str):
            return PropertyType[self.type.upper()]
        return PropertyType.STRING

    @property
    def constraint(self) -> Optional[ConstraintType]:
        if self.unique:
            return ConstraintType.UNIQUE
        if self.required:
            return ConstraintType.EXISTS
        return None


# cache builtin ``type`` before it gets shadowed
builtins_type = type


# ---------------------------------------------------------------------------
# Node / Relationship definitions
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class NodeDef:
    """Definition of a node (entity) type in the ontology."""

    description: str = ""
    properties: Dict[str, P] = field(default_factory=dict)
    aliases: List[str] = field(default_factory=list)
    broader: List[str] = field(default_factory=list)
    same_as: Optional[str] = None  # e.g. "schema:Organization"

    # --- introspection helpers ------------------------------------------------

    @property
    def unique_properties(self) -> List[str]:
        return [name for name, p in self.properties.items() if p.unique]

    @property
    def indexed_properties(self) -> List[str]:
        return [name for name, p in self.properties.items() if p.index]

    @property
    def required_properties(self) -> List[str]:
        return [name for name, p in self.properties.items() if p.required or p.unique]


@dataclass(slots=True)
class RelDef:
    """Definition of a relationship type in the ontology."""

    source: str = "Any"
    target: str = "Any"
    description: str = ""
    cardinality: str = "MANY_TO_MANY"
    properties: Dict[str, P] = field(default_factory=dict)
    aliases: List[str] = field(default_factory=list)
    same_as: Optional[str] = None  # e.g. "schema:worksFor"


# ---------------------------------------------------------------------------
# Ontology
# ---------------------------------------------------------------------------


class Ontology:
    """Schema definition that drives extraction prompts, query prompts,
    graph constraints, and post-extraction validation.

    Parameters
    ----------
    name:
        Human-readable ontology name (appears in LLM prompts).
    package_id:
        Stable governance identifier for the ontology package lineage.
    version:
        Semantic version string.
    description:
        One-liner shown at the top of generated prompts.
    nodes:
        Mapping of label -> :class:`NodeDef`.
    relationships:
        Mapping of rel_type -> :class:`RelDef`.
    """

    def __init__(
        self,
        name: str,
        *,
        package_id: str = "",
        version: str = "1.0.0",
        description: str = "",
        graph_model: str = "lpg",  # "lpg", "rdf", "hybrid"
        namespace: str = "",  # RDF namespace URI (e.g. "https://schema.org/")
        nodes: Optional[Dict[str, NodeDef]] = None,
        relationships: Optional[Dict[str, RelDef]] = None,
    ) -> None:
        self.name = name
        self.package_id = package_id.strip() or name
        self.version = version
        self.description = description
        self.graph_model = graph_model
        self.namespace = namespace
        self.nodes: Dict[str, NodeDef] = dict(nodes or {})
        self.relationships: Dict[str, RelDef] = dict(relationships or {})
        self._allowed_labels: Set[str] = set(self.nodes.keys())

    # ------------------------------------------------------------------
    # Loaders
    # ------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, path: Union[str, Path]) -> "Ontology":
        """Load an ontology from a YAML file.

        Expected YAML structure::

            graph_type: financial
            version: "1.0.0"
            description: "Financial entity schema"
            nodes:
              Company:
                description: "A registered business"
                aliases: [Firm, Corp]
                properties:
                  name:
                    type: STRING
                    constraint: UNIQUE
                  ticker:
                    type: STRING
                    index: true
            relationships:
              WORKS_AT:
                source: Person
                target: Company
                cardinality: MANY_TO_ONE
                description: "Employment"
        """
        with open(path, "r") as fh:
            data = yaml.safe_load(fh)
        return cls._from_dict(data)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Ontology":
        """Build from a plain dict (e.g. JSON payload)."""
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: Dict[str, Any]) -> "Ontology":
        nodes: Dict[str, NodeDef] = {}
        for label, nd in (data.get("nodes") or {}).items():
            props: Dict[str, P] = {}
            for pname, pd in (nd.get("properties") or {}).items():
                ptype = PropertyType[pd.get("type", "STRING").upper()] if isinstance(pd, dict) else PropertyType.STRING
                constraint_str = pd.get("constraint", "").upper() if isinstance(pd, dict) else ""
                props[pname] = P(
                    type=ptype,
                    unique=constraint_str == "UNIQUE",
                    index=pd.get("index", False) if isinstance(pd, dict) else False,
                    required=pd.get("required", False) if isinstance(pd, dict) else False,
                    description=pd.get("description", "") if isinstance(pd, dict) else "",
                    aliases=pd.get("aliases", []) if isinstance(pd, dict) else [],
                )
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
                ptype = PropertyType[pd.get("type", "STRING").upper()] if isinstance(pd, dict) else PropertyType.STRING
                rprops[pname] = P(
                    type=ptype,
                    description=pd.get("description", "") if isinstance(pd, dict) else "",
                )
            rels[rtype] = RelDef(
                source=rd.get("source", "Any"),
                target=rd.get("target", "Any"),
                description=rd.get("description", ""),
                cardinality=rd.get("cardinality", "MANY_TO_MANY"),
                properties=rprops,
                aliases=rd.get("aliases", []),
                same_as=rd.get("sameAs") or rd.get("same_as"),
            )

        return cls(
            name=data.get("graph_type") or data.get("name") or "Unnamed",
            package_id=data.get("package_id", "") or data.get("packageId", "") or data.get("graph_type") or data.get("name") or "Unnamed",
            version=data.get("version", "1.0.0"),
            description=data.get("description", ""),
            graph_model=data.get("graph_model", "lpg"),
            namespace=data.get("namespace", ""),
            nodes=nodes,
            relationships=rels,
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict suitable for YAML/JSON export."""
        nodes_out: Dict[str, Any] = {}
        for label, nd in self.nodes.items():
            props_out: Dict[str, Any] = {}
            for pname, p in nd.properties.items():
                entry: Dict[str, Any] = {"type": p.property_type.value}
                if p.constraint:
                    entry["constraint"] = p.constraint.value
                if p.index:
                    entry["index"] = True
                if p.required:
                    entry["required"] = True
                if p.description:
                    entry["description"] = p.description
                if p.aliases:
                    entry["aliases"] = list(p.aliases)
                props_out[pname] = entry
            node_entry: Dict[str, Any] = {"description": nd.description, "properties": props_out}
            if nd.same_as:
                node_entry["sameAs"] = nd.same_as
            if nd.aliases:
                node_entry["aliases"] = list(nd.aliases)
            if nd.broader:
                node_entry["broader"] = list(nd.broader)
            nodes_out[label] = node_entry

        rels_out: Dict[str, Any] = {}
        for rtype, rd in self.relationships.items():
            rel_entry: Dict[str, Any] = {
                "source": rd.source,
                "target": rd.target,
                "description": rd.description,
                "cardinality": rd.cardinality,
            }
            if rd.same_as:
                rel_entry["sameAs"] = rd.same_as
            if rd.aliases:
                rel_entry["aliases"] = list(rd.aliases)
            if rd.properties:
                rp: Dict[str, Any] = {}
                for pname, p in rd.properties.items():
                    rp[pname] = {"type": p.property_type.value}
                    if p.description:
                        rp[pname]["description"] = p.description
                rel_entry["properties"] = rp
            rels_out[rtype] = rel_entry

        result: Dict[str, Any] = {
            "graph_type": self.name,
            "package_id": self.package_id,
            "version": self.version,
            "description": self.description,
            "graph_model": self.graph_model,
        }
        if self.namespace:
            result["namespace"] = self.namespace
        result["nodes"] = nodes_out
        result["relationships"] = rels_out
        return result

    def to_yaml(self, path: Union[str, Path]) -> None:
        """Export ontology to a YAML file."""
        with open(path, "w") as fh:
            yaml.dump(self.to_dict(), fh, sort_keys=False, default_flow_style=False)

    # ------------------------------------------------------------------
    # JSON-LD — canonical storage format
    # ------------------------------------------------------------------

    #: Default JSON-LD @context for SEOCHO ontologies.
    JSONLD_CONTEXT: Dict[str, str] = {
        "schema": "https://schema.org/",
        "skos": "http://www.w3.org/2004/02/skos/core#",
        "sh": "http://www.w3.org/ns/shacl#",
        "xsd": "http://www.w3.org/2001/XMLSchema#",
        "seocho": "https://seocho.dev/ontology/",
    }

    @classmethod
    def from_jsonld(cls, path: Union[str, Path]) -> "Ontology":
        """Load an ontology from a JSON-LD file.

        This is the **canonical** persistence format.  The JSON-LD
        ``@context`` connects node labels to standard vocabularies
        (schema.org, SKOS) while keeping the file human-editable.

        Example file::

            {
              "@context": { ... },
              "@id": "seocho:financial",
              "@type": "seocho:Ontology",
              "name": "financial",
              "version": "1.0.0",
              "nodes": {
                "Company": {
                  "description": "기업",
                  "sameAs": "schema:Organization",
                  "aliases": ["기업", "회사"],
                  "properties": {
                    "name": {"type": "string", "unique": true}
                  }
                }
              },
              "relationships": { ... }
            }
        """
        from .ontology_serialization import ontology_from_jsonld_path

        return ontology_from_jsonld_path(cls, path)

    @classmethod
    def from_jsonld_dict(cls, data: Dict[str, Any]) -> "Ontology":
        """Build from a parsed JSON-LD dict."""
        from .ontology_serialization import ontology_from_jsonld_dict

        return ontology_from_jsonld_dict(cls, data)

    @classmethod
    def _from_jsonld_dict(cls, data: Dict[str, Any]) -> "Ontology":
        from .ontology_serialization import ontology_from_jsonld_dict

        return ontology_from_jsonld_dict(cls, data)

    def to_jsonld(self, path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
        """Export ontology as a JSON-LD document.

        If *path* is given the document is also written to disk.  The
        dict is always returned.

        This is the **canonical** persistence format for SEOCHO
        ontologies.
        """
        from .ontology_serialization import ontology_to_jsonld

        return ontology_to_jsonld(self, path)

    # ------------------------------------------------------------------
    # SHACL — derived validation shapes
    # ------------------------------------------------------------------

    _XSD_MAP: Dict[str, str] = {
        "STRING": "xsd:string",
        "INTEGER": "xsd:integer",
        "FLOAT": "xsd:float",
        "BOOLEAN": "xsd:boolean",
        "DATETIME": "xsd:dateTime",
        "DATE": "xsd:date",
    }

    def to_shacl(self) -> Dict[str, Any]:
        """Derive SHACL shapes from the ontology definition.

        Returns a JSON-LD-style dict that describes one
        ``sh:NodeShape`` per node type and one ``sh:PropertyShape`` per
        property with constraints.

        The shapes can be used for:

        - Post-extraction validation (does the extracted data conform?)
        - DozerDB constraint generation (via ``to_cypher_constraints()``)
        - Human review (readable constraint summary)

        The returned dict is structured as::

            {
              "@context": { ... },
              "shapes": [
                {
                  "@type": "sh:NodeShape",
                  "targetClass": "seocho:Company",
                  "properties": [
                    {
                      "path": "seocho:name",
                      "datatype": "xsd:string",
                      "minCount": 1,
                      "maxCount": 1,
                      "unique": true
                    }
                  ]
                }
              ]
            }
        """
        shapes: List[Dict[str, Any]] = []

        for label, nd in self.nodes.items():
            prop_shapes: List[Dict[str, Any]] = []
            for pname, p in nd.properties.items():
                ps: Dict[str, Any] = {
                    "path": f"seocho:{pname}",
                }
                xsd = self._XSD_MAP.get(p.property_type.value)
                if xsd:
                    ps["datatype"] = xsd
                if p.unique:
                    ps["minCount"] = 1
                    ps["maxCount"] = 1
                    ps["unique"] = True
                elif p.required:
                    ps["minCount"] = 1
                if p.description:
                    ps["description"] = p.description
                prop_shapes.append(ps)

            shape: Dict[str, Any] = {
                "@type": "sh:NodeShape",
                "targetClass": f"seocho:{label}",
            }
            if nd.description:
                shape["description"] = nd.description
            if prop_shapes:
                shape["properties"] = prop_shapes
            shapes.append(shape)

        # Relationship cardinality as property shapes on the source node
        for rtype, rd in self.relationships.items():
            if rd.cardinality in ("ONE_TO_ONE", "MANY_TO_ONE"):
                # The source node should have at most one of this rel
                rel_shape: Dict[str, Any] = {
                    "path": f"seocho:{rtype}",
                    "maxCount": 1,
                    "description": f"{rd.source} -[:{rtype}]-> {rd.target} ({rd.cardinality})",
                }
                # Find or create the source shape
                for s in shapes:
                    if s.get("targetClass") == f"seocho:{rd.source}":
                        s.setdefault("properties", []).append(rel_shape)
                        break

        return {
            "@context": dict(self.JSONLD_CONTEXT),
            "@type": "seocho:ShaclDocument",
            "shapes": shapes,
        }

    def to_ontology_candidate(self) -> Any:
        """Convert this ontology into the typed runtime ontology artifact."""
        from .ontology_artifacts import ontology_to_ontology_candidate

        return ontology_to_ontology_candidate(self)

    def to_shacl_candidate(self) -> Any:
        """Convert derived SHACL output into the typed runtime artifact shape."""
        from .ontology_artifacts import ontology_to_shacl_candidate

        return ontology_to_shacl_candidate(self)

    def to_vocabulary_candidate(self, *, include_properties: bool = True) -> Any:
        """Convert ontology labels and aliases into a lightweight vocabulary."""
        from .ontology_artifacts import ontology_to_vocabulary_candidate

        return ontology_to_vocabulary_candidate(self, include_properties=include_properties)

    def to_approved_artifacts(
        self,
        *,
        include_vocabulary: bool = True,
        include_property_terms: bool = True,
    ) -> Any:
        """Build an ``ApprovedArtifacts`` payload from this ontology."""
        from .ontology_artifacts import ontology_to_approved_artifacts

        return ontology_to_approved_artifacts(
            self,
            include_vocabulary=include_vocabulary,
            include_property_terms=include_property_terms,
        )

    def to_semantic_prompt_context(
        self,
        *,
        instructions: Optional[Sequence[str]] = None,
        include_vocabulary: bool = True,
        include_property_terms: bool = True,
    ) -> Any:
        """Build a typed semantic prompt context from this ontology."""
        from .ontology_artifacts import ontology_to_semantic_prompt_context

        return ontology_to_semantic_prompt_context(
            self,
            instructions=instructions,
            include_vocabulary=include_vocabulary,
            include_property_terms=include_property_terms,
        )

    def to_semantic_artifact_draft(
        self,
        *,
        name: Optional[str] = None,
        include_vocabulary: bool = True,
        include_property_terms: bool = True,
        source_summary: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Build a draft semantic artifact payload from this ontology."""
        from .ontology_artifacts import ontology_to_semantic_artifact_draft

        return ontology_to_semantic_artifact_draft(
            self,
            name=name,
            include_vocabulary=include_vocabulary,
            include_property_terms=include_property_terms,
            source_summary=source_summary,
        )

    def validate_with_shacl(self, data: Dict[str, Any]) -> List[str]:
        """Validate extracted data against SHACL shapes derived from
        this ontology.

        This combines :meth:`validate_extraction` with SHACL-level
        checks (datatype validation, cardinality).

        Parameters
        ----------
        data:
            Dict with ``"nodes"`` and ``"relationships"`` lists.

        Returns
        -------
        List of validation error strings (empty = valid).
        """
        errors = list(self.validate_extraction(data))
        shacl = self.to_shacl()

        # Build shape lookup: targetClass -> shape
        shape_map: Dict[str, Dict[str, Any]] = {}
        for shape in shacl.get("shapes", []):
            tc = shape.get("targetClass", "")
            if tc.startswith("seocho:"):
                shape_map[tc[len("seocho:"):]] = shape

        for node in data.get("nodes", []):
            nid = node.get("id", "")
            label = node.get("label", "")
            shape = shape_map.get(label)
            if shape is None:
                continue
            props = node.get("properties", {})
            for ps in shape.get("properties", []):
                path = ps.get("path", "")
                if path.startswith("seocho:"):
                    pname = path[len("seocho:"):]
                else:
                    continue
                # skip relationship paths
                if pname in self.relationships:
                    continue

                value = props.get(pname)
                min_count = ps.get("minCount", 0)
                if min_count >= 1 and (value is None or value == ""):
                    # already reported by validate_extraction, skip dup
                    continue

                # datatype check
                expected_xsd = ps.get("datatype")
                if expected_xsd and value is not None and value != "":
                    if expected_xsd == "xsd:integer" and not isinstance(value, int):
                        try:
                            int(value)
                        except (ValueError, TypeError):
                            errors.append(
                                f"Node '{nid}' ({label}).{pname}: "
                                f"expected integer, got {type(value).__name__}"
                            )
                    elif expected_xsd == "xsd:float" and not isinstance(value, (int, float)):
                        try:
                            float(value)
                        except (ValueError, TypeError):
                            errors.append(
                                f"Node '{nid}' ({label}).{pname}: "
                                f"expected float, got {type(value).__name__}"
                            )
                    elif expected_xsd == "xsd:boolean" and not isinstance(value, bool):
                        errors.append(
                            f"Node '{nid}' ({label}).{pname}: "
                            f"expected boolean, got {type(value).__name__}"
                        )

        # Relationship cardinality check
        source_rel_counts: Dict[str, Dict[str, int]] = {}  # node_id -> {rel_type -> count}
        for rel in data.get("relationships", []):
            src = rel.get("source", "")
            rtype = rel.get("type", "")
            source_rel_counts.setdefault(src, {})
            source_rel_counts[src][rtype] = source_rel_counts[src].get(rtype, 0) + 1

        for rtype, rd in self.relationships.items():
            if rd.cardinality in ("ONE_TO_ONE", "MANY_TO_ONE"):
                for node in data.get("nodes", []):
                    nid = node.get("id", "")
                    label = node.get("label", "")
                    if label == rd.source:
                        count = source_rel_counts.get(nid, {}).get(rtype, 0)
                        if count > 1:
                            errors.append(
                                f"Node '{nid}' ({label}) has {count} "
                                f"[:{rtype}] relationships but cardinality "
                                f"is {rd.cardinality} (max 1)"
                            )

        return errors

    # ------------------------------------------------------------------
    # Normalization / Denormalization — SHACL-guided graph transforms
    # ------------------------------------------------------------------

    def denormalization_plan(self) -> Dict[str, Any]:
        """Compute a SHACL-guided denormalization plan.

        Uses relationship cardinality to determine which target-node
        properties can be **safely embedded** into source nodes.

        Rules:

        - ``MANY_TO_ONE`` → embed target props into source (safe: at most 1 target)
        - ``ONE_TO_ONE``  → embed in both directions
        - ``ONE_TO_MANY`` → embed source props into each target
        - ``MANY_TO_MANY`` → **never embed** (must traverse)

        Returns
        -------
        Dict keyed by source node label::

            {
              "Person": {
                "embeds": [
                  {
                    "via": "CEO_OF",
                    "target": "Company",
                    "direction": "outgoing",
                    "cardinality": "MANY_TO_ONE",
                    "safe": True,
                    "fields": {"company_name": "name", "company_ticker": "ticker"},
                  }
                ]
              }
            }
        """
        plan: Dict[str, Any] = {}

        for rtype, rd in self.relationships.items():
            entries = self._denorm_entries_for(rtype, rd)
            for entry in entries:
                src_label = entry.pop("_source_label")
                plan.setdefault(src_label, {"embeds": []})
                plan[src_label]["embeds"].append(entry)

        return plan

    def _denorm_entries_for(
        self, rtype: str, rd: RelDef,
    ) -> List[Dict[str, Any]]:
        """Produce denorm entries for one relationship definition."""
        entries: List[Dict[str, Any]] = []

        # Self-referential relationships are never safe to embed —
        # they create ambiguous field names (e.g. person_name from which person?)
        if rd.source == rd.target:
            entries.append({
                "_source_label": rd.source,
                "via": rtype,
                "target": rd.target,
                "direction": "outgoing",
                "cardinality": rd.cardinality,
                "safe": False,
                "fields": {},
                "reason": f"Self-referential ({rd.source}->{rd.target}) — cannot embed",
            })
            return entries

        safe_outgoing = rd.cardinality in ("MANY_TO_ONE", "ONE_TO_ONE")
        safe_incoming = rd.cardinality in ("ONE_TO_MANY", "ONE_TO_ONE")

        if safe_outgoing and rd.target in self.nodes:
            target_nd = self.nodes[rd.target]
            prefix = rd.target.lower()
            field_map = {
                f"{prefix}_{pname}": pname
                for pname in target_nd.properties
            }
            entries.append({
                "_source_label": rd.source,
                "via": rtype,
                "target": rd.target,
                "direction": "outgoing",
                "cardinality": rd.cardinality,
                "safe": True,
                "fields": field_map,
            })

        if safe_incoming and rd.source in self.nodes:
            source_nd = self.nodes[rd.source]
            prefix = rd.source.lower()
            field_map = {
                f"{prefix}_{pname}": pname
                for pname in source_nd.properties
            }
            entries.append({
                "_source_label": rd.target,
                "via": rtype,
                "target": rd.source,
                "direction": "incoming",
                "cardinality": rd.cardinality,
                "safe": True,
                "fields": field_map,
            })

        if rd.cardinality == "MANY_TO_MANY":
            entries.append({
                "_source_label": rd.source,
                "via": rtype,
                "target": rd.target,
                "direction": "outgoing",
                "cardinality": rd.cardinality,
                "safe": False,
                "fields": {},
                "reason": "MANY_TO_MANY — must traverse, cannot embed",
            })

        return entries

    def to_denormalized_view(
        self,
        nodes: Sequence[Dict[str, Any]],
        relationships: Sequence[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Produce denormalized node views by embedding related properties.

        Takes the normalized graph data (as returned by extraction) and
        produces flattened node dicts where safe-to-embed relationship
        targets have their properties inlined.

        This is useful for:

        - Building flat context for LLM answer synthesis
        - Vector-store document creation (richer per-node text)
        - Export to tabular formats

        Parameters
        ----------
        nodes:
            List of ``{"id", "label", "properties": {...}}`` dicts.
        relationships:
            List of ``{"source", "target", "type", "properties": {...}}``
            dicts.

        Returns
        -------
        List of denormalized node dicts with embedded fields.
        """
        plan = self.denormalization_plan()

        # Build lookup: node_id -> node dict
        node_map: Dict[str, Dict[str, Any]] = {}
        for n in nodes:
            node_map[n.get("id", "")] = n

        # Build adjacency: (source_id, rel_type) -> [target_id, ...]
        outgoing: Dict[str, Dict[str, List[str]]] = {}  # src -> {rtype -> [tgt]}
        incoming: Dict[str, Dict[str, List[str]]] = {}  # tgt -> {rtype -> [src]}
        for rel in relationships:
            src = rel.get("source", "")
            tgt = rel.get("target", "")
            rtype = rel.get("type", "")
            outgoing.setdefault(src, {}).setdefault(rtype, []).append(tgt)
            incoming.setdefault(tgt, {}).setdefault(rtype, []).append(src)

        result: List[Dict[str, Any]] = []
        for n in nodes:
            nid = n.get("id", "")
            label = n.get("label", "")
            props = dict(n.get("properties", {}))

            denorm_entry: Dict[str, Any] = {
                "id": nid,
                "label": label,
                "properties": dict(props),
                "_embedded": {},
            }

            label_plan = plan.get(label, {})
            for embed in label_plan.get("embeds", []):
                if not embed.get("safe", False):
                    continue

                rtype = embed["via"]
                direction = embed["direction"]
                field_map = embed["fields"]

                # Find the related node(s) — should be exactly 0 or 1
                if direction == "outgoing":
                    related_ids = outgoing.get(nid, {}).get(rtype, [])
                else:
                    related_ids = incoming.get(nid, {}).get(rtype, [])

                if not related_ids:
                    continue

                # Take the first (cardinality guarantees at most 1)
                related_node = node_map.get(related_ids[0])
                if related_node is None:
                    continue

                related_props = related_node.get("properties", {})
                embedded: Dict[str, Any] = {}
                for embed_key, source_key in field_map.items():
                    if source_key in related_props:
                        denorm_entry["properties"][embed_key] = related_props[source_key]
                        embedded[embed_key] = related_props[source_key]

                if embedded:
                    denorm_entry["_embedded"][rtype] = {
                        "from": related_ids[0],
                        "fields": embedded,
                    }

            result.append(denorm_entry)

        return result

    def normalize_view(
        self,
        denormalized_nodes: Sequence[Dict[str, Any]],
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Reverse a denormalized view back into normalized nodes + relationships.

        Strips embedded fields (those present in the denormalization
        plan) from node properties, restoring the canonical normalized
        form.

        Parameters
        ----------
        denormalized_nodes:
            Output of :meth:`to_denormalized_view`.

        Returns
        -------
        Tuple of ``(clean_nodes, inferred_relationships)`` where
        ``clean_nodes`` have embedded fields removed and
        ``inferred_relationships`` are reconstructed from the
        ``_embedded`` metadata.
        """
        plan = self.denormalization_plan()

        # Collect all embed field names per label
        embed_fields_by_label: Dict[str, set] = {}
        for label, label_plan in plan.items():
            fields: set = set()
            for embed in label_plan.get("embeds", []):
                if embed.get("safe", False):
                    fields.update(embed["fields"].keys())
            embed_fields_by_label[label] = fields

        clean_nodes: List[Dict[str, Any]] = []
        inferred_rels: List[Dict[str, Any]] = []

        for dn in denormalized_nodes:
            nid = dn.get("id", "")
            label = dn.get("label", "")
            props = dict(dn.get("properties", {}))
            embedded_meta = dn.get("_embedded", {})

            # Strip embedded fields
            strip = embed_fields_by_label.get(label, set())
            clean_props = {k: v for k, v in props.items() if k not in strip}

            clean_nodes.append({
                "id": nid,
                "label": label,
                "properties": clean_props,
            })

            # Reconstruct relationships from _embedded metadata
            for rtype, meta in embedded_meta.items():
                related_id = meta.get("from", "")
                if related_id:
                    rd = self.relationships.get(rtype)
                    if rd is None:
                        continue
                    # Determine direction
                    label_plan = plan.get(label, {})
                    for embed in label_plan.get("embeds", []):
                        if embed["via"] == rtype:
                            if embed["direction"] == "outgoing":
                                inferred_rels.append({
                                    "source": nid,
                                    "target": related_id,
                                    "type": rtype,
                                    "properties": {},
                                })
                            else:
                                inferred_rels.append({
                                    "source": related_id,
                                    "target": nid,
                                    "type": rtype,
                                    "properties": {},
                                })
                            break

        # Deduplicate inferred relationships
        seen: set = set()
        unique_rels: List[Dict[str, Any]] = []
        for r in inferred_rels:
            key = (r["source"], r["type"], r["target"])
            if key not in seen:
                seen.add(key)
                unique_rels.append(r)

        return clean_nodes, unique_rels

    # ------------------------------------------------------------------
    # Prompt context generation (the key SDK feature)
    # ------------------------------------------------------------------

    def to_extraction_context(self) -> Dict[str, str]:
        """Build a context dict for **extraction/indexing** prompts.

        The returned dict is ready to be merged into a Jinja2 template
        context.  Keys:

        - ``ontology_name``
        - ``entity_types``  — human-readable list of node types + props
        - ``relationship_types`` — human-readable relationship listing
        - ``constraints_summary`` — property constraints for the LLM
        """
        return {
            "ontology_name": self.name,
            "entity_types": self._cached_render("entity_types", self._render_entity_types),
            "relationship_types": self._cached_render("relationship_types", self._render_relationship_types),
            "constraints_summary": self._cached_render("constraints_summary", self._render_constraints_summary),
        }

    def to_query_context(self) -> Dict[str, str]:
        """Build a context dict for **query-time** prompts.

        This is the key missing piece in the current codebase — the LLM
        generating Cypher (or answering questions) now receives full
        schema awareness including cardinality and constraints.

        Keys:

        - ``ontology_name``
        - ``graph_schema`` — full schema block for system prompt
        - ``node_types`` — concise node listing for quick reference
        - ``relationship_types`` — concise relationship listing
        - ``query_hints`` — constraint-derived hints (e.g. use exact
          match on UNIQUE properties)
        """
        return {
            "ontology_name": self.name,
            "ontology_profile": self._cached_render("ontology_profile", self._render_query_profile_summary),
            "graph_schema": self._cached_render("graph_schema", self._render_graph_schema),
            "node_types": self._cached_render("node_types", self._render_node_types_compact),
            "relationship_types": self._cached_render("rel_types_compact", self._render_relationship_types_compact),
            "query_hints": self._cached_render("query_hints", self._render_query_hints),
        }

    def to_query_profile(self) -> Dict[str, Any]:
        """Return structured ontology profile metadata for query planning."""
        deterministic_intents = [
            "entity_lookup",
            "relationship_lookup",
            "neighbors",
            "path",
            "count",
            "list_all",
        ]
        if self._supports_financial_metric_queries():
            deterministic_intents.extend(["financial_metric_lookup", "financial_metric_delta"])
        return {
            "ontology_name": self.name,
            "package_id": self.package_id,
            "version": self.version,
            "graph_model": self.graph_model,
            "node_labels": sorted(self.nodes.keys()),
            "relationship_types": sorted(self.relationships.keys()),
            "deterministic_intents": deterministic_intents,
        }

    def to_linking_context(self) -> Dict[str, str]:
        """Build a context dict for **entity linking** prompts."""
        return {
            "ontology_name": self.name,
            "relationship_types": self._cached_render("relationship_types", self._render_relationship_types),
            "entity_types": self._cached_render("entity_types", self._render_entity_types),
        }

    # ------------------------------------------------------------------
    # Cypher constraint generation
    # ------------------------------------------------------------------

    def to_cypher_constraints(self) -> List[str]:
        """Generate Cypher CREATE CONSTRAINT / CREATE INDEX statements."""
        stmts: List[str] = []
        for label, nd in self.nodes.items():
            for pname, p in nd.properties.items():
                if p.unique:
                    cname = f"constraint_{label}_{pname}_unique"
                    stmts.append(
                        f"CREATE CONSTRAINT {cname} IF NOT EXISTS "
                        f"FOR (n:{label}) REQUIRE n.{pname} IS UNIQUE"
                    )
            for pname, p in nd.properties.items():
                if p.index and not p.unique:
                    iname = f"index_{label}_{pname}"
                    stmts.append(
                        f"CREATE INDEX {iname} IF NOT EXISTS "
                        f"FOR (n:{label}) ON (n.{pname})"
                    )
        return stmts

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> List[str]:
        """Validate ontology consistency.  Returns a list of error strings
        (empty means valid)."""
        errors: List[str] = []
        for rtype, rd in self.relationships.items():
            if rd.source != "Any" and rd.source not in self.nodes:
                errors.append(f"Relationship '{rtype}' references unknown source '{rd.source}'")
            if rd.target != "Any" and rd.target not in self.nodes:
                errors.append(f"Relationship '{rtype}' references unknown target '{rd.target}'")
        for label, nd in self.nodes.items():
            if not nd.unique_properties:
                errors.append(f"Node '{label}' has no UNIQUE property — consider adding one")
            if not _LABEL_RE.match(label):
                errors.append(f"Node label '{label}' contains invalid characters")
        for rtype in self.relationships:
            if not _LABEL_RE.match(rtype):
                errors.append(f"Relationship type '{rtype}' contains invalid characters")
        return errors

    def validate_extraction(self, data: Dict[str, Any]) -> List[str]:
        """Validate extracted graph data against this ontology.

        Parameters
        ----------
        data:
            Dict with ``"nodes"`` and ``"relationships"`` lists as
            produced by the LLM extraction step.

        Returns
        -------
        List of validation error strings (empty = valid).
        """
        errors: List[str] = []
        known_ids: Set[str] = set()

        for node in data.get("nodes", []):
            nid = node.get("id", "")
            label = node.get("label", "")
            known_ids.add(nid)

            if label not in self.nodes and label != "Entity":
                errors.append(f"Node '{nid}' has unknown label '{label}'")
                continue

            nd = self.nodes.get(label)
            if nd is None:
                continue
            props = node.get("properties", {})
            for req in nd.required_properties:
                if req not in props or not props[req]:
                    errors.append(f"Node '{nid}' ({label}) missing required property '{req}'")

        for rel in data.get("relationships", []):
            rtype = rel.get("type", "")
            src = rel.get("source", "")
            tgt = rel.get("target", "")
            if rtype not in self.relationships:
                errors.append(f"Unknown relationship type '{rtype}'")

        return errors

    def score_extraction(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Score the quality of extracted graph data against this ontology.

        Returns a dict with per-node scores, per-relationship scores,
        and an overall confidence score (0.0–1.0).

        Scoring criteria:

        - **Label match**: Is the node label in the ontology? (0 or 1)
        - **Property completeness**: How many required/unique properties
          are filled? (0.0–1.0)
        - **Type correctness**: Do filled properties match expected types?
        - **Relationship validity**: Is the rel type known? Are source/target
          labels correct?

        Parameters
        ----------
        data:
            Dict with ``"nodes"`` and ``"relationships"`` lists.

        Returns
        -------
        Dict with ``"nodes"``, ``"relationships"``, ``"overall"`` scores::

            {
              "overall": 0.85,
              "nodes": [
                {"id": "p1", "label": "Person", "score": 0.9, "details": {...}},
              ],
              "relationships": [
                {"source": "p1", "target": "c1", "type": "WORKS_AT", "score": 1.0},
              ],
            }
        """
        node_scores: List[Dict[str, Any]] = []
        rel_scores: List[Dict[str, Any]] = []

        for node in data.get("nodes", []):
            if not isinstance(node, dict):
                continue
            nid = node.get("id", "")
            label = node.get("label", "")
            props = node.get("properties", {})

            score_parts: Dict[str, float] = {}

            # Label match
            if label in self.nodes:
                score_parts["label_match"] = 1.0
                nd = self.nodes[label]

                # Property completeness
                required = nd.required_properties
                if required:
                    filled = sum(1 for r in required if r in props and props[r])
                    score_parts["property_completeness"] = filled / len(required)
                else:
                    score_parts["property_completeness"] = 1.0

                # Type correctness
                type_checks = 0
                type_correct = 0
                for pname, p in nd.properties.items():
                    if pname in props and props[pname] is not None:
                        type_checks += 1
                        val = props[pname]
                        if p.property_type.value == "INTEGER" and isinstance(val, (int, float)):
                            type_correct += 1
                        elif p.property_type.value == "FLOAT" and isinstance(val, (int, float)):
                            type_correct += 1
                        elif p.property_type.value == "BOOLEAN" and isinstance(val, bool):
                            type_correct += 1
                        elif p.property_type.value == "STRING" and isinstance(val, str):
                            type_correct += 1
                        elif p.property_type.value in ("DATETIME", "DATE") and isinstance(val, str):
                            type_correct += 1
                        else:
                            type_correct += 0.5  # partial credit for other types
                score_parts["type_correctness"] = (
                    type_correct / type_checks if type_checks > 0 else 1.0
                )
            elif label == "Entity":
                score_parts["label_match"] = 0.5
                score_parts["property_completeness"] = 0.5
                score_parts["type_correctness"] = 0.5
            else:
                score_parts["label_match"] = 0.0
                score_parts["property_completeness"] = 0.0
                score_parts["type_correctness"] = 0.0

            node_score = sum(score_parts.values()) / len(score_parts) if score_parts else 0.0
            node_scores.append({
                "id": nid,
                "label": label,
                "score": round(node_score, 3),
                "details": score_parts,
            })

        for rel in data.get("relationships", []):
            rtype = rel.get("type", "")
            src = rel.get("source", "")
            tgt = rel.get("target", "")

            if rtype in self.relationships:
                rel_score = 1.0
            else:
                rel_score = 0.0

            rel_scores.append({
                "source": src,
                "target": tgt,
                "type": rtype,
                "score": rel_score,
            })

        all_scores = [n["score"] for n in node_scores] + [r["score"] for r in rel_scores]
        overall = sum(all_scores) / len(all_scores) if all_scores else 0.0

        return {
            "overall": round(overall, 3),
            "nodes": node_scores,
            "relationships": rel_scores,
        }

    # ------------------------------------------------------------------
    # Label safety
    # ------------------------------------------------------------------

    def is_valid_label(self, label: str) -> bool:
        return label in self._allowed_labels or label == "Entity"

    def sanitize_label(self, label: str) -> str:
        return label if self.is_valid_label(label) else "Entity"

    # ------------------------------------------------------------------
    # Render cache — avoids recomputing identical string outputs
    # ------------------------------------------------------------------

    def _cached_render(self, key: str, fn: Any) -> str:
        """Return a cached render result, computing it on first access."""
        if not hasattr(self, "_render_cache"):
            object.__setattr__(self, "_render_cache", {})
        cache = self._render_cache  # type: ignore[attr-defined]
        if key not in cache:
            cache[key] = fn()
        return cache[key]

    def invalidate_render_cache(self) -> None:
        """Clear all cached render outputs.

        Call this after programmatically mutating nodes or relationships
        on an existing Ontology instance (rare — ontologies are normally
        immutable after construction).
        """
        if hasattr(self, "_render_cache"):
            self._render_cache.clear()  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Private renderers — extraction
    # ------------------------------------------------------------------

    def _render_entity_types(self) -> str:
        lines: List[str] = []
        for label, nd in self.nodes.items():
            parts: List[str] = []
            for pname, p in nd.properties.items():
                tag = f"{pname}[{p.constraint.value}]" if p.constraint else pname
                parts.append(tag)
            props_str = ", ".join(parts) if parts else "none"
            desc = nd.description or label
            alias_str = f" (aliases: {', '.join(nd.aliases)})" if nd.aliases else ""
            lines.append(f"- {label}: {desc}{alias_str} (properties: {props_str})")
        return "\n".join(lines)

    def _render_relationship_types(self) -> str:
        lines: List[str] = []
        for rtype, rd in self.relationships.items():
            desc = rd.description or rtype
            card = rd.cardinality.replace("_", "-").lower()
            alias_str = f" (aliases: {', '.join(rd.aliases)})" if rd.aliases else ""
            lines.append(f"- {rtype}: {rd.source} -> {rd.target} [{card}] ({desc}){alias_str}")
        return "\n".join(lines)

    def _render_constraints_summary(self) -> str:
        lines: List[str] = []
        for label, nd in self.nodes.items():
            for pname, p in nd.properties.items():
                if p.constraint:
                    lines.append(f"- {label}.{pname}: {p.constraint.value}")
                if p.property_type != PropertyType.STRING:
                    lines.append(f"- {label}.{pname}: datatype={p.property_type.value}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Private renderers — query
    # ------------------------------------------------------------------

    def _render_graph_schema(self) -> str:
        """Full schema block for query-time system prompts."""
        sections: List[str] = []

        sections.append(f'Ontology: "{self.name}"')
        if self.description:
            sections.append(f"Description: {self.description}")
        sections.append("")

        # Nodes
        sections.append("Node types:")
        for label, nd in self.nodes.items():
            desc = f" — {nd.description}" if nd.description else ""
            sections.append(f"  ({label}){desc}")
            for pname, p in nd.properties.items():
                dtype = p.property_type.value
                flags: List[str] = []
                if p.unique:
                    flags.append("UNIQUE")
                if p.required:
                    flags.append("REQUIRED")
                if p.index:
                    flags.append("INDEXED")
                flag_str = f" [{', '.join(flags)}]" if flags else ""
                sections.append(f"    .{pname}: {dtype}{flag_str}")
        sections.append("")

        # Relationships
        sections.append("Relationship types:")
        for rtype, rd in self.relationships.items():
            card = rd.cardinality.replace("_", "-").lower()
            desc = f" — {rd.description}" if rd.description else ""
            sections.append(f"  [:{rtype}] ({rd.source})->({rd.target}) [{card}]{desc}")
            for pname, p in rd.properties.items():
                sections.append(f"    .{pname}: {p.property_type.value}")

        return "\n".join(sections)

    def _render_node_types_compact(self) -> str:
        return ", ".join(self.nodes.keys())

    def _render_relationship_types_compact(self) -> str:
        parts: List[str] = []
        for rtype, rd in self.relationships.items():
            parts.append(f"{rtype}({rd.source}->{rd.target})")
        return ", ".join(parts)

    def _render_query_hints(self) -> str:
        """Generate query-time hints derived from constraints."""
        hints: List[str] = []
        for label, nd in self.nodes.items():
            for pname, p in nd.properties.items():
                if p.unique:
                    hints.append(f"- {label}.{pname} is UNIQUE — use exact MATCH for lookups")
                if p.property_type in (PropertyType.INTEGER, PropertyType.FLOAT):
                    hints.append(f"- {label}.{pname} is numeric ({p.property_type.value}) — comparison operators are valid")
                if p.property_type in (PropertyType.DATETIME, PropertyType.DATE):
                    hints.append(f"- {label}.{pname} is temporal ({p.property_type.value}) — use datetime functions")
        for rtype, rd in self.relationships.items():
            if rd.cardinality == "ONE_TO_ONE":
                hints.append(f"- [{rtype}] is ONE-TO-ONE — expect at most 1 result per direction")
            elif rd.cardinality == "MANY_TO_ONE":
                hints.append(f"- [{rtype}] is MANY-TO-ONE — each {rd.source} has at most one {rd.target}")
        return "\n".join(hints)

    def _render_query_profile_summary(self) -> str:
        profile = self.to_query_profile()
        deterministic_intents = ", ".join(profile["deterministic_intents"])
        return (
            f"package_id={profile['package_id']}, "
            f"version={profile['version']}, "
            f"graph_model={profile['graph_model']}, "
            f"deterministic_intents=[{deterministic_intents}]"
        )

    def _supports_financial_metric_queries(self) -> bool:
        if "Company" not in self.nodes or "FinancialMetric" not in self.nodes:
            return False
        for rel in self.relationships.values():
            if rel.source == "Company" and rel.target == "FinancialMetric":
                return True
        return False

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------

    def merge(
        self,
        other: "Ontology",
        *,
        strategy: str = "union",
        name: Optional[str] = None,
    ) -> "Ontology":
        """Merge another ontology into this one.

        Combines nodes, relationships, and their properties. When both
        ontologies define the same label or relationship type, the
        ``strategy`` determines how conflicts are resolved.

        Parameters
        ----------
        other:
            The ontology to merge in.
        strategy:
            Conflict resolution for overlapping definitions:
            - ``"union"`` — combine properties from both (default)
            - ``"left_wins"`` — keep this ontology's definition
            - ``"right_wins"`` — keep other ontology's definition
            - ``"strict"`` — raise on any conflict
        name:
            Name for the merged ontology (defaults to "left+right").

        Returns
        -------
        A new :class:`Ontology` containing definitions from both.

        Example::

            finance = Ontology.from_jsonld("finance.jsonld")
            legal = Ontology.from_jsonld("legal.jsonld")
            combined = finance.merge(legal)
            combined.to_jsonld("combined.jsonld")
        """
        merged_name = name or f"{self.name}+{other.name}"
        merged_nodes: Dict[str, NodeDef] = {}
        merged_rels: Dict[str, RelDef] = {}
        conflicts: List[str] = []

        # --- Merge nodes ---
        all_labels = set(self.nodes.keys()) | set(other.nodes.keys())
        for label in sorted(all_labels):
            left = self.nodes.get(label)
            right = other.nodes.get(label)

            if left and not right:
                merged_nodes[label] = left
            elif right and not left:
                merged_nodes[label] = right
            else:
                # Both define this label — apply strategy
                merged_nodes[label] = self._merge_node_def(
                    label, left, right, strategy, conflicts
                )

        # --- Merge relationships ---
        all_rels = set(self.relationships.keys()) | set(other.relationships.keys())
        for rtype in sorted(all_rels):
            left = self.relationships.get(rtype)
            right = other.relationships.get(rtype)

            if left and not right:
                merged_rels[rtype] = left
            elif right and not left:
                merged_rels[rtype] = right
            else:
                merged_rels[rtype] = self._merge_rel_def(
                    rtype, left, right, strategy, conflicts
                )

        if conflicts and strategy == "strict":
            raise ValueError(
                f"Merge conflicts in strict mode:\n" +
                "\n".join(f"  - {c}" for c in conflicts)
            )

        return Ontology(
            name=merged_name,
            version=self.version,
            description=f"Merged: {self.name} + {other.name}",
            graph_model=self.graph_model,
            namespace=self.namespace or other.namespace,
            nodes=merged_nodes,
            relationships=merged_rels,
        )

    @staticmethod
    def _merge_node_def(
        label: str,
        left: NodeDef,
        right: NodeDef,
        strategy: str,
        conflicts: List[str],
    ) -> NodeDef:
        if strategy == "left_wins":
            return left
        if strategy == "right_wins":
            return right

        # Union: combine properties
        merged_props = dict(left.properties)
        for pname, p in right.properties.items():
            if pname in merged_props:
                existing = merged_props[pname]
                if existing.property_type != p.property_type:
                    conflicts.append(
                        f"Node '{label}' property '{pname}': "
                        f"type {existing.property_type.value} vs {p.property_type.value}"
                    )
                    if strategy == "strict":
                        continue
                # Union: more restrictive wins
                merged_props[pname] = P(
                    type=existing.property_type,
                    index=existing.index or p.index,
                    required=existing.required or p.required,
                    unique=existing.unique or p.unique,
                    description=existing.description or p.description,
                    aliases=list(set(existing.aliases + p.aliases)),
                )
            else:
                merged_props[pname] = p

        return NodeDef(
            description=left.description or right.description,
            properties=merged_props,
            aliases=list(set(left.aliases + right.aliases)),
            broader=list(set(left.broader + right.broader)),
            same_as=left.same_as or right.same_as,
        )

    @staticmethod
    def _merge_rel_def(
        rtype: str,
        left: RelDef,
        right: RelDef,
        strategy: str,
        conflicts: List[str],
    ) -> RelDef:
        if strategy == "left_wins":
            return left
        if strategy == "right_wins":
            return right

        # Check source/target compatibility
        if left.source != right.source or left.target != right.target:
            conflicts.append(
                f"Relationship '{rtype}': "
                f"{left.source}->{left.target} vs {right.source}->{right.target}"
            )
            if strategy == "strict":
                return left

        merged_props = dict(left.properties)
        merged_props.update(right.properties)

        return RelDef(
            source=left.source,
            target=left.target,
            description=left.description or right.description,
            cardinality=left.cardinality,
            properties=merged_props,
            aliases=list(set(left.aliases + right.aliases)),
            same_as=left.same_as or right.same_as,
        )

    # ------------------------------------------------------------------
    # Migration
    # ------------------------------------------------------------------

    def migration_plan(self, new_ontology: "Ontology") -> Dict[str, Any]:
        """Compute a migration plan from this ontology to a new version.

        Returns Cypher statements needed to transform existing graph
        data to match the new ontology schema.

        Parameters
        ----------
        new_ontology:
            The target ontology to migrate to.

        Returns
        -------
        Dict with ``renames``, ``additions``, ``removals``, and
        ``cypher_statements`` (ready to execute).

        Example::

            plan = old_onto.migration_plan(new_onto)
            print(plan["summary"])
            for stmt in plan["cypher_statements"]:
                graph_store.query(stmt["cypher"])  # dry_run first!
        """
        plan: Dict[str, Any] = {
            "from_version": self.version,
            "to_version": new_ontology.version,
            "renames": [],
            "additions": [],
            "removals": [],
            "cypher_statements": [],
            "breaking": False,
        }

        old_labels = set(self.nodes.keys())
        new_labels = set(new_ontology.nodes.keys())

        # Added node types
        for label in sorted(new_labels - old_labels):
            plan["additions"].append({"type": "node", "label": label})

        # Removed node types (breaking)
        for label in sorted(old_labels - new_labels):
            plan["removals"].append({"type": "node", "label": label})
            plan["breaking"] = True
            plan["cypher_statements"].append({
                "description": f"Remove all :{label} nodes",
                "cypher": f"MATCH (n:{label}) DETACH DELETE n",
                "breaking": True,
            })

        # Changed node types (property changes)
        for label in sorted(old_labels & new_labels):
            old_props = set(self.nodes[label].properties.keys())
            new_props = set(new_ontology.nodes[label].properties.keys())

            for prop in sorted(new_props - old_props):
                plan["additions"].append({"type": "property", "label": label, "property": prop})

            for prop in sorted(old_props - new_props):
                plan["removals"].append({"type": "property", "label": label, "property": prop})
                plan["cypher_statements"].append({
                    "description": f"Remove property {prop} from :{label}",
                    "cypher": f"MATCH (n:{label}) REMOVE n.`{prop}`",
                    "breaking": False,
                })

        # Relationship changes
        old_rels = set(self.relationships.keys())
        new_rels = set(new_ontology.relationships.keys())

        for rtype in sorted(new_rels - old_rels):
            plan["additions"].append({"type": "relationship", "relationship": rtype})

        for rtype in sorted(old_rels - new_rels):
            plan["removals"].append({"type": "relationship", "relationship": rtype})
            plan["breaking"] = True
            plan["cypher_statements"].append({
                "description": f"Remove all [{rtype}] relationships",
                "cypher": f"MATCH ()-[r:{rtype}]->() DELETE r",
                "breaking": True,
            })

        plan["summary"] = (
            f"Migration {self.version} → {new_ontology.version}: "
            f"{len(plan['additions'])} additions, "
            f"{len(plan['removals'])} removals, "
            f"{len(plan['cypher_statements'])} Cypher statements"
            + (" (BREAKING)" if plan["breaking"] else "")
        )

        return plan

    def apply_migration(
        self,
        graph_store: Any,
        new_ontology: "Ontology",
        *,
        database: str = "neo4j",
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Compute and optionally execute a migration to *new_ontology*.

        Parameters
        ----------
        graph_store:
            A :class:`~seocho.store.graph.GraphStore` instance (must support
            ``execute_write``).
        new_ontology:
            The target ontology.
        database:
            Target database name.
        dry_run:
            If ``True``, return the plan without executing any statements.

        Returns
        -------
        Dict with ``plan`` (full migration plan), ``executed`` (list of
        executed statement results), and ``errors`` (list of failures).
        When *dry_run* is ``True``, ``executed`` is empty.
        """
        plan = self.migration_plan(new_ontology)
        result: Dict[str, Any] = {
            "plan": plan,
            "executed": [],
            "errors": [],
            "dry_run": dry_run,
        }

        if dry_run or not plan["cypher_statements"]:
            return result

        for stmt in plan["cypher_statements"]:
            cypher = stmt["cypher"]
            try:
                write_result = graph_store.execute_write(
                    cypher, database=database,
                )
                result["executed"].append({
                    "description": stmt["description"],
                    "cypher": cypher,
                    "result": write_result,
                })
            except Exception as exc:
                result["errors"].append({
                    "description": stmt["description"],
                    "cypher": cypher,
                    "error": str(exc),
                })

        return result

    # ------------------------------------------------------------------
    # Dunder
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"Ontology(name={self.name!r}, package_id={self.package_id!r}, version={self.version!r}, "
            f"nodes={len(self.nodes)}, relationships={len(self.relationships)})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Ontology):
            return NotImplemented
        return self.to_dict() == other.to_dict()
