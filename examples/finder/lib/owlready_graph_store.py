"""Owlready2-backed RDF / OWL store for the FinDER RDF vs LPG tutorial.

Implements ``seocho.store.graph.GraphStore`` over an owlready2 ``World``.
The choice of owlready2 (rather than plain rdflib) is deliberate:

- FIBO ships as OWL ontologies (TTL/RDFXML); owlready2 loads them natively
- owlready2 exposes OWL classes/individuals/properties as first-class
  Python objects, so a node label maps to a Python class and an edge
  type maps to an ``ObjectProperty``
- SPARQL is supported by projecting to rdflib via ``world.as_rdflib_graph()``
- An optional reasoner pass shows the LPG-can't-do-this advantage of OWL

This is consistent with how the rest of seocho already uses owlready2
(see ``seocho/ontology_governance.py`` and
``scripts/ontology/build_ontology_hints.py``) — strictly offline, no
hot-path reasoning.

Public extras beyond the GraphStore ABC:
- ``sparql(query)`` — SPARQL SELECT via rdflib projection
- ``onto`` — the underlying owlready2 ``Ontology``
- ``run_reasoner(infer=True)`` — synchronous OWL reasoning
- ``serialize(format='turtle')`` — dump RDF
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from seocho.store.graph import GraphStore


def _safe_local(name: str) -> str:
    """Make a string safe to use as the local part of an IRI / Python identifier."""
    cleaned = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in str(name)).strip("_")
    if not cleaned:
        return "x"
    if cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return cleaned


class OwlreadyGraphStore(GraphStore):
    """Embedded OWL/RDF store backed by owlready2."""

    def __init__(
        self,
        *,
        ontology: Any,
        namespace: str = "https://spec.edmcouncil.org/fibo/",
        store_path: Optional[str] = None,
    ) -> None:
        try:
            import owlready2
        except ImportError as exc:
            raise ImportError(
                "OwlreadyGraphStore requires 'owlready2'. "
                "Install it with: pip install owlready2"
            ) from exc

        self._owlready2 = owlready2
        self._world = owlready2.World(filename=store_path) if store_path else owlready2.World()
        self._namespace = namespace if namespace.endswith("/") or namespace.endswith("#") else namespace + "/"
        self._onto = self._world.get_ontology(self._namespace)

        # Compile seocho ontology classes and object properties up-front so
        # writes can simply look them up by label.
        self._classes: Dict[str, Any] = {}
        self._object_properties: Dict[str, Any] = {}
        self._data_property: Any = None
        with self._onto:
            Thing = owlready2.Thing
            for label in ontology.nodes.keys():
                cls_name = _safe_local(label)
                cls = self._onto.__class__.__bases__  # noqa: F841 (placeholder for clarity)
                cls = type(cls_name, (Thing,), {"namespace": self._onto})
                self._classes[label] = cls
            for rtype in ontology.relationships.keys():
                prop_name = _safe_local(rtype)
                prop = type(prop_name, (owlready2.ObjectProperty,), {"namespace": self._onto})
                self._object_properties[rtype] = prop
            # Single generic AnnotationProperty for arbitrary string properties.
            # (Fine for tutorial; production work should declare per-property
            # DataProperties with proper domain/range.)
            self._data_property = type(
                "has_property",
                (owlready2.AnnotationProperty,),
                {"namespace": self._onto},
            )

    # ------------------------------------------------------------------
    # Public extras
    # ------------------------------------------------------------------

    @property
    def onto(self) -> Any:
        return self._onto

    def sparql(self, query: str) -> List[Dict[str, Any]]:
        """SPARQL SELECT via the rdflib projection of the owlready2 world."""
        rdf_graph = self._world.as_rdflib_graph()
        rows: List[Dict[str, Any]] = []
        for row in rdf_graph.query(query):
            rows.append({str(var): str(val) for var, val in zip(row.labels, row)})
        return rows

    def run_reasoner(self, *, infer_property_values: bool = False) -> Dict[str, Any]:
        """Run an OWL reasoner over the world.

        Uses owlready2's ``sync_reasoner`` (HermiT-backed via Java).
        Skipped silently if no JVM available — this is offline tooling
        and the tutorial flag-gates the call.
        """
        try:
            with self._onto:
                self._owlready2.sync_reasoner(self._world, infer_property_values=infer_property_values)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def serialize(self, *, format: str = "turtle") -> str:
        rdf_graph = self._world.as_rdflib_graph()
        return rdf_graph.serialize(format=format)

    # ------------------------------------------------------------------
    # GraphStore ABC
    # ------------------------------------------------------------------

    def write(
        self,
        nodes: Sequence[Dict[str, Any]],
        relationships: Sequence[Dict[str, Any]],
        *,
        database: str = "neo4j",
        workspace_id: str = "default",
        source_id: str = "",
    ) -> Dict[str, Any]:
        summary: Dict[str, Any] = {"nodes_created": 0, "relationships_created": 0, "errors": []}
        instances: Dict[str, Any] = {}

        with self._onto:
            for node in nodes:
                node_id = str(node.get("id") or node.get("properties", {}).get("name", ""))
                if not node_id:
                    summary["errors"].append(f"Node missing id: {node}")
                    continue
                label = str(node.get("label", "Entity"))
                cls = self._classes.get(label)
                if cls is None:
                    summary["errors"].append(f"Unknown class for label '{label}'")
                    continue
                local = _safe_local(node_id)
                # Look up existing individual or create a new one.
                existing = self._onto[local]
                instance = existing if existing is not None else cls(local)
                # Stamp every literal property as an annotation. Workspace
                # and source provenance live as annotations too.
                for k, v in (node.get("properties", {}) or {}).items():
                    if v is None:
                        continue
                    self._data_property[instance].append(f"{k}={v}")
                if workspace_id:
                    self._data_property[instance].append(f"_workspace_id={workspace_id}")
                if source_id:
                    self._data_property[instance].append(f"_source_id={source_id}")
                instances[node_id] = instance
                summary["nodes_created"] += 1

            for rel in relationships:
                src_id = str(rel.get("source", ""))
                tgt_id = str(rel.get("target", ""))
                if not src_id or not tgt_id:
                    summary["errors"].append(f"Edge missing source/target: {rel}")
                    continue
                rtype = str(rel.get("type", "RELATED_TO"))
                prop = self._object_properties.get(rtype)
                if prop is None:
                    # Tolerant: create the ObjectProperty on the fly so
                    # extraction-time labels that weren't declared up-front
                    # still produce edges (the tutorial pre-declares from
                    # the seocho ontology, so this is rare).
                    with self._onto:
                        prop = type(_safe_local(rtype), (self._owlready2.ObjectProperty,), {"namespace": self._onto})
                    self._object_properties[rtype] = prop
                src = instances.get(src_id) or self._onto[_safe_local(src_id)]
                tgt = instances.get(tgt_id) or self._onto[_safe_local(tgt_id)]
                if src is None or tgt is None:
                    summary["errors"].append(f"Unresolved endpoint for edge {src_id}-[{rtype}]->{tgt_id}")
                    continue
                prop[src].append(tgt)
                summary["relationships_created"] += 1
        return summary

    def query(
        self,
        cypher: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        database: str = "neo4j",
        workspace_id: Optional[str] = None,
        enforce_workspace_filter: bool = False,
    ) -> List[Dict[str, Any]]:
        # owlready2 has no Cypher engine.  Fall back to a name-based
        # search so seocho's auto-generated queries still produce
        # something; for real RDF retrieval call ``sparql(...)``.
        merged = dict(params or {})
        candidates: List[str] = [str(v) for v in merged.values() if v]
        out: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for kw in candidates:
            for ind in self._onto.search(iri=f"*{_safe_local(kw)}*"):
                key = str(ind.iri)
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    "iri": key,
                    "name": ind.name,
                    "classes": [c.name for c in ind.is_a if hasattr(c, "name")],
                    "labels": list(getattr(self._data_property[ind], "__iter__", lambda: [])()),
                })
        return out

    def ensure_constraints(
        self,
        ontology: Any,
        *,
        database: str = "neo4j",
        strict: bool = False,
        transactional: bool = False,
    ) -> Dict[str, Any]:
        # Classes and properties are declared at __init__; no runtime
        # constraints to apply on owlready2.
        return {"success": 0, "errors": []}

    def execute_write(
        self,
        cypher: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        database: str = "neo4j",
        workspace_id: Optional[str] = None,
        enforce_workspace_filter: bool = False,
    ) -> Dict[str, Any]:
        raise NotImplementedError(
            "OwlreadyGraphStore.execute_write: use write(...) or sparql(...)."
        )

    def get_schema(self, *, database: str = "neo4j") -> Dict[str, Any]:
        return {
            "labels": sorted(self._classes.keys()),
            "relationship_types": sorted(self._object_properties.keys()),
            "individual_count": len(list(self._onto.individuals())),
        }

    def delete_by_source(
        self,
        source_id: str,
        *,
        database: str = "neo4j",
    ) -> Dict[str, Any]:
        marker = f"_source_id={source_id}"
        nodes_deleted = 0
        for ind in list(self._onto.individuals()):
            if marker in self._data_property[ind]:
                self._owlready2.destroy_entity(ind)
                nodes_deleted += 1
        return {"nodes_deleted": nodes_deleted, "relationships_deleted": 0}

    def count_by_source(
        self,
        source_id: str,
        *,
        database: str = "neo4j",
    ) -> Dict[str, int]:
        marker = f"_source_id={source_id}"
        nodes = sum(
            1
            for ind in self._onto.individuals()
            if marker in self._data_property[ind]
        )
        return {"nodes": nodes, "relationships": 0}

    def close(self) -> None:
        try:
            self._world.save()
        except Exception:
            pass
