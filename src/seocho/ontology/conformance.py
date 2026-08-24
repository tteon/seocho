"""Does the declared ontology describe the graph that is actually loaded?

Everything text2cypher does rests on the ontology: it is what goes into the prompt, what
``policy_from_ontology`` validates against, and what :mod:`seocho.query.grammar` derives the
decode-time grammar from. Nothing else checks it against the graph, and the drift fails
silently in both directions — measured on the AIsummit26 FinBench graph (2026-08-22), where
five real, fully-populated properties were undeclared:

* **declared but absent** — the prompt advertises a label, relationship or property that does
  not exist; the model uses it, the query returns nothing, and nothing errors.
* **present but undeclared** — the graph has something the ontology hides; the validator
  rejects a correct query (``unknown_properties``) and the repair loop burns generations on
  something no repair can fix, while the grammar makes the same question *unrepresentable*.
* **identity keys** — the properties the ontology names as identities are what text2cypher
  anchors on; an unindexed one turns every anchored query into a sweep.
* **relationship endpoints** — a declared ``(source)-[TYPE]->(target)`` triple that never
  occurs lets the grammar admit a pattern that can only ever match nothing. Endpoint
  declarations use the ``Person|Company`` union syntax, and the check expands it: treating
  the union as a literal label reported four real relationships as never occurring, which was
  a bug in the first version of this check rather than drift in the ontology.

Read-only, dependency-free: the caller supplies ``run_query`` (any callable that executes a
Cypher string and returns ``list[dict]``), so this works against neo4j-python, the rust
driver, or a test double alike.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Sequence, Set, Tuple

RunQuery = Callable[[str], List[Dict[str, Any]]]


def _labels(value: Any) -> List[str]:
    """Endpoint labels with the ontology's union syntax expanded."""
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple)) else [value]
    out: List[str] = []
    for item in values:
        out.extend(part.strip() for part in str(item).split("|") if part.strip())
    return out


def declared_view(ontology: Any) -> Dict[str, Any]:
    """The ontology as the prompt, the policy, and the grammar see it."""
    nodes = getattr(ontology, "nodes", None) or {}
    rels = getattr(ontology, "relationships", None) or {}

    def props_of(spec: Any) -> List[str]:
        p = getattr(spec, "properties", None)
        if p is None and isinstance(spec, Mapping):
            p = spec.get("properties")
        return sorted((p or {}).keys())

    def ident_of(spec: Any) -> List[str]:
        k = getattr(spec, "identity_keys", None)
        if k is None and isinstance(spec, Mapping):
            k = spec.get("identity_keys")
        return list(k or [])

    endpoints: List[str] = []
    for name, spec in rels.items():
        src = getattr(spec, "source", None) or (spec.get("source") if isinstance(spec, Mapping) else None)
        dst = getattr(spec, "target", None) or (spec.get("target") if isinstance(spec, Mapping) else None)
        endpoints.extend(f"{a}-[{name}]->{b}" for a in _labels(src) for b in _labels(dst))
    return {
        "labels": sorted(nodes.keys()),
        "rel_types": sorted(rels.keys()),
        "node_properties": {k: props_of(v) for k, v in nodes.items()},
        "rel_properties": {k: props_of(v) for k, v in rels.items()},
        "identity_keys": {k: ident_of(v) for k, v in nodes.items()},
        "rel_endpoints": sorted(set(endpoints)),
    }


def graph_view(run_query: RunQuery, *, sample: int = 5000) -> Dict[str, Any]:
    """What the database actually contains, read through its own schema procedures."""
    labels = sorted(r["label"] for r in run_query("CALL db.labels() YIELD label RETURN label"))
    rel_types = sorted(r["relationshipType"] for r in run_query(
        "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType"))
    node_props: Dict[str, List[str]] = {}
    for label in labels:
        rows = run_query(
            f"MATCH (n:`{label}`) WITH keys(n) AS k LIMIT {int(sample)} "
            "UNWIND k AS key RETURN DISTINCT key ORDER BY key")
        node_props[label] = [r["key"] for r in rows]
    rel_props: Dict[str, List[str]] = {}
    for rt in rel_types:
        rows = run_query(
            f"MATCH ()-[r:`{rt}`]->() WITH keys(r) AS k LIMIT {int(sample)} "
            "UNWIND k AS key RETURN DISTINCT key ORDER BY key")
        rel_props[rt] = [r["key"] for r in rows]
    endpoints = sorted(
        f'{r["src"]}-[{r["t"]}]->{r["dst"]}' for r in run_query(
            "MATCH (a)-[r]->(b) WITH labels(a) AS la, type(r) AS t, labels(b) AS lb "
            "UNWIND la AS src UNWIND lb AS dst RETURN DISTINCT src, t, dst"))
    indexed: Set[Tuple[str, str]] = set()
    for ix in run_query("SHOW INDEXES YIELD labelsOrTypes, properties "
                        "RETURN labelsOrTypes, properties"):
        for lab in (ix.get("labelsOrTypes") or []):
            for prop in (ix.get("properties") or []):
                indexed.add((lab, prop))
    return {"labels": labels, "rel_types": rel_types, "node_properties": node_props,
            "rel_properties": rel_props, "rel_endpoints": endpoints,
            "indexed": sorted(indexed)}


def conformance_report(ontology: Any, run_query: RunQuery, *,
                       infrastructure_prefix: str = "_") -> Dict[str, Any]:
    """Compare declaration against reality; every mismatch names its consequence.

    Properties starting with ``infrastructure_prefix`` (tenant scope, harness-derived
    degree columns) are infrastructure, not domain schema — the ontology is not expected
    to declare them.
    """
    d = declared_view(ontology)
    g = graph_view(run_query)
    problems: List[str] = []

    def diff(scope: str, decl: Set[str], real: Set[str]) -> Dict[str, Any]:
        absent, undeclared = sorted(decl - real), sorted(real - decl)
        if absent:
            problems.append(f"{scope}: declared but absent {absent}")
        if undeclared:
            problems.append(f"{scope}: present but undeclared {undeclared}")
        return {"scope": scope, "declared_but_absent": absent,
                "present_but_undeclared": undeclared}

    diffs = [diff("labels", set(d["labels"]), set(g["labels"])),
             diff("relationship_types", set(d["rel_types"]), set(g["rel_types"]))]
    for label in sorted(set(d["labels"]) | set(g["labels"])):
        real = {p for p in g["node_properties"].get(label, [])
                if not p.startswith(infrastructure_prefix)}
        diffs.append(diff(f"properties:{label}",
                          set(d["node_properties"].get(label, [])), real))
    for rt in sorted(set(d["rel_types"]) | set(g["rel_types"])):
        real = {p for p in g["rel_properties"].get(rt, [])
                if not p.startswith(infrastructure_prefix)}
        diffs.append(diff(f"rel_properties:{rt}",
                          set(d["rel_properties"].get(rt, [])), real))

    indexed = {tuple(x) for x in g["indexed"]}
    identity_rows = []
    for label, keys in sorted(d["identity_keys"].items()):
        for key in keys:
            exists = key in set(g["node_properties"].get(label, []))
            is_indexed = (label, key) in indexed
            identity_rows.append({"label": label, "identity_key": key,
                                  "exists_in_graph": exists, "indexed": is_indexed})
            if not exists:
                problems.append(f"{label}.{key}: identity key not in graph")
            elif not is_indexed:
                problems.append(f"{label}.{key}: identity key NOT INDEXED — "
                                f"anchored queries on it sweep")

    endpoint_diff = diff("relationship_endpoints",
                         set(d["rel_endpoints"]), set(g["rel_endpoints"]))
    return {"declared": d, "graph": g, "diffs": diffs, "identity_keys": identity_rows,
            "endpoint_diff": endpoint_diff, "problems": problems,
            "conformant": not problems}


__all__ = ["conformance_report", "declared_view", "graph_view"]
