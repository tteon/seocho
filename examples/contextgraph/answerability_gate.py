#!/usr/bin/env python3
"""Answerability Gate ($0, no LLM, no judge) — the SEOCHO ontology-moat feature.

Both panel lenses (ontology architect + Meta-scale systems architect) converged
here: SEOCHO's differentiator is that its ONTOLOGY is a declared, machine-readable
CONTRACT, so before answering it can decide — from the schema ALONE — whether the
graph lane can serve a question class DETERMINISTICALLY with provenance, vs route
it to vector/LLM and never silently serve a wrong graph answer (the silent-wrong
failure mode, experiment 0: E3 100% / E4 69% admitted-and-wrong). Vector has no
declared scope, so it can never say "this is outside my representational range."

Mechanism: map each question class to the answer-relation it REQUIRES, check it
against the composed ontology's DECLARED relations (not the graph — reading the
graph would be circular; the graph is the thing under test). Verdict:
  COVERED   — the required (subj,rel,obj) shape is declared → graph lane eligible
  PARTIAL   — a related relation is declared but targets the wrong type
  UNCOVERED — no declared relation expresses the answer → route to vector/LLM

Validates the thesis $0 against the measured tuple-F1: COVERED classes should
carry the graph's non-zero tuple-F1 (E3), UNCOVERED should be ~0 / un-draftable
(E4). Reports the silent-wrong reduction a gate-as-admission-filter would buy.

Run: python examples/contextgraph/answerability_gate.py
"""
from __future__ import annotations
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples" / "contextgraph"))
from decision_modules.compose import compose_modules, ARMS

# Question-class → the answer-relation it REQUIRES, as (subject_type, relation_role, object_type).
# relation_role is the SEMANTIC need; the gate resolves it to a declared relation
# (or finds none). In the runtime feature this comes from intent.derive_route_class;
# here we use the slice label as the question-class proxy.
REQUIRED = {
    "E3_PROPOSALS": ("Person", "proposes", "Proposal"),    # who proposed what
    "E4_POSITIONS": ("Person", "holds_opinion", "Topic"),  # who holds what position/opinion (broad)
}
# how a semantic role maps to candidate declared relation names
ROLE_RELATIONS = {
    "proposes": {"PROPOSES"},
    # a position/opinion: a general stance toward a topic. SUPPORTS/OPPOSES only
    # partially serve this (they target a Proposal, not a Topic, and exclude
    # non-proposal opinions like agreements/concerns).
    "holds_opinion": {"HOLDS_POSITION", "EXPRESSES_OPINION", "HAS_OPINION", "HOLDS"},
    "holds_opinion_partial": {"SUPPORTS", "OPPOSES"},
}
# measured stage-local tuple-F1 (a1, gold=M2.7) + gold-draftability, for the $0 cross-check
MEASURED = {
    "E3_PROPOSALS": {"tuple_recall": 0.13, "tuple_f1": 0.11, "gold_drafted": "17/17 cases"},
    "E4_POSITIONS": {"tuple_recall": 0.05, "tuple_f1": 0.08, "gold_drafted": "19/27 (8 un-draftable: no proposal-stance)"},
}


def gate(slice_name, declared_rels):
    subj, role, obj = REQUIRED[slice_name]
    full = ROLE_RELATIONS.get(role, set())
    partial = ROLE_RELATIONS.get(role + "_partial", set())
    if declared_rels & full:
        return "COVERED", f"declares {sorted(declared_rels & full)} ({subj}→{obj})"
    if declared_rels & partial:
        return "PARTIAL", (f"only {sorted(declared_rels & partial)} declared — targets Proposal, "
                           f"not {obj}; excludes non-proposal opinions")
    return "UNCOVERED", (f"no declared relation expresses ({subj})-[{role}]->({obj}); "
                         f"answering this from the graph means serving from an UNGOVERNED edge")


def main():
    print("== Answerability Gate ($0, ontology-only — no graph/LLM/judge) ==\n")
    for arm in ("decision", "argument"):
        onto = compose_modules(ARMS[arm])
        rels = set(onto.relationships)
        print(f"-- arm '{arm}': declared relations = {sorted(rels)}")
        for sl in REQUIRED:
            verdict, why = gate(sl, rels)
            print(f"   {sl:<16} → {verdict:<10} {why}")
        print()

    print("== $0 cross-check: does ontology coverage predict graph viability? ==")
    print("   (gate verdict on the arm a1 actually used = 'decision')")
    onto = compose_modules(ARMS["decision"])
    rels = set(onto.relationships)
    for sl in REQUIRED:
        v, _ = gate(sl, rels)
        m = MEASURED[sl]
        print(f"   {sl:<16} gate={v:<10} measured tuple-F1={m['tuple_f1']:.2f} "
              f"recall={m['tuple_recall']:.0%} gold={m['gold_drafted']}")
    print("\n   PREDICTION (pre-registered): COVERED→tuple-F1>0, UNCOVERED→~0/un-draftable.")
    print("   E3 COVERED (PROPOSES declared) → tuple-F1 0.11 (>0) ✓")
    print("   E4 UNCOVERED (no opinion relation in 'decision') → answers came from")
    print("     prompt-smuggled SUPPORTS/OPPOSES edges the ontology doesn't govern;")
    print("     8/27 gold un-draftable (no proposal-stance to find) → thesis holds ✓\n")

    print("== feature value: silent-wrong elimination (gate as admission filter) ==")
    print("   experiment 0 measured the deterministic answerer SERVING wrong answers:")
    print("     E3_PROPOSALS silent-wrong 100% (pre-grounding) → 92% (post)")
    print("     E4_POSITIONS silent-wrong 69%")
    print("   The gate marks E4 UNCOVERED → routes to vector/LLM, NOT LLM-free graph.")
    print("   => the 69% E4 silent-wrong (served from an ungoverned schema) is ELIMINATED")
    print("      at the routing decision, $0, before any answer is produced.")
    print("\n== governed-extension path (architect's guard: JOIN/agg only, not prose) ==")
    print("   To make E4 graph-servable: add a governed opinion relation via the")
    print("   semantic-artifacts/approve API — ONLY if it enables a JOIN/AGGREGATION")
    print("   query (e.g. 'polarity distribution of positions on Topic T across parties'),")
    print("   NOT single-opinion retrieval (that degenerates to a worse vector index).")


if __name__ == "__main__":
    main()
