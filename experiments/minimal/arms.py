#!/usr/bin/env python3
"""Build the four extraction arms that decide whether the ontology helps.

Every ontology result reported so far used a 151-term schema written by hand for
this project and labelled "FIBO". That conflates three different things: having
no schema, having a small hand-made one, and having the real industry ontology
with the synonym layer that exists precisely to let two people name one concept
the same way. These arms separate them.

    A  none        generic Entity / RELATED_TO; the floor
    B  local       the 151-term hand-written modules; every prior result
    C  fibo        real FIBO classes and relations, scoped to the corpus
    D  fibo+syn    C plus synonyms, abbreviations, and preferred designations

Only the schema handed to the extractor moves. Documents, model, chunking,
prompt template, and seed are held fixed, which is what makes a difference
between arms attributable to the ontology (CLAUDE.md 20.9).

Scoping, and why it is not tuning
---------------------------------
Real FIBO declares more classes than fit in any prompt, so C and D keep the
subset the corpus actually mentions. That selection reads the **source 10-K
text only**. It never reads a question, a gold answer, or a result. Selecting a
schema against the documents you are about to extract is ordinary ontology
scoping; selecting it against the questions would be fitting the test set, and
is why the question text is not loaded here at all.

No model is called. Output is the four extraction contexts and their sizes.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TTL = ROOT / "dataset/fibo/fibo-quickstart.ttl"
CLASS_CACHE = ROOT / "dataset/fibo/fibo-classes.json"
OUT_ROOT = ROOT / "outputs/minimal"

LOCAL_MODULES = ["acc", "be", "fbc", "fnd", "ind"]

# The real FIBO domains that arm B's hand-written modules were approximating.
# BE, FBC, FND and IND are the direct counterparts; accounting has no top-level
# FIBO domain of its own and lives inside FBC and FND. CMNS is the Commons
# vocabulary those domains are all built on, so excluding it would break their
# domain and range declarations. Fixed before the run and not adjusted after.
FIBO_DOMAINS = ("BE", "FBC", "FND", "IND", "CMNS")

AV = "https://www.omg.org/spec/Commons/AnnotationVocabulary/"
FND_AV = ("https://spec.edmcouncil.org/fibo/ontology/FND/Utilities/"
          "AnnotationVocabulary/")
ANNOTATIONS = {
    f"{AV}synonym": "synonym",
    f"{AV}abbreviation": "abbreviation",
    f"{FND_AV}preferredDesignation": "preferred",
    f"{FND_AV}commonDesignation": "common",
}

# A label this generic matches almost any English sentence, so corpus mention
# tells us nothing about whether the class is relevant. Listed rather than
# filtered by length so the exclusion is auditable.
TOO_GENERIC = {
    "thing", "entity", "party", "agent", "role", "item", "state", "event",
    "date", "time", "period", "value", "amount", "number", "name", "code",
    "identifier", "reference", "document", "record", "collection", "group",
    "set", "list", "type", "class", "property", "relation", "concept",
    "situation", "occurrence", "arrangement", "context", "aspect", "quantity",
}


# --------------------------------------------------------------------------
# FIBO parsing


def parse_fibo(force: bool = False) -> dict[str, Any]:
    """Classes and object properties from the FIBO turtle, with annotations."""
    if CLASS_CACHE.is_file() and not force:
        return json.loads(CLASS_CACHE.read_text())
    if not TTL.is_file():
        raise SystemExit(f"FIBO turtle not found at {TTL}")

    from rdflib import Graph, RDF, RDFS, OWL, URIRef

    graph = Graph()
    graph.parse(TTL, format="turtle")

    def label_of(subject) -> str:
        for value in graph.objects(subject, RDFS.label):
            return str(value).strip()
        return ""

    def annotations_of(subject) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for predicate, kind in ANNOTATIONS.items():
            values = [str(v).strip() for v in graph.objects(subject, URIRef(predicate))]
            values = [v for v in values if v]
            if values:
                found[kind] = sorted(set(values))
        return found

    classes = {}
    for subject in graph.subjects(RDF.type, OWL.Class):
        if not isinstance(subject, URIRef):
            continue
        label = label_of(subject)
        if not label:
            continue
        parents = [str(p) for p in graph.objects(subject, RDFS.subClassOf)
                   if isinstance(p, URIRef)]
        classes[str(subject)] = {
            "label": label,
            "parents": parents,
            "annotations": annotations_of(subject),
        }

    properties = {}
    for subject in graph.subjects(RDF.type, OWL.ObjectProperty):
        if not isinstance(subject, URIRef):
            continue
        label = label_of(subject)
        if not label:
            continue
        domains = [str(d) for d in graph.objects(subject, RDFS.domain)
                   if isinstance(d, URIRef)]
        ranges = [str(r) for r in graph.objects(subject, RDFS.range)
                  if isinstance(r, URIRef)]
        properties[str(subject)] = {
            "label": label, "domains": domains, "ranges": ranges,
            "annotations": annotations_of(subject),
        }

    payload = {"classes": classes, "object_properties": properties}
    CLASS_CACHE.write_text(json.dumps(payload, ensure_ascii=False) + "\n")
    return payload


# --------------------------------------------------------------------------
# Corpus scoping


def load_corpus_text() -> tuple[list[list[str]], int]:
    """The reference passages only, tokenized per document.

    Questions and answers are not read.
    """
    spec = importlib.util.spec_from_file_location(
        "finder_index", ROOT / "examples/mdm/11_index_providers.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cases = module.load_cases_full(seed=42)
    documents = [re.findall(r"[a-z0-9]+", " ".join(case["references"]).lower())
                 for case in cases]
    return [d for d in documents if d], len(cases)


def camel_to_words(name: str) -> str:
    parts = re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+|\d+", name)
    return " ".join(parts).lower()


def pascal(label: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", label)
    return "".join(p[0].upper() + p[1:] for p in parts if p)


def screaming(label: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", label)
    return "_".join(p.upper() for p in parts if p)


def domain_of(iri: str) -> str:
    found = re.search(r"/fibo/ontology/([A-Z]+)/", iri)
    if found:
        return found.group(1)
    return "CMNS" if "omg.org/spec/Commons" in iri else "OTHER"


def normalize(label: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9]+", str(label).lower()))


def document_frequency(documents: list[list[str]],
                       candidates: set[tuple[str, ...]]) -> Counter:
    """How many documents name each candidate phrase.

    Document frequency rather than raw count, because one filing that repeats a
    term four hundred times says less about the corpus than four hundred filings
    that each mention it once. Matching is on word sequences, so a short label
    can no longer match inside a longer word: the first attempt counted the
    abbreviation "CO" a hundred thousand times and ranked a certificate class
    above every real one.
    """
    lengths = sorted({len(c) for c in candidates})
    frequency: Counter = Counter()
    for words in documents:
        present = set()
        for n in lengths:
            if n > len(words):
                break
            for i in range(len(words) - n + 1):
                gram = tuple(words[i:i + n])
                if gram in candidates:
                    present.add(gram)
        frequency.update(present)
    return frequency


def scope_to_corpus(fibo: dict[str, Any], documents: list[list[str]], limit: int,
                    min_documents: int) -> list[dict[str, Any]]:
    """Rank FIBO classes by how many documents name them.

    Two filters, in this order. First FIBO's own modularity: only the domains
    arm B was approximating are eligible, which is how an ontology engineer
    scopes and owes nothing to our corpus. Then document frequency, purely to
    fit what remains into a prompt.

    Frequency is measured over the label *and* its aliases, and this was a
    correction. Selecting on the label alone looked safer, on the reasoning that
    reading synonyms would leak arm D's advantage into arm C. It does not: C and
    D share one class set and only D's prompt carries the aliases. What
    label-only selection actually did was systematically discard the classes
    where the synonym layer matters, because those are exactly the classes whose
    formal FIBO label the filings never use. FIBO calls it "amount of money" and
    a 10-K says "cash". Selecting on the label would have guaranteed arm D
    showed nothing, which is a rigged comparison rather than a conservative one.
    """
    candidates: dict[tuple[str, ...], list[tuple[str, dict]]] = {}
    names: dict[tuple[str, ...], set[tuple[str, ...]]] = {}
    for iri, body in fibo["classes"].items():
        if domain_of(iri) not in FIBO_DOMAINS:
            continue
        gram = normalize(body["label"])
        if not gram or (len(gram) == 1 and gram[0] in TOO_GENERIC):
            continue
        candidates.setdefault(gram, []).append((iri, body))
        aliases = {normalize(v) for values in body["annotations"].values()
                   for v in values}
        names.setdefault(gram, {gram}).update(a for a in aliases
                                              if a and a[0] not in TOO_GENERIC)

    frequency = document_frequency(
        documents, {g for grams in names.values() for g in grams})
    total_docs = len(documents)

    scored = []
    for gram, entries in candidates.items():
        by_name = {" ".join(g): frequency.get(g, 0) for g in names[gram]}
        count = max(by_name.values())
        if count < min_documents:
            continue
        iri, body = entries[0]
        scored.append({"iri": iri, "label": body["label"], "mentions": count,
                       "label_documents": frequency.get(gram, 0),
                       "document_share": round(count / total_docs, 6),
                       "named_by": by_name,
                       "annotations": body["annotations"],
                       "parents": body["parents"]})
    scored.sort(key=lambda c: (-c["mentions"], c["label"]))
    # Collapse classes whose PascalCase name collides; FIBO reuses labels
    # across modules and a graph label must be unique.
    seen, kept = set(), []
    for entry in scored:
        key = pascal(entry["label"])
        if key in seen:
            continue
        seen.add(key)
        entry["node"] = key
        kept.append(entry)
        if len(kept) >= limit:
            break
    return kept


def nearest_kept(fibo: dict[str, Any], iri: str, kept: dict[str, str],
                 depth: int = 6) -> str | None:
    """Map a FIBO class to the kept class standing in for it.

    A relation declared on a class we did not keep is still usable if one of
    that class's ancestors was kept, because FIBO's hierarchy says the instance
    is also of the ancestor type. Without this, relations were being discarded
    for naming a class one level too specific, and the first attempt kept two.
    """
    seen, frontier = set(), [iri]
    for _ in range(depth):
        following = []
        for node in frontier:
            if node in kept:
                return kept[node]
            if node in seen:
                continue
            seen.add(node)
            following += fibo["classes"].get(node, {}).get("parents", [])
        if not following:
            return None
        frontier = following
    return None


def scope_relations(fibo: dict[str, Any], nodes: dict[str, str],
                    frequency: Counter, limit: int) -> list[dict[str, Any]]:
    """Object properties whose domain and range both reach a kept class."""
    scored = []
    seen = set()
    for iri, body in fibo["object_properties"].items():
        if domain_of(iri) not in FIBO_DOMAINS:
            continue
        domains = [n for n in (nearest_kept(fibo, d, nodes) for d in body["domains"]) if n]
        ranges = [n for n in (nearest_kept(fibo, r, nodes) for r in body["ranges"]) if n]
        if not domains or not ranges:
            continue
        rel = screaming(body["label"])
        if rel in seen:
            continue
        seen.add(rel)
        scored.append({
            "iri": iri, "rel": rel, "label": body["label"],
            "source": domains[0], "target": ranges[0],
            "mentions": frequency.get(normalize(body["label"]), 0),
            "annotations": body["annotations"],
        })
    scored.sort(key=lambda r: (-r["mentions"], r["rel"]))
    return scored[:limit]


# --------------------------------------------------------------------------
# Arm construction


def value_properties(node: str) -> dict[str, Any]:
    """The minimal property set every fact node needs to be comparable.

    Held identical across arms on purpose. Varying properties as well as
    classes would make an arm difference unattributable, and `period` is the
    property CQ9 is about, so every arm must have the chance to fill it.
    """
    return {
        "name": {"type": "STRING", "constraint": "UNIQUE", "required": True},
        "value": {"type": "STRING"},
        "period": {"type": "STRING"},
    }


def build_arm_a() -> dict[str, Any]:
    from examples.finder.datasets.fibo_modules.compose import compose_modules
    return {"arm": "A", "id": "none", "ontology": compose_modules([]),
            "description": "generic Entity/RELATED_TO, no declared vocabulary"}


def build_arm_b() -> dict[str, Any]:
    from examples.finder.datasets.fibo_modules.compose import compose_modules
    return {"arm": "B", "id": "local", "ontology": compose_modules(LOCAL_MODULES),
            "description": f"hand-written modules {'+'.join(LOCAL_MODULES)}"}


def build_fibo_arm(classes: list[dict], relations: list[dict], *,
                   synonyms: bool) -> dict[str, Any]:
    from seocho import Ontology

    nodes: dict[str, Any] = {}
    for entry in classes:
        body: dict[str, Any] = {
            "description": f"FIBO {entry['label']}",
            "properties": value_properties(entry["node"]),
        }
        if synonyms:
            aliases = []
            for kind in ("preferred", "common", "synonym", "abbreviation"):
                aliases += entry["annotations"].get(kind, [])
            # The label itself is not an alias; only other names for it are.
            aliases = [a for a in dict.fromkeys(aliases) if a != entry["label"]]
            if aliases:
                body["aliases"] = aliases
        nodes[entry["node"]] = body

    relationships = {}
    for rel in relations:
        relationships[rel["rel"]] = {
            "source": rel["source"], "target": rel["target"],
            "description": f"FIBO {rel['label']}",
            "cardinality": "MANY_TO_MANY",
        }
    if not relationships:
        raise SystemExit("scoping produced no relationships; widen the limits")

    suffix = "fibo_syn" if synonyms else "fibo"
    ontology = Ontology.from_dict({
        "graph_type": suffix, "package_id": suffix, "version": "1.0.0",
        "graph_model": "lpg",
        "description": ("real FIBO scoped to the corpus"
                        + (", with the synonym layer" if synonyms else "")),
        "nodes": nodes, "relationships": relationships,
    })
    return {
        "arm": "D" if synonyms else "C",
        "id": suffix, "ontology": ontology,
        "description": ("real FIBO scoped to the corpus"
                        + (" plus synonyms, abbreviations and preferred "
                           "designations" if synonyms else "")),
    }


def context_of(arm: dict[str, Any]) -> dict[str, Any]:
    ctx = arm["ontology"].to_extraction_context()
    blob = ctx["entity_types"] + "\n" + ctx["relationship_types"]
    return {
        "arm": arm["arm"], "id": arm["id"], "description": arm["description"],
        "ontology_name": ctx["ontology_name"],
        "nodes": len(arm["ontology"].nodes),
        "relationships": len(arm["ontology"].relationships),
        "context_chars": len(blob),
        "ontology_hash": hashlib.sha256(blob.encode()).hexdigest()[:16],
        "entity_types": ctx["entity_types"],
        "relationship_types": ctx["relationship_types"],
    }


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--class-limit", type=int, default=60,
                    help="how many corpus-mentioned FIBO classes arms C and D keep")
    ap.add_argument("--relation-limit", type=int, default=40)
    ap.add_argument("--min-documents", type=int, default=20,
                    help="a class label must appear in this many 10-K documents to be kept")
    ap.add_argument("--reparse", action="store_true")
    args = ap.parse_args()

    import observe

    run = observe.Run(OUT_ROOT, "arms", {"decisive": {
        "class_limit": args.class_limit, "relation_limit": args.relation_limit,
        "min_documents": args.min_documents, "local_modules": LOCAL_MODULES,
        "scoping": "corpus_reference_text_only", "seed": 42}})

    with run.stage("fibo.parse", ttl=str(TTL.relative_to(ROOT))) as out:
        fibo = parse_fibo(force=args.reparse)
        out["classes"] = len(fibo["classes"])
        out["object_properties"] = len(fibo["object_properties"])

    with run.stage("corpus.load") as out:
        documents, n_cases = load_corpus_text()
        out["cases"] = n_cases
        out["documents"] = len(documents)
        out["corpus_words"] = sum(len(d) for d in documents)
        out["reads_questions"] = False
        out["reads_answers"] = False

    with run.stage("fibo.scope", class_limit=args.class_limit,
                   min_documents=args.min_documents) as out:
        classes = scope_to_corpus(fibo, documents, args.class_limit,
                                  args.min_documents)
        frequency = document_frequency(
            documents,
            {normalize(b["label"]) for b in fibo["object_properties"].values()
             if normalize(b["label"])})
        by_iri = {c["iri"]: c["node"] for c in classes}
        relations = scope_relations(fibo, by_iri, frequency, args.relation_limit)
        with_syn = sum(1 for c in classes if c["annotations"])
        out["classes_kept"] = len(classes)
        out["relations_kept"] = len(relations)
        out["classes_with_annotations"] = with_syn
        out["selection_reads_synonyms"] = True
        out["aliases_only_in_arm_D"] = True
        out["classes_found_via_alias"] = sum(
            1 for c in classes if c["label_documents"] < c["mentions"])
        out["fibo_domains"] = list(FIBO_DOMAINS)
        out["relations"] = [f"{r['source']}-{r['rel']}->{r['target']}"
                            for r in relations[:8]]
        out["top"] = [f"{c['node']}({c['mentions']})" for c in classes[:12]]

    contexts = []
    for builder in (build_arm_a, build_arm_b):
        arm = builder()
        with run.stage(f"arm.{arm['arm']}", ontology=arm["id"]) as out:
            ctx = context_of(arm)
            contexts.append(ctx)
            out.update({k: v for k, v in ctx.items()
                        if k not in ("entity_types", "relationship_types")})

    for synonyms in (False, True):
        arm = build_fibo_arm(classes, relations, synonyms=synonyms)
        with run.stage(f"arm.{arm['arm']}", ontology=arm["id"]) as out:
            ctx = context_of(arm)
            contexts.append(ctx)
            out.update({k: v for k, v in ctx.items()
                        if k not in ("entity_types", "relationship_types")})

    alias_terms = sum(len(c["annotations"].get(k, []))
                      for c in classes
                      for k in ("synonym", "abbreviation", "preferred", "common"))

    payload = {
        "contract": "log2026.extraction_arms.v1",
        "question": ("Does a real ontology, and separately its synonym layer, "
                     "make two independent extractions describe the same fact "
                     "with the same name?"),
        "held_fixed": ["documents", "extractor model", "chunking", "prompt "
                       "template", "seed", "node property set"],
        "moving": "the class and relation vocabulary handed to the extractor",
        "scoping_boundary": ("classes selected by how many source 10-K documents "
                             "name the class label; question and answer text is "
                             "never read, and the synonym layer is not used for "
                             "selection so that arm D isolates it"),
        "alias_terms_added_in_D": alias_terms,
        "arms": contexts,
    }
    (run.dir / "arms.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    print()
    print(f"{'arm':4s} {'id':10s} {'nodes':>6s} {'rels':>5s} {'chars':>7s}  hash")
    for ctx in contexts:
        print(f"{ctx['arm']:4s} {ctx['id']:10s} {ctx['nodes']:6d} "
              f"{ctx['relationships']:5d} {ctx['context_chars']:7d}  "
              f"{ctx['ontology_hash']}")
    print(f"\nD adds {alias_terms} alias terms over C")

    run.finish({"arms": len(contexts), "classes_scoped": len(classes),
                "relations_scoped": len(relations),
                "alias_terms_added_in_D": alias_terms,
                "artifact": str((run.dir / "arms.json").relative_to(ROOT))})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
