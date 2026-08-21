"""Post-pass ontology induction from a (stabilized) extracted graph (cold-start).

The inductive half of the cold-start bootstrap: take a graph produced by
upper-anchored open extraction (nodes carrying a specific ``label`` and an ``upper``
anchor), and induce a domain :class:`Ontology` —
- concrete types become ``NodeDef``s with ``broader=[upper]`` (a free subclass
  hierarchy under the foundational categories),
- relationship types become ``RelDef``s with source/target taken from the majority
  observed endpoint types,
- plus the mined axioms (``axioms.mine_axioms``) as the enrichment layer.

This is what turns "the LLM extracted something" into a governed, versionable schema,
WITHOUT a human authoring it — the human only approves. See
wiki/cold-start-schema-bootstrap-design.md.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List, Tuple

from .core import NodeDef, Ontology, P, RelDef


def _label_of(node: Dict[str, Any]) -> str:
    lab = node.get("label", node.get("labels"))
    if isinstance(lab, list):
        return str(lab[0]) if lab else ""
    return str(lab or "")


def induce_ontology_from_graph(
    graph: Dict[str, Any],
    *,
    upper_property: str = "upper",
    min_type_support: int = 1,
    name: str = "induced",
    version: str = "0.1.0",
) -> Tuple[Ontology, List[Any]]:
    """Induce a domain Ontology (types + hierarchy + relationships) and mine axioms.

    Returns ``(ontology, axioms)``. The ontology is a draft — persist it via the
    snapshot store and promote after approval (the lifecycle in ADR-0175/0176).
    Axiom mining is optional: if the ``axioms`` module is unavailable (it lands with
    seocho-ia4.8), ``axioms`` is an empty list and the type/hierarchy induction still
    runs.
    """
    try:
        from ..axioms import mine_axioms
    except ImportError:
        mine_axioms = None

    nodes = graph.get("nodes", []) or []
    rels = graph.get("relationships", []) or []

    # concrete type -> upper anchor (majority) + property keys + support
    upper_of: Dict[str, Counter] = defaultdict(Counter)
    prop_keys: Dict[str, set] = defaultdict(set)
    type_support: Counter = Counter()
    label_by_id: Dict[str, str] = {}
    for n in nodes:
        lab = _label_of(n)
        if not lab:
            continue
        label_by_id[str(n.get("id", ""))] = lab
        type_support[lab] += 1
        up = (n.get("properties", {}) or {}).get(upper_property) or n.get(upper_property)
        if up:
            upper_of[lab][str(up)] += 1
        for k in (n.get("properties", {}) or {}):
            if not str(k).startswith("_") and k != upper_property:
                prop_keys[lab].add(str(k))

    node_defs: Dict[str, NodeDef] = {}
    for lab, sup in type_support.items():
        if sup < min_type_support:
            continue
        broader = [upper_of[lab].most_common(1)[0][0]] if upper_of.get(lab) else []
        props = {k: P(str) for k in sorted(prop_keys.get(lab, set()))} or {"name": P(str)}
        node_defs[lab] = NodeDef(description=f"Induced type (support={sup}).",
                                 properties=props, broader=broader)

    # relationship types: majority (source-type, target-type)
    rel_endpoints: Dict[str, Counter] = defaultdict(Counter)
    for r in rels:
        rt = str(r.get("type", ""))
        s, t = label_by_id.get(str(r.get("source", ""))), label_by_id.get(str(r.get("target", "")))
        if rt and s and t:
            rel_endpoints[rt][(s, t)] += 1
    rel_defs: Dict[str, RelDef] = {}
    for rt, ends in rel_endpoints.items():
        (src, tgt), _ = ends.most_common(1)[0]
        if src in node_defs and tgt in node_defs:
            rel_defs[rt] = RelDef(source=src, target=tgt,
                                  description=f"Induced relation (support={sum(ends.values())}).")

    onto = Ontology(name=name, version=version,
                    description="Induced from an upper-anchored extracted graph.",
                    nodes=node_defs, relationships=rel_defs)
    axioms = mine_axioms(graph, min_support=max(min_type_support, 2)) if mine_axioms else []
    return onto, axioms


def induction_report(graph: Dict[str, Any], *,
                     upper_property: str = "upper") -> Dict[str, Any]:
    """Cheap drift/anchoring diagnostics for the cold-start A/B (no Ontology built)."""
    nodes = graph.get("nodes", []) or []
    types_per_upper: Dict[str, set] = defaultdict(set)
    anchored = 0
    total = 0
    for n in nodes:
        lab = _label_of(n)
        if not lab:
            continue
        total += 1
        up = (n.get("properties", {}) or {}).get(upper_property) or n.get(upper_property)
        if up:
            anchored += 1
            types_per_upper[str(up)].add(lab)
    return {
        "nodes": total,
        "anchored": anchored,
        "anchor_rate": round(anchored / total, 3) if total else 0.0,
        "distinct_types": len({_label_of(n) for n in nodes if _label_of(n)}),
        "upper_categories_used": len(types_per_upper),
        "types_per_upper": {k: sorted(v) for k, v in types_per_upper.items()},
    }
