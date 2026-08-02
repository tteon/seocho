#!/usr/bin/env python3
"""Build the four schema descriptions an agent could be given, and diff them.

A text2cypher system tells the model what is in the graph, and standard practice
is to ask the graph: introspect the store and stringify what it reports. The
published work asks how to serialise that, how to prune it per question and how
to compress it. All of it treats the introspected schema as the thing to be
described.

But the graph was built from an ontology, and the two are different documents.
Introspection reports what an extractor produced. The ontology reports what was
supposed to be produced. Where a language model did the extracting, that gap is
the thing nobody measures, and this measures it.

Four descriptions, each built by a rule rather than by hand:

    introspected        every label, relationship type and property key the
                        store reports — the standard practice
    declared            the ontology's own classes, relationships and
                        properties
    declared_present    the ontology restricted to what was actually extracted,
                        because an ontology declaring seventy classes of which
                        seventeen exist sends an agent looking for fifty-three
                        things that are not there
    introspected_typed  introspection plus which properties hold something a
                        query can compare

The output is the four descriptions as they would appear in a prompt, their
token cost, and what each contains that the others do not. Nothing here runs an
agent — that is the second half. This is the half that establishes there is
something to test.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for path in (str(ROOT / "experiments/minimal"), str(HERE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from dotenv import dotenv_values  # noqa: E402

for _key, _value in dotenv_values(ROOT / ".env").items():
    if _value is not None:
        os.environ.setdefault(_key, _value)

import arms as arms_mod  # noqa: E402
import parallel  # noqa: E402

URI = os.environ.get("SEOCHO_NEO4J_URI", "bolt://localhost:7687")
OUT_ROOT = ROOT / "outputs/minimal"
CATEGORIES = ["Accounting", "Company overview", "Financials", "Footnotes",
              "Governance", "Legal", "Risk", "Shareholder return"]
COVERAGE = 0.90
BOOKKEEPING = re.compile(r"^_")


def auth() -> tuple[str, str]:
    return (os.environ.get("NEO4J_USER", "neo4j"),
            os.environ.get("NEO4J_PASSWORD", ""))


def database_for(tag: str, category: str) -> str:
    return (f"{re.sub(r'[^a-z0-9]', '', tag.lower())}cat"
            f"{re.sub(r'[^a-z0-9]', '', category.lower())}")


def introspect(driver, database: str) -> dict[str, Any]:
    """What the store reports about itself, which is what practice describes."""
    with driver.session(database=database) as session:
        labels = {r["l"]: r["c"] for r in session.run(
            "MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) AS c")}
        rels = {r["t"]: r["c"] for r in session.run(
            "MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c")}
        keys = {r["k"]: r["c"] for r in session.run(
            "MATCH (n) UNWIND keys(n) AS k RETURN k, count(*) AS c")}
        numeric = {r["k"] for r in session.run(
            "MATCH (n) UNWIND keys(n) AS k WITH k, n[k] AS v "
            "WHERE v IS NOT NULL AND (v IS :: INTEGER OR v IS :: FLOAT) "
            "RETURN DISTINCT k")} if True else set()
    return {"database": database, "labels": labels, "relationship_types": rels,
            "property_keys": keys, "numeric_keys": sorted(numeric)}


def render(labels, rels, keys, *, numeric: set[str] = frozenset(),
           note: str = "") -> str:
    """One description, in the shape a prompt would carry."""
    lines = []
    if note:
        lines.append(f"# {note}")
    lines.append("Node labels:")
    lines += [f"  {l}" for l in sorted(labels)]
    lines.append("Relationship types:")
    lines += [f"  {r}" for r in sorted(rels)]
    lines.append("Node properties:")
    for key in sorted(keys):
        kind = " (number, comparable)" if key in numeric else ""
        lines.append(f"  {key}{kind}")
    return "\n".join(lines)


def approx_tokens(text: str) -> int:
    """Whitespace words plus punctuation, close enough to compare descriptions."""
    return len(re.findall(r"\w+|[^\w\s]", text))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default="v2")
    ap.add_argument("--condition", default="A",
                    help="which ontology condition the loaded graphs came from")
    ap.add_argument("--class-limit", type=int, default=70)
    args = ap.parse_args()

    import observe
    from neo4j import GraphDatabase

    run = observe.Run(OUT_ROOT, "schema-sources", {"decisive": {
        "tag": args.tag, "condition": args.condition,
        "coverage_threshold": COVERAGE, "class_limit": args.class_limit,
        "seed": 42}})

    with run.stage("ontology", condition=args.condition) as out:
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
        declared_props = arms_mod.datatype_domains(arms_mod.TTL)
        hierarchy = arms_mod.subsumption_within(fibo, by_iri)
        built = {
            "A": arms_mod.build_arm_a()["ontology"],
            "C": arms_mod.build_fibo_arm(scoped, relations, fibo=fibo,
                                         declared=declared_props)["ontology"],
            "D": arms_mod.build_fibo_arm(scoped, relations, synonyms=True,
                                         fibo=fibo,
                                         declared=declared_props)["ontology"],
            "E": arms_mod.build_fibo_arm(scoped, relations,
                                         subsumption=hierarchy, fibo=fibo,
                                         declared=declared_props)["ontology"],
        }
        ontology = built[args.condition]
        declared_labels = set(ontology.nodes)
        declared_rels = set(ontology.relationships)
        declared_keys = {name for node in ontology.nodes.values()
                         for name in node.properties}
        out["declared_labels"] = len(declared_labels)
        out["declared_relationships"] = len(declared_rels)
        out["declared_properties"] = len(declared_keys)

    driver = GraphDatabase.driver(URI, auth=auth())
    try:
        with run.stage("introspect", categories=len(CATEGORIES)) as out:
            rows = parallel.io_map(
                lambda c: introspect(driver, database_for(args.tag, c)),
                CATEGORIES)
            surveyed = [r for r in rows if r]
            labels: Counter = Counter()
            rels: Counter = Counter()
            keys: Counter = Counter()
            numeric: set[str] = set()
            for row in surveyed:
                labels.update(row["labels"])
                rels.update(row["relationship_types"])
                keys.update(row["property_keys"])
                numeric |= set(row["numeric_keys"])
            out["labels"] = len(labels)
            out["relationship_types"] = len(rels)
            out["property_keys"] = len(keys)
            out["numeric_keys"] = len(numeric)
    finally:
        driver.close()

    with run.stage("describe") as out:
        present_labels = set(labels)
        domain_keys = {k for k in keys if not BOOKKEEPING.match(k)}

        descriptions = {
            "introspected": render(
                present_labels, set(rels), set(keys),
                note="every label, relationship type and property key the store reports"),
            "declared": render(
                declared_labels, declared_rels, declared_keys,
                note="the ontology's own vocabulary"),
            "declared_present": render(
                declared_labels & present_labels,
                declared_rels & set(rels),
                declared_keys & domain_keys,
                note="the ontology restricted to what was extracted"),
            "introspected_typed": render(
                present_labels, set(rels), set(keys), numeric=numeric,
                note="introspection, marking which properties a query can compare"),
        }
        sizes = {k: {"characters": len(v), "approx_tokens": approx_tokens(v)}
                 for k, v in descriptions.items()}
        out.update({k: v["approx_tokens"] for k, v in sizes.items()})

    with run.stage("diff") as out:
        undeclared_labels = sorted(present_labels - declared_labels)
        never_extracted = sorted(declared_labels - present_labels)
        undeclared_rels = sorted(set(rels) - declared_rels)
        bookkeeping = sorted(k for k in keys if BOOKKEEPING.match(k))
        gap = {
            "labels_present_but_undeclared": len(undeclared_labels),
            "labels_declared_but_never_extracted": len(never_extracted),
            "relationship_types_present_but_undeclared": len(undeclared_rels),
            "bookkeeping_keys_in_introspection": len(bookkeeping),
            "labels_in_common": len(declared_labels & present_labels),
        }
        out.update(gap)
        out["undeclared_examples"] = undeclared_labels[:10]

    payload = {
        "contract": "log2026.schema_sources.v1",
        "question": ("How far apart are the schema an agent is usually given "
                     "and the ontology the graph was built from?"),
        "method": ("four descriptions built by rule from the loaded databases "
                   "and the condition's ontology — introspected, declared, "
                   "declared restricted to what was extracted, and introspected "
                   "with comparable properties marked — then compared on size "
                   "and on what each contains that the others do not"),
        "claim_boundary": ("A static comparison of descriptions. It establishes "
                           "that the two sources differ and by how much; it "
                           "does not show that the difference changes what an "
                           "agent can query, which needs the agent to be run."),
        "tag": args.tag, "condition": args.condition,
        "declared": {"labels": len(declared_labels),
                     "relationship_types": len(declared_rels),
                     "properties": len(declared_keys)},
        "introspected": {"labels": len(labels),
                         "relationship_types": len(rels),
                         "property_keys": len(keys),
                         "numeric_keys": len(numeric)},
        "gap": gap,
        "sizes": sizes,
        "undeclared_labels": undeclared_labels,
        "labels_declared_but_never_extracted": never_extracted,
        "undeclared_relationship_types": undeclared_rels,
        "descriptions": descriptions,
    }
    (run.dir / "schema_sources.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    print()
    print(f"{'description':22s} {'labels':>7s} {'rels':>6s} {'props':>6s} "
          f"{'tokens':>7s}")
    counts = {
        "introspected": (len(labels), len(rels), len(keys)),
        "declared": (len(declared_labels), len(declared_rels), len(declared_keys)),
        "declared_present": (len(declared_labels & present_labels),
                             len(declared_rels & set(rels)),
                             len(declared_keys & domain_keys)),
        "introspected_typed": (len(labels), len(rels), len(keys)),
    }
    for name, (nl, nr, nk) in counts.items():
        print(f"{name:22s} {nl:7d} {nr:6d} {nk:6d} "
              f"{sizes[name]['approx_tokens']:7d}")

    print(f"\nwhere they disagree:")
    print(f"  present but the ontology never declared it   "
          f"{gap['labels_present_but_undeclared']} labels, "
          f"{gap['relationship_types_present_but_undeclared']} relationship types")
    print(f"  declared but never extracted                 "
          f"{gap['labels_declared_but_never_extracted']} labels")
    print(f"  in common                                    "
          f"{gap['labels_in_common']} labels")
    print(f"  bookkeeping keys introspection would include {gap['bookkeeping_keys_in_introspection']}")
    if undeclared_labels:
        print(f"\n  undeclared labels an agent would be told about: "
              f"{', '.join(undeclared_labels[:8])}")

    run.finish({"gap": gap,
                "tokens": {k: v["approx_tokens"] for k, v in sizes.items()},
                "artifact": str((run.dir / "schema_sources.json").relative_to(ROOT))})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
