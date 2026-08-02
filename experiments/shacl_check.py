#!/usr/bin/env python3
"""Validate the extracted graphs with a real constraint checker.

`Ontology.validate_with_shacl` walks a dict shaped like SHACL and checks
minCount and datatype by hand. There is no W3C SHACL engine anywhere in the
repository, so the name asserts a standard the code does not implement, and the
only validation that actually runs is set membership: is this label in the
declared class list, is this relationship type in the declared list.

Set membership cannot see most of what an ontology says. It cannot tell that a
relationship's endpoints are the wrong classes, that a node declared to require
a name has none, that two classes asserted disjoint are both on one node, or
that a property is used outside the class that declares it. Those are the
constraints, and none of them is currently checked.

So this generates shapes from the ontology each condition shipped, converts the
committed snapshots to triples, and runs pySHACL over them.

The comparison is by kind, not by count, and the first attempt at it was wrong.
Counting violations and dividing made SHACL look weaker — 441 against 4,856 for
condition A — but the two are not measuring one thing more or less thoroughly.
The membership test counts every *use* of an undeclared label or relationship
type, while a SHACL node shape reports once per node; and a relationship type
the ontology never declared has no shape at all, so SHACL is structurally unable
to object to it. Neither number bounds the other.

What matters is the class of violation each can see. Membership sees undeclared
names and nothing else. SHACL sees whether a relationship's endpoints are the
classes its type declares, and whether a node has the properties its class
requires — neither of which membership can express at all, at any count.

Validation is switched off by default in the pipeline (`strict_validation`
defaults to False), so the violations here were all written to the graph. This
does not fix that; it measures it.

    python3 experiments/shacl_check.py --tag v2 --arms A,C,D,E
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for path in (str(ROOT / "experiments/minimal"), str(HERE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

import arms as arms_mod  # noqa: E402
import cq_suite  # noqa: E402  — the snapshot-to-triples mapping lives there

SNAPSHOTS = ROOT / "snapshots"
OUT_ROOT = ROOT / "outputs/minimal"
NS = cq_suite.NS

SHAPE_PREFIXES = """
@prefix sh:   <http://www.w3.org/ns/shacl#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix s:    <urn:seocho:> .
@prefix c:    <urn:seocho:c/> .
@prefix r:    <urn:seocho:r/> .
"""


def shapes_for(ontology, arm: str) -> str:
    """SHACL shapes from what the condition actually declared.

    Three constraint kinds, chosen because each is something the string check
    is structurally unable to see:

      required properties   a class declaring `name` as required means a node
                            of that class without one is invalid, not merely
                            unusual
      closed class set      a node whose type the condition never declared is a
                            violation of the schema it was given
      relationship endpoints  a declared relationship states its source and
                            target class, and an edge between other classes
                            contradicts that
    """
    lines = [SHAPE_PREFIXES]
    declared = sorted(ontology.nodes)
    for label, node in sorted(ontology.nodes.items()):
        required = [name for name, prop in node.properties.items()
                    if getattr(prop, "required", False)]
        body = [f"c:{label}Shape a sh:NodeShape ;",
                f"    sh:targetClass c:{label} ;"]
        for name in required:
            body.append(
                f"    sh:property [ sh:path s:{name} ; sh:minCount 1 ; "
                f"sh:severity sh:Violation ] ;")
        body[-1] = body[-1].rstrip(";") + "."
        lines.append("\n".join(body))

    # A node carrying a type the condition never declared. Expressed as a shape
    # over the type itself so the report names the offending class.
    lines.append(
        "s:DeclaredClassShape a sh:NodeShape ;\n"
        "    sh:targetSubjectsOf rdf:type ;\n"
        "    sh:property [ sh:path rdf:type ; sh:severity sh:Violation ;\n"
        "        sh:in ( " + " ".join(f"c:{l}" for l in declared) + " ) ] .")

    for rtype, rel in sorted(ontology.relationships.items()):
        source = getattr(rel, "source", "") or ""
        target = getattr(rel, "target", "") or ""
        if not source or not target:
            continue
        lines.append(
            f"r:{rtype}Shape a sh:NodeShape ;\n"
            f"    sh:targetSubjectsOf r:{rtype} ;\n"
            f"    sh:class c:{source} ;\n"
            f"    sh:property [ sh:path r:{rtype} ; sh:class c:{target} ;\n"
            f"        sh:severity sh:Violation ] .")
    return "\n\n".join(lines)


def string_check(ontology, triples: list[str]) -> dict[str, int]:
    """What the current membership test would report on the same data.

    Reimplemented from the graph rather than called, because the pipeline's
    version runs during extraction and this has to run after it. The rule is
    the same one: a label or a relationship type not in the declared list.
    """
    declared_classes = set(ontology.nodes)
    declared_rels = set(ontology.relationships)
    bad_labels: Counter = Counter()
    bad_rels: Counter = Counter()
    for line in triples:
        if " rdf:type <" in line or f"rdf:type <{NS}c/" in line:
            label = line.rsplit(f"{NS}c/", 1)[-1].split(">", 1)[0]
            if label not in declared_classes:
                bad_labels[label] += 1
        elif f"<{NS}r/" in line:
            rtype = line.split(f"{NS}r/", 1)[-1].split(">", 1)[0]
            if rtype not in declared_rels:
                bad_rels[rtype] += 1
    return {"undeclared_label_uses": sum(bad_labels.values()),
            "undeclared_labels": len(bad_labels),
            "undeclared_relationship_uses": sum(bad_rels.values()),
            "undeclared_relationship_types": len(bad_rels)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default="v2")
    ap.add_argument("--arms", default="A,C,D,E")
    ap.add_argument("--class-limit", type=int, default=70)
    args = ap.parse_args()

    import observe
    from pyshacl import validate
    from rdflib import Graph

    directory = SNAPSHOTS / (args.tag or "v1")
    arms = [a.strip().upper() for a in args.arms.split(",") if a.strip()]

    run = observe.Run(OUT_ROOT, "shacl-check", {"decisive": {
        "tag": args.tag, "arms": arms, "engine": "pyshacl",
        "constraints": ["required properties", "declared class set",
                        "relationship endpoints"],
        "class_limit": args.class_limit, "seed": 42}})

    with run.stage("ontologies", class_limit=args.class_limit) as out:
        fibo = arms_mod.parse_fibo()
        documents, _ = arms_mod.load_corpus_text()
        scoped = arms_mod.scope_to_corpus(fibo, documents, args.class_limit, 20)
        by_iri = {c["iri"]: c["node"] for c in scoped}
        frequency = arms_mod.document_frequency(
            documents,
            {arms_mod.normalize(b["label"])
             for b in fibo["object_properties"].values()
             if arms_mod.normalize(b["label"])})
        relations = arms_mod.scope_relations(fibo, by_iri, frequency, 40)
        declared = arms_mod.datatype_domains(arms_mod.TTL)
        hierarchy = arms_mod.subsumption_within(fibo, by_iri)
        built = {
            "A": arms_mod.build_arm_a()["ontology"],
            "B": arms_mod.build_arm_b()["ontology"],
            "C": arms_mod.build_fibo_arm(scoped, relations, fibo=fibo,
                                         declared=declared)["ontology"],
            "D": arms_mod.build_fibo_arm(scoped, relations, synonyms=True,
                                         fibo=fibo, declared=declared)["ontology"],
            "E": arms_mod.build_fibo_arm(scoped, relations, subsumption=hierarchy,
                                         fibo=fibo, declared=declared)["ontology"],
        }
        out["conditions"] = {a: len(built[a].nodes) for a in arms}

    results: dict[str, Any] = {}
    for arm in arms:
        ontology = built[arm]
        with run.stage(f"validate.{arm}", arm=arm) as out:
            triples = cq_suite.snapshot_triples(directory, [arm])
            data = Graph()
            data.parse(data=cq_suite.PREFIXES + "\n".join(triples),
                       format="turtle")
            shapes = Graph()
            shapes.parse(data=shapes_for(ontology, arm), format="turtle")
            try:
                conforms, report_graph, _ = validate(
                    data, shacl_graph=shapes, inference="none",
                    abort_on_first=False, meta_shacl=False, advanced=False)
                kinds: Counter = Counter()
                paths: Counter = Counter()
                from rdflib.namespace import Namespace

                SH = Namespace("http://www.w3.org/ns/shacl#")
                for result in report_graph.subjects(None, SH.ValidationResult):
                    component = report_graph.value(result, SH.sourceConstraintComponent)
                    path = report_graph.value(result, SH.resultPath)
                    kinds[str(component).rsplit("#", 1)[-1]] += 1
                    if path is not None:
                        paths[str(path).rsplit("/", 1)[-1].rsplit("#", 1)[-1]] += 1
                violations = sum(kinds.values())
                error = ""
            except Exception as exc:  # noqa: BLE001 — recorded, never imputed
                conforms, violations, kinds, paths = None, -1, Counter(), Counter()
                error = f"{type(exc).__name__}: {exc}"

            membership = string_check(ontology, triples)
            out["triples"] = len(triples)
            out["conforms"] = conforms
            out["shacl_violations"] = violations
            out["membership_findings"] = (
                membership["undeclared_label_uses"]
                + membership["undeclared_relationship_uses"])
            if error:
                out["error"] = error

            results[arm] = {
                "declared_classes": len(ontology.nodes),
                "declared_relationships": len(ontology.relationships),
                "triples": len(triples), "conforms": conforms,
                "shacl_violations": violations,
                "violation_kinds": dict(kinds.most_common(8)),
                "violation_paths": dict(paths.most_common(8)),
                "membership_check": membership,
                "error": error,
            }

    payload = {
        "contract": "log2026.shacl_check.v1",
        "question": ("How much does a real constraint checker see that the "
                     "membership test in the pipeline does not?"),
        "method": ("SHACL shapes generated from each condition's own ontology — "
                   "required properties, the declared class set, and "
                   "relationship endpoint classes — run by pySHACL over the "
                   "committed snapshots, against a reimplementation of the "
                   "pipeline's label-and-type membership test on the same "
                   "triples"),
        "claim_boundary": ("The shapes cover three constraint kinds, not "
                           "everything an ontology can express; cardinality "
                           "beyond minCount, datatypes and disjointness are not "
                           "modelled. A relationship type the ontology never "
                           "declared has no shape and so cannot be objected to, "
                           "which is why the SHACL and membership totals are "
                           "reported side by side rather than as a ratio — "
                           "neither bounds the other. Violations are counted on "
                           "data already written, since validation is off by "
                           "default in the pipeline; this measures the gap "
                           "rather than closing it."),
        "tag": args.tag, "by_condition": results,
    }
    (run.dir / "shacl_check.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    # Which constraint kinds only one of the two can express. This, not a
    # count, is what the comparison is for.
    ONLY_SHACL = {"ClassConstraintComponent": "relationship endpoints are the "
                                              "classes the type declares",
                  "MinCountConstraintComponent": "a class's required property "
                                                 "is present"}
    print()
    print(f"{'cond':5s} {'triples':>8s} {'undeclared names':>17s} "
          f"{'endpoint/required':>18s}")
    for arm in arms:
        cell = results[arm]
        membership = (cell["membership_check"]["undeclared_label_uses"]
                      + cell["membership_check"]["undeclared_relationship_uses"])
        structural = sum(count for kind, count in cell["violation_kinds"].items()
                         if kind in ONLY_SHACL)
        print(f"{arm:5s} {cell['triples']:8d} {membership:17d} {structural:18d}")
    print("\nundeclared names  = what the pipeline's membership test can see")
    print("endpoint/required = what only a constraint checker can see; the "
          "membership test cannot express these at any count")
    for arm in arms:
        kinds = results[arm]["violation_kinds"]
        if kinds:
            print(f"\ncondition {arm}, by constraint kind:")
            for kind, count in kinds.items():
                note = ONLY_SHACL.get(kind, "")
                print(f"  {count:6d}  {kind:32s} {note}")
            break
    print("\nA relationship type the ontology never declared has no shape, so "
          "SHACL cannot object to it. The two views are complementary and "
          "neither total bounds the other.")

    run.finish({"by_condition": {a: {
        "shacl_violations": results[a]["shacl_violations"],
        "membership": (results[a]["membership_check"]["undeclared_label_uses"]
                       + results[a]["membership_check"]["undeclared_relationship_uses"])}
        for a in arms},
        "artifact": str((run.dir / "shacl_check.json").relative_to(ROOT))})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
