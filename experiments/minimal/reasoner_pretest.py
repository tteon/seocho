#!/usr/bin/env python3
"""Does a reasoner derive anything FIBO does not already state?

Arm E would hand the extractor FIBO's *inferred* structure rather than only its
asserted axioms. That is only worth paying for if the reasoner actually produces
something new. If everything it returns was already written down, arm E is arm C
with extra steps, and this script exists to find that out for nothing.

Four things are counted, each chosen because the extraction path currently lacks
it entirely:

  subsumption   `Ontology._render_entity_types` emits a flat list of class
                names with no hierarchy at all. If classes can be placed under
                one another, two views answering `PubliclyHeldCompany` and
                `LegalEntity` for the same entity stop being a mismatch and
                become compatible — which changes what the comparability
                measure is measuring.
  equivalence   distinct IRIs denoting one class: the class-level version of
                folding surface forms onto one identifier.
  domain/range  relation endpoints. arms.py walks the parent chain by hand to
                find them; entailment does it completely.
  disjointness  pairs that cannot both hold. The pipeline has no contradiction
                detector, so this is a new capability rather than a better
                number.

Which reasoner, and what that costs in claim strength
-----------------------------------------------------
HermiT and Pellet are OWL 2 DL and both need a JVM, which this machine does not
have. Installing one is a change to the box, so the default here is `owlrl`,
a pure-Python OWL 2 RL closure over rdflib.

RL is a real profile, not an approximation of convenience: it gives subsumption
transitivity, equivalence, property characteristics, and domain/range
propagation, which is all four of the things counted above. What it does not do
is derive subsumption from complex class expressions — a class defined only by
an intersection or a someValuesFrom restriction will not be placed by RL where
a DL reasoner would place it. So every count here is a **lower bound** on what
a DL reasoner would find, and the verdict is stated in those terms.

Reasoning is offline schema compilation, the only place CLAUDE.md 6.3 permits
this. Nothing here runs in a request path and no model is called.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for path in (str(HERE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

import arms  # noqa: E402

TTL = ROOT / "dataset/fibo/fibo-quickstart.ttl"
OUT_ROOT = ROOT / "outputs/minimal"


def short(iri: Any) -> str:
    return str(iri).rsplit("/", 1)[-1].split("#")[-1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--class-limit", type=int, default=70)
    args = ap.parse_args()

    import observe
    from rdflib import Graph, RDFS, OWL, URIRef

    run = observe.Run(OUT_ROOT, "reasoner-pretest", {"decisive": {
        "engine": "owlrl.OWLRL_Semantics", "profile": "OWL 2 RL",
        "class_limit": args.class_limit,
        "source": str(TTL.relative_to(ROOT)), "seed": 42}})

    with run.stage("scope", class_limit=args.class_limit) as out:
        fibo = arms.parse_fibo()
        documents, _ = arms.load_corpus_text()
        scoped = arms.scope_to_corpus(fibo, documents, args.class_limit, 20)
        wanted = {c["iri"]: c["node"] for c in scoped}
        out["classes"] = len(wanted)

    with run.stage("load", source=str(TTL.relative_to(ROOT))) as out:
        graph = Graph()
        graph.parse(TTL, format="turtle")
        out["triples"] = len(graph)

    def relations() -> dict[str, set[tuple[str, str]]]:
        """The three relation sets the arms would consume, as plain triples."""
        subclass = {(str(s), str(o)) for s, o in graph.subject_objects(RDFS.subClassOf)
                    if isinstance(s, URIRef) and isinstance(o, URIRef)}
        equivalent = {(str(s), str(o)) for s, o
                      in graph.subject_objects(OWL.equivalentClass)
                      if isinstance(s, URIRef) and isinstance(o, URIRef)}
        disjoint = {(str(s), str(o)) for s, o
                    in graph.subject_objects(OWL.disjointWith)
                    if isinstance(s, URIRef) and isinstance(o, URIRef)}
        return {"subclass": subclass, "equivalent": equivalent,
                "disjoint": disjoint}

    def endpoints() -> int:
        resolved = 0
        for prop in set(graph.subjects(None, OWL.ObjectProperty)) | set(
                graph.subjects(URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#type"),
                               OWL.ObjectProperty)):
            domains = {str(d) for d in graph.objects(prop, RDFS.domain)}
            ranges = {str(r) for r in graph.objects(prop, RDFS.range)}
            if domains & set(wanted) and ranges & set(wanted):
                resolved += 1
        return resolved

    with run.stage("before") as out:
        before = relations()
        before_endpoints = endpoints()
        out["subclass"] = len(before["subclass"])
        out["equivalent"] = len(before["equivalent"])
        out["disjoint"] = len(before["disjoint"])
        out["relations_with_both_endpoints_in_scope"] = before_endpoints

    with run.stage("reason", profile="OWL 2 RL") as out:
        try:
            import owlrl

            owlrl.DeductiveClosure(owlrl.OWLRL_Semantics,
                                   axiomatic_triples=False,
                                   datatype_axioms=False).expand(graph)
            out["status"] = "completed"
            out["triples_after"] = len(graph)
        except Exception as exc:  # noqa: BLE001 — recorded, never imputed
            out["status"] = "failed"
            out["error"] = f"{type(exc).__name__}: {str(exc)[:600]}"
            run.log("entailment failed; arm E cannot be justified from this run")
            run.finish({"reasoning": "failed", "error": out["error"]})
            return 1

    with run.stage("after") as out:
        after = relations()
        after_endpoints = endpoints()
        out["subclass"] = len(after["subclass"])
        out["equivalent"] = len(after["equivalent"])
        out["disjoint"] = len(after["disjoint"])
        out["relations_with_both_endpoints_in_scope"] = after_endpoints

    in_scope = set(wanted)

    with run.stage("diff") as out:
        new_subclass = after["subclass"] - before["subclass"]
        new_equivalent = after["equivalent"] - before["equivalent"]
        new_disjoint = after["disjoint"] - before["disjoint"]

        # Only edges between two scoped classes can change comparability: a
        # parent outside the seventy the arm ships cannot help two views agree.
        internal_subclass = [(s, o) for s, o in new_subclass
                             if s in in_scope and o in in_scope]
        internal_equivalent = [(s, o) for s, o in new_equivalent
                               if s in in_scope and o in in_scope]
        touching = [(s, o) for s, o in new_subclass
                    if s in in_scope or o in in_scope]

        out["new_subclass_total"] = len(new_subclass)
        out["new_subclass_touching_scope"] = len(touching)
        out["new_subclass_within_scope"] = len(internal_subclass)
        out["new_equivalent_within_scope"] = len(internal_equivalent)
        out["new_disjoint"] = len(new_disjoint)
        out["endpoints_gained"] = after_endpoints - before_endpoints

    with run.stage("hierarchy") as out:
        # How much of the arm's own class list gains a parent inside the list.
        parents: dict[str, set[str]] = defaultdict(set)
        for s, o in after["subclass"]:
            if s in in_scope and o in in_scope and s != o:
                parents[wanted[s]].add(wanted[o])
        asserted: dict[str, set[str]] = defaultdict(set)
        for s, o in before["subclass"]:
            if s in in_scope and o in in_scope and s != o:
                asserted[wanted[s]].add(wanted[o])
        out["classes_with_a_parent_in_scope_before"] = len(asserted)
        out["classes_with_a_parent_in_scope_after"] = len(parents)
        out["examples"] = [f"{c} < {'|'.join(sorted(p))}"
                           for c, p in sorted(parents.items())[:10]]

    disjoint_in_scope = [(wanted[s], wanted[o]) for s, o in after["disjoint"]
                         if s in in_scope and o in in_scope]

    gains = (len(internal_subclass) + len(internal_equivalent)
             + len(disjoint_in_scope) + max(0, after_endpoints - before_endpoints))
    verdict = ("arm E is justified: entailment adds structure the flat class "
               "list does not have" if gains > 0 else
               "arm E is not justified by this evidence: entailment adds "
               "nothing inside the arm's own class set")

    payload = {
        "contract": "log2026.reasoner_pretest.v2",
        "question": ("Does entailment derive structure FIBO does not state, for "
                     "the classes arm C ships?"),
        "engine": "owlrl OWL 2 RL closure over rdflib",
        "claim_boundary": ("OWL 2 RL, not DL. RL does not derive subsumption "
                           "from complex class expressions, so every count here "
                           "is a lower bound on what HermiT or Pellet would "
                           "find. Counts what entailment adds to the schema; it "
                           "says nothing about whether a richer schema improves "
                           "extraction, which only the paid arm can answer."),
        "scoped_classes": len(wanted),
        "triples_before": before_endpoints and len(before["subclass"]),
        "new_subclass_total": len(new_subclass),
        "new_subclass_touching_scope": len(touching),
        "new_subclass_within_scope": len(internal_subclass),
        "new_equivalent_within_scope": len(internal_equivalent),
        "new_disjoint": len(new_disjoint),
        "disjoint_pairs_in_scope": len(disjoint_in_scope),
        "relations_with_both_endpoints_in_scope": {
            "before": before_endpoints, "after": after_endpoints},
        "classes_with_a_parent_in_scope": {
            "before": len(asserted), "after": len(parents)},
        "verdict": verdict,
        "hierarchy": {c: sorted(p) for c, p in sorted(parents.items())},
        "disjoint_examples": disjoint_in_scope[:40],
        "new_subclass_examples": [[short(s), short(o)]
                                  for s, o in internal_subclass[:40]],
    }
    (run.dir / "reasoner_pretest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    print()
    print(f"scoped classes                          {len(wanted)}")
    print(f"subclass edges entailment added (all)   {len(new_subclass):,}")
    print(f"  touching a scoped class               {len(touching):,}")
    print(f"  both ends inside the arm's classes    {len(internal_subclass)}")
    print(f"equivalences added within scope         {len(internal_equivalent)}")
    print(f"disjoint pairs available within scope   {len(disjoint_in_scope)}")
    print(f"relations with both endpoints in scope  "
          f"{before_endpoints} -> {after_endpoints}")
    print(f"classes with a parent inside the arm    "
          f"{len(asserted)} -> {len(parents)}")
    print(f"\n{verdict}")
    for line in payload["hierarchy"] and list(
            f"  {c} < {', '.join(p)}" for c, p in sorted(parents.items())[:12]):
        print(line)

    run.finish({"verdict": verdict,
                "new_subclass_within_scope": len(internal_subclass),
                "disjoint_pairs_in_scope": len(disjoint_in_scope),
                "classes_with_parent_in_scope": len(parents),
                "artifact": str((run.dir / "reasoner_pretest.json").relative_to(ROOT))})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
