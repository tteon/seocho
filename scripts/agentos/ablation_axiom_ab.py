"""A/B: SHACL-only vs induced+deduced axioms on an extracted graph (seocho-ia4.10).

hadry (2026-08-16): "이 실험을 한 번도 안 해봤다." This is the never-run test of the
write-time-rigor bet — does mining richer axioms (induction) + materializing
entailments (deduction) buy anything over the SHACL-only status quo?

- Arm A (SHACL-only, today): rules.infer_rules_from_graph — single-property shape
  constraints (required/datatype/enum/range) only.
- Arm B (induced+deduced): axioms.mine_axioms -> approve -> materialize_entailments —
  functional/inverse-functional, disjointness, subclass, composition rules, plus
  entailment materialization and contradiction detection.

Runs OFFLINE on a deterministic extracted-graph fixture (no API, no DB), so the
MECHANISM metrics are measurable now: axioms mined, approval burden (# candidates a
human reviews), contradictions the SHACL-only arm cannot catch, entailed structure
added. The remaining metric — does the enriched projection improve LLM ANSWER
quality — needs the live e2e (real dataset -> extraction -> DozerDB), which is the
pending run this harness is the offline half of.

Usage: python scripts/agentos/ablation_axiom_ab.py --out outputs/agentos/axiom_ab.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from seocho.axioms import approve, materialize_entailments, mine_axioms  # noqa: E402
from seocho.rules import infer_rules_from_graph  # noqa: E402


def _fixture() -> Dict[str, Any]:
    """A deterministic extracted graph with real axiom-bearing patterns + planted
    violations the SHACL-only arm cannot see."""
    nodes = []
    rels = []
    # Customers and Orders: each Order PLACED_BY exactly one Customer (functional),
    # except one planted violation.
    for i in range(8):
        nodes.append({"id": f"cust-{i}", "label": "Customer", "properties": {"name": f"C{i}"}})
    for i in range(12):
        nodes.append({"id": f"order-{i}", "label": "Order", "properties": {"amount": i * 10}})
        rels.append({"source": f"order-{i}", "target": f"cust-{i % 8}", "type": "PLACED_BY"})
    rels.append({"source": "order-0", "target": "cust-3", "type": "PLACED_BY"})  # functional VIOLATION

    # Managers are Employees (subclass): every Manager node also carries Employee.
    for i in range(5):
        nodes.append({"id": f"mgr-{i}", "label": ["Manager", "Employee"], "properties": {"name": f"M{i}"}})
    for i in range(10):
        nodes.append({"id": f"emp-{i}", "label": "Employee", "properties": {"name": f"E{i}"}})

    # Person vs Company: disjoint (multi-label vocabulary) with ONE planted violation.
    # 14 each -> 1 violation gives confidence 1 - 1/15 = 0.933 >= 0.9 (mined despite it).
    for i in range(14):
        nodes.append({"id": f"person-{i}", "label": "Person", "properties": {"name": f"P{i}"}})
    for i in range(14):
        nodes.append({"id": f"co-{i}", "label": "Company", "properties": {"name": f"Co{i}"}})
    nodes.append({"id": "mix-0", "label": ["Person", "Company"], "properties": {"name": "X"}})  # disjoint VIOLATION

    # Composition rule: WORKS_IN(x,team) ^ LOCATED_IN(team,city) => BASED_IN(x,city).
    # 12 WORKS_IN paths; BASED_IN asserted for all but 1 -> rule confidence 11/12=0.917
    # >= 0.9 (mined), and materialize entails the 1 missing edge.
    cities = ["nyc", "sf", "ldn"]
    for c in cities:
        nodes.append({"id": f"team-{c}", "label": "Team", "properties": {}})
        nodes.append({"id": f"city-{c}", "label": "City", "properties": {}})
        rels.append({"source": f"team-{c}", "target": f"city-{c}", "type": "LOCATED_IN"})
    for i in range(10):   # 10 DISTINCT employees -> 10 distinct (emp,city) body paths
        c = cities[i % 3]
        rels.append({"source": f"emp-{i}", "target": f"team-{c}", "type": "WORKS_IN"})
        if i != 0:        # miss exactly one BASED_IN -> confidence 9/10=0.9, entail 1
            rels.append({"source": f"emp-{i}", "target": f"city-{c}", "type": "BASED_IN"})
    return {"nodes": nodes, "relationships": rels}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min-support", type=int, default=3)
    ap.add_argument("--min-confidence", type=float, default=0.9)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    graph = _fixture()
    n_nodes = len(graph["nodes"])
    n_rels = len(graph["relationships"])

    # --- Arm A: SHACL-only (status quo) ---
    # rules.py is single-label (as real extraction nodes are); give it a
    # first-label view so the multi-label fixture nodes don't break it. This does
    # not change Arm A's property-shape output (it only reads node.properties).
    single = {
        "nodes": [{**n, "label": (n["label"][0] if isinstance(n.get("label"), list)
                                  else n.get("label"))} for n in graph["nodes"]],
        "relationships": graph["relationships"],
    }
    ruleset = infer_rules_from_graph(single)
    arm_a = {
        "property_shape_constraints": len(ruleset.rules),
        "axiom_classes_mined": 0,          # SHACL-only mines no cross-type/edge axioms
        "contradictions_caught": 0,        # cannot see functional/disjoint violations
        "entailed_edges": 0,
        "entailed_labels": 0,
    }

    # --- Arm B: induced + deduced ---
    candidates = mine_axioms(graph, min_support=args.min_support, min_confidence=args.min_confidence)
    approved = approve(candidates, min_support=args.min_support, min_confidence=args.min_confidence)
    ent = materialize_entailments(graph, approved)
    by_kind: Dict[str, int] = {}
    for c in approved:
        by_kind[c.kind] = by_kind.get(c.kind, 0) + 1
    arm_b = {
        "property_shape_constraints": len(ruleset.rules),   # B keeps A's shapes too
        "candidates_mined": len(candidates),
        "approved_axioms": len(approved),                    # the human approval burden
        "approved_by_kind": by_kind,
        "contradictions_caught": len(ent["contradictions"]),
        "contradiction_kinds": sorted({c["kind"] for c in ent["contradictions"]}),
        "entailed_edges": ent["entailed_edges"],
        "entailed_labels": ent["entailed_labels"],
        "graph_enrichment_ratio": round(ent["entailed_edges"] / max(n_rels, 1), 3),
    }

    report = {
        "graph": {"nodes": n_nodes, "relationships": n_rels},
        "arm_A_shacl_only": arm_a,
        "arm_B_induced_deduced": arm_b,
        "note": "answer-quality delta requires the live LLM e2e (real dataset -> "
                "extraction -> DozerDB); this offline arm measures the mechanism.",
    }

    print("=== axiom A/B: SHACL-only vs induced+deduced (seocho-ia4.10) ===")
    print(f"  graph: {n_nodes} nodes, {n_rels} rels")
    print(f"  {'metric':26s} {'A: SHACL-only':>14s} {'B: induced+deduced':>20s}")
    print(f"  {'property-shape constraints':26s} {arm_a['property_shape_constraints']:>14d} "
          f"{arm_b['property_shape_constraints']:>20d}")
    print(f"  {'axiom classes mined':26s} {arm_a['axiom_classes_mined']:>14d} "
          f"{arm_b['approved_axioms']:>20d}  {arm_b['approved_by_kind']}")
    print(f"  {'contradictions caught':26s} {arm_a['contradictions_caught']:>14d} "
          f"{arm_b['contradictions_caught']:>20d}  {arm_b['contradiction_kinds']}")
    print(f"  {'entailed edges added':26s} {arm_a['entailed_edges']:>14d} "
          f"{arm_b['entailed_edges']:>20d}")
    print(f"  {'entailed labels added':26s} {arm_a['entailed_labels']:>14d} "
          f"{arm_b['entailed_labels']:>20d}")
    print(f"  approval burden (candidates a human reviews) = {arm_b['approved_axioms']} "
          f"of {arm_b['candidates_mined']} mined")
    print("  NOTE: answer-quality delta needs the live e2e (this is the offline mechanism half).")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
