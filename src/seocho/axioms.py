"""Inductive axiom mining + deductive entailment materialization (seocho-ia4.8/ia4.9).

The existing `rules.py` mines only single-*property* SHACL-shape axioms (required /
datatype / enum / range). This module adds the richer axiom classes the write-time-
rigor thesis needs — mined automatically from the extracted graph (induction), so a
human APPROVES candidates rather than AUTHORS them — plus a cheap, structural
deductive step that materializes entailed edges/labels so the projected graph carries
inferred structure the LLM can traverse. See wiki/axiom-induction-deduction-
projection-design.md.

Induction (``mine_axioms``): over {nodes, relationships},
- functional / inverse-functional: a relationship type where (almost) every source
  has <=1 target (resp. target has <=1 source) — the DL functional/IFP property, and
  the write-time interning `identity_keys` signal in shape form.
- disjoint: two labels that never co-occur on a node (owl:disjointWith candidate) —
  catches the 동명이인 / wrong-subgraph boundary-1 error at write time.
- subclass: label A whose every instance also carries label B (A ⊑ B).
- rule (AMIE-lite): a 2-hop composition R1(x,y) ∧ R2(y,z) ⇒ R3(x,z), with
  support/confidence.
All candidates carry support + confidence; the caller keeps those at/above a
threshold (the cheap human-approval gate).

Deduction (``materialize_entailments``): applies confirmed axioms — subclass closure
(ancestor labels), composition rules (new edges) — and DETECTS contradictions
(functional / disjoint violations). Entailed elements are marked ``_entailed:"true"``
(analogous to the existing ``_out_of_ontology`` stamp) so induced/deduced assertions
stay auditable against asserted ones. Structural only; a full OWL reasoner
(owlready2) stays offline (governance), never on this path.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set, Tuple


@dataclass
class AxiomCandidate:
    kind: str                    # functional | inverse_functional | disjoint | subclass | rule
    subject: str                 # relationship type or label (or "R1,R2=>R3" for rule)
    detail: Dict[str, Any] = field(default_factory=dict)
    support: int = 0             # # of instances the pattern is drawn from
    confidence: float = 0.0      # fraction consistent with the axiom

    def key(self) -> Tuple[str, str]:
        return (self.kind, self.subject)


def _node_labels(nodes: List[Dict[str, Any]]) -> Dict[str, Set[str]]:
    """node id -> set of labels (a node may carry several; label may be str or list)."""
    out: Dict[str, Set[str]] = {}
    for n in nodes:
        nid = str(n.get("id", ""))
        lab = n.get("label", n.get("labels", []))
        labs = {str(lab)} if isinstance(lab, str) else {str(x) for x in (lab or [])}
        out.setdefault(nid, set()).update({x for x in labs if x})
    return out


def mine_axioms(
    graph: Dict[str, Any],
    *,
    min_support: int = 3,
    min_confidence: float = 0.9,
    mine_rules: bool = True,
) -> List[AxiomCandidate]:
    """Induce candidate axioms from an extracted graph. Returns ALL candidates with
    support/confidence; the caller filters by threshold (the approval gate)."""
    nodes = graph.get("nodes", []) or []
    rels = graph.get("relationships", []) or []
    labels_of = _node_labels(nodes)
    out: List[AxiomCandidate] = []

    # --- functional / inverse-functional per relationship type -----------------
    by_type_src: Dict[str, Dict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))
    by_type_tgt: Dict[str, Dict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))
    for r in rels:
        rt = str(r.get("type", ""))
        s, t = str(r.get("source", "")), str(r.get("target", ""))
        if rt and s and t:
            by_type_src[rt][s].add(t)
            by_type_tgt[rt][t].add(s)
    for rt, src_map in by_type_src.items():
        n = len(src_map)
        if n >= min_support:
            consistent = sum(1 for tgts in src_map.values() if len(tgts) <= 1)
            conf = consistent / n
            out.append(AxiomCandidate("functional", rt,
                                      {"reading": "each source has <=1 target"},
                                      support=n, confidence=round(conf, 3)))
    for rt, tgt_map in by_type_tgt.items():
        n = len(tgt_map)
        if n >= min_support:
            consistent = sum(1 for srcs in tgt_map.values() if len(srcs) <= 1)
            conf = consistent / n
            out.append(AxiomCandidate("inverse_functional", rt,
                                      {"reading": "each target has <=1 source"},
                                      support=n, confidence=round(conf, 3)))

    # --- disjointness: labels that (almost) never co-occur ---------------------
    # Restricted to the MULTI-LABEL VOCABULARY — labels that the modeling actually
    # multi-types somewhere. Between labels that are never multi-typed, disjointness
    # is trivially true for every unrelated pair (combinatorial noise); it is only a
    # meaningful CANDIDATE among classes where overlap is structurally possible.
    # Confidence-based, so a rare violation does not suppress the axiom (the violation
    # is then caught deductively by materialize_entailments).
    label_counts: Dict[str, int] = defaultdict(int)
    cooccur: Dict[Tuple[str, str], int] = defaultdict(int)
    multi_vocab: Set[str] = set()
    for labs in labels_of.values():
        for lb in labs:
            label_counts[lb] += 1
        if len(labs) > 1:
            multi_vocab.update(labs)
        ls = sorted(labs)
        for i in range(len(ls)):
            for j in range(i + 1, len(ls)):
                cooccur[(ls[i], ls[j])] += 1
    candidates_lbl = sorted(lb for lb in multi_vocab if label_counts[lb] >= min_support)
    for i in range(len(candidates_lbl)):
        for j in range(i + 1, len(candidates_lbl)):
            a, b = candidates_lbl[i], candidates_lbl[j]
            co = cooccur.get((a, b), 0)
            conf = 1.0 - co / max(min(label_counts[a], label_counts[b]), 1)
            if conf >= min_confidence:
                out.append(AxiomCandidate("disjoint", f"{a}|{b}",
                                          {"labels": [a, b], "cooccurrences": co},
                                          support=min(label_counts[a], label_counts[b]),
                                          confidence=round(conf, 3)))

    # --- subclass: A's instances all also carry B ------------------------------
    inst_of: Dict[str, Set[str]] = defaultdict(set)
    for nid, labs in labels_of.items():
        for lb in labs:
            inst_of[lb].add(nid)
    present = sorted(lb for lb, c in label_counts.items() if c >= min_support)
    for a in present:
        for b in present:
            if a == b:
                continue
            if inst_of[a] and inst_of[a] <= inst_of[b] and len(inst_of[a]) < len(inst_of[b]):
                out.append(AxiomCandidate("subclass", f"{a}=>{b}",
                                          {"child": a, "parent": b},
                                          support=len(inst_of[a]), confidence=1.0))

    # --- AMIE-lite composition rules: R1(x,y) ^ R2(y,z) => R3(x,z) --------------
    if mine_rules:
        out.extend(_mine_composition_rules(rels, min_support=min_support))

    return out


def _mine_composition_rules(rels, *, min_support: int) -> List[AxiomCandidate]:
    out: List[AxiomCandidate] = []
    edges: Dict[str, List[Tuple[str, str]]] = defaultdict(list)   # type -> [(s,t)]
    pair_types: Dict[Tuple[str, str], Set[str]] = defaultdict(set)  # (s,t) -> {types}
    for r in rels:
        rt, s, t = str(r.get("type", "")), str(r.get("source", "")), str(r.get("target", ""))
        if rt and s and t:
            edges[rt].append((s, t))
            pair_types[(s, t)].add(rt)
    by_src: Dict[str, List[Tuple[str, str]]] = defaultdict(list)   # type indexed by source
    for rt, es in edges.items():
        for s, t in es:
            by_src[s].append((rt, t))
    # count body support and head co-occurrence
    body: Dict[Tuple[str, str], int] = defaultdict(int)
    bodyhead: Dict[Tuple[str, str, str], int] = defaultdict(int)
    for r in rels:
        r1, x, y = str(r.get("type", "")), str(r.get("source", "")), str(r.get("target", ""))
        if not (r1 and x and y):
            continue
        for (r2, z) in by_src.get(y, []):
            body[(r1, r2)] += 1
            for r3 in pair_types.get((x, z), set()):
                bodyhead[(r1, r2, r3)] += 1
    for (r1, r2, r3), bh in bodyhead.items():
        b = body[(r1, r2)]
        if b >= min_support and r3 not in (r1, r2):
            out.append(AxiomCandidate("rule", f"{r1},{r2}=>{r3}",
                                      {"body": [r1, r2], "head": r3},
                                      support=b, confidence=round(bh / b, 3)))
    return out


def approve(candidates: List[AxiomCandidate], *, min_support: int = 3,
            min_confidence: float = 0.9) -> List[AxiomCandidate]:
    """The cheap approval gate: keep high-support, high-confidence candidates."""
    return [c for c in candidates
            if c.support >= min_support and c.confidence >= min_confidence]


def materialize_entailments(
    graph: Dict[str, Any],
    axioms: List[AxiomCandidate],
) -> Dict[str, Any]:
    """Deduce: apply confirmed axioms (subclass closure, composition rules) to add
    entailed labels/edges (marked ``_entailed``), and detect contradictions
    (functional / disjoint violations). Returns a report + the enriched graph."""
    nodes = [dict(n) for n in (graph.get("nodes", []) or [])]
    rels = [dict(r) for r in (graph.get("relationships", []) or [])]
    labels_of = _node_labels(nodes)
    node_by_id = {str(n.get("id", "")): n for n in nodes}

    entailed_labels = 0
    entailed_edges: List[Dict[str, Any]] = []
    contradictions: List[Dict[str, Any]] = []

    # subclass closure: A ⊑ B -> stamp label B on A's instances
    for ax in axioms:
        if ax.kind == "subclass":
            child, parent = ax.detail["child"], ax.detail["parent"]
            for nid, labs in labels_of.items():
                if child in labs and parent not in labs:
                    n = node_by_id.get(nid)
                    if n is not None:
                        props = n.setdefault("properties", {})
                        props[f"_entailed_label_{parent}"] = "true"
                        labs.add(parent)
                        entailed_labels += 1

    # disjoint violation detection
    for ax in axioms:
        if ax.kind == "disjoint":
            a, b = ax.detail["labels"]
            for nid, labs in labels_of.items():
                if a in labs and b in labs:
                    contradictions.append({"kind": "disjoint_violation",
                                           "node": nid, "labels": [a, b]})

    # functional violation detection
    by_type_src: Dict[str, Dict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))
    for r in rels:
        rt, s, t = str(r.get("type", "")), str(r.get("source", "")), str(r.get("target", ""))
        if rt and s and t:
            by_type_src[rt][s].add(t)
    for ax in axioms:
        if ax.kind == "functional":
            for s, tgts in by_type_src.get(ax.subject, {}).items():
                if len(tgts) > 1:
                    contradictions.append({"kind": "functional_violation",
                                           "rel_type": ax.subject, "source": s,
                                           "targets": sorted(tgts)})

    # composition rules: add entailed head edges where absent
    existing = {(str(r.get("source")), str(r.get("target")), str(r.get("type"))) for r in rels}
    by_src: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    for r in rels:
        by_src[str(r.get("source", ""))].append((str(r.get("type", "")), str(r.get("target", ""))))
    for ax in axioms:
        if ax.kind == "rule":
            r1, r2 = ax.detail["body"]
            r3 = ax.detail["head"]
            for r in rels:
                if str(r.get("type")) != r1:
                    continue
                x, y = str(r.get("source", "")), str(r.get("target", ""))
                for (rt2, z) in by_src.get(y, []):
                    if rt2 == r2 and (x, z, r3) not in existing:
                        entailed_edges.append({"source": x, "target": z, "type": r3,
                                               "properties": {"_entailed": "true"}})
                        existing.add((x, z, r3))

    rels.extend(entailed_edges)
    return {
        "nodes": nodes,
        "relationships": rels,
        "entailed_labels": entailed_labels,
        "entailed_edges": len(entailed_edges),
        "contradictions": contradictions,
    }
