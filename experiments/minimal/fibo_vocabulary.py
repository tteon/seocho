"""Extract the real FIBO vocabulary, including its SKOS-derived synonym layer.

The local YAML modules carry 151 terms, hand-written for this project. Real FIBO
carries roughly sixteen thousand labels plus an annotation vocabulary that exists
precisely to give a concept more than one name:

    cmns-av:synonym                 an alternative term for the same concept
    cmns-av:abbreviation            the short form
    fibo-fnd-utl-av:preferredDesignation   the term the industry prefers
    fibo-fnd-utl-av:commonDesignation      the term in common use

FIBO instructs implementers not to use skos:altLabel directly and to use these
subproperties instead, which is why a plain SKOS reader finds almost nothing
here. Reading only rdfs:label would miss the entire synonym layer, and the
synonym layer is the part relevant to making two extractions agree on a name.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TTL = ROOT / "dataset/fibo/fibo-quickstart.ttl"
CACHE = ROOT / "dataset/fibo/fibo-vocabulary.json"

LABEL_PREDICATES = {
    "http://www.w3.org/2000/01/rdf-schema#label": "label",
    "http://www.w3.org/2004/02/skos/core#prefLabel": "label",
    "https://www.omg.org/spec/Commons/AnnotationVocabulary/synonym": "synonym",
    "https://www.omg.org/spec/Commons/AnnotationVocabulary/abbreviation": "abbreviation",
    "https://spec.edmcouncil.org/fibo/ontology/FND/Utilities/AnnotationVocabulary/preferredDesignation": "preferred",
    "https://spec.edmcouncil.org/fibo/ontology/FND/Utilities/AnnotationVocabulary/commonDesignation": "common",
}


@dataclass
class Vocabulary:
    terms: set[str] = field(default_factory=set)          # single words
    phrases: set[str] = field(default_factory=set)        # full labels, normalized
    by_kind: dict[str, int] = field(default_factory=dict)
    subjects: int = 0

    def as_dict(self) -> dict:
        return {"terms": sorted(self.terms), "phrases": sorted(self.phrases),
                "by_kind": self.by_kind, "subjects": self.subjects}


STOPWORDS = {"the", "a", "an", "of", "and", "or", "to", "in", "for", "with", "by"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", str(text).lower())
            if len(w) > 2 and w not in STOPWORDS}


def build(force: bool = False) -> Vocabulary:
    """Parse the FIBO turtle once and cache the extracted vocabulary."""
    if CACHE.is_file() and not force:
        raw = json.loads(CACHE.read_text())
        return Vocabulary(terms=set(raw["terms"]), phrases=set(raw["phrases"]),
                          by_kind=raw["by_kind"], subjects=raw["subjects"])
    if not TTL.is_file():
        raise SystemExit(f"FIBO turtle not found at {TTL}")

    from rdflib import Graph

    graph = Graph()
    graph.parse(TTL, format="turtle")

    vocab = Vocabulary()
    counts: dict[str, int] = {}
    subjects = set()
    for predicate, kind in LABEL_PREDICATES.items():
        from rdflib import URIRef

        for subject, _, value in graph.triples((None, URIRef(predicate), None)):
            text = str(value).strip()
            if not text:
                continue
            counts[kind] = counts.get(kind, 0) + 1
            subjects.add(subject)
            vocab.phrases.add(re.sub(r"\s+", " ", text.lower()))
            vocab.terms |= _words(text)
    vocab.by_kind = counts
    vocab.subjects = len(subjects)

    CACHE.write_text(json.dumps(vocab.as_dict(), indent=2, ensure_ascii=False) + "\n")
    return vocab


if __name__ == "__main__":
    v = build(force=True)
    print(f"subjects with a label: {v.subjects:,}")
    print(f"distinct words:        {len(v.terms):,}")
    print(f"distinct phrases:      {len(v.phrases):,}")
    print(f"by annotation kind:    {v.by_kind}")
