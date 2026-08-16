"""Fact-level provenance chain (organ 3).

Architecture (hadry): **PostgreSQL is the ground truth / system of record; the graph is
a projection of it** (see :mod:`seocho.provenance_store`). This module is the fact-level
provenance CHAIN that lives on that ground truth — per extracted fact, an auditable
"who/what/when/from-where derived it": correct PROV-O, content-addressed so the
ground-truth row / graph node / bundle IRI all correlate and a re-index is idempotent,
and **value-free** (references facts by id, never embeds the object content — so the
provenance itself is not an exfiltration channel).

The design review's anti-duplication point still holds and is honored: cell/sub-cell
masking stays ``risk/preflight.OntologyDisclosurePolicy.filter_record`` and principal
authz stays ``agent/identity.AgentPrincipal`` — not re-implemented. This module adds only
the provenance chain; ``provenance_store`` adds the ground-truth rows + classification +
RLS + the governed projection.

Design decisions taken from the review:
- **content-addressed fact_id** = hash(workspace_id, canonical(subject,predicate,object)):
  the graph node, the provenance, and the bundle IRI all derive from it; re-extraction
  upserts on it (idempotent).
- **ONE prov:Bundle per agent-run** (not a file per fact): the run is the Activity, facts
  are Entities it generated, referenced by fact_id IRI. No object values embedded.
- **Correct PROV-O**: run=Activity, agent=SoftwareAgent, ontology_version=the run's Plan;
  fact wasGeneratedBy run, wasDerivedFrom source_doc, wasAttributedTo agent. Non-standard
  bits (source_platform, confidence) under a seocho: namespace.

Access control: the ground truth is gated by RLS (provenance_store); the graph is a
GOVERNED projection that carries the classification forward, and the graph read path
reuses OntologyDisclosurePolicy.filter_record + AgentPrincipal.authorize — so both the
system of record AND its projection enforce sensitivity. This module is the provenance
CHAIN only.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Sequence

_PROV = "http://www.w3.org/ns/prov#"
_SEO = "https://seocho.dev/prov#"


def _canon(value: object) -> str:
    return " ".join(str(value if value is not None else "").strip().lower().split())


def content_fact_id(workspace_id: str, subject: str, predicate: str, obj: str) -> str:
    """Deterministic, content-addressed fact id. Same (workspace, s, p, o) -> same id,
    so the graph node, the provenance row, and the bundle IRI all correlate and a
    re-index is an idempotent upsert (never a duplicate)."""
    key = "|".join(_canon(x) for x in (workspace_id, subject, predicate, obj))
    return "fact_" + hashlib.blake2b(key.encode("utf-8"), digest_size=12).hexdigest()


@dataclass(frozen=True)
class Fact:
    subject: str
    predicate: str
    object: str
    confidence: float = 1.0

    def fact_id(self, workspace_id: str) -> str:
        return content_fact_id(workspace_id, self.subject, self.predicate, self.object)


@dataclass
class ProvenanceRun:
    """One indexing-agent run over one source document — the PROV Activity."""
    run_id: str
    workspace_id: str
    source_doc: str
    ontology_version: str
    source_platform: str = ""
    agent: str = "seocho-indexing-agent"
    generated_at: str = ""
    facts: List[Fact] = field(default_factory=list)

    def fact_ids(self) -> List[str]:
        return [f.fact_id(self.workspace_id) for f in self.facts]

    def to_ttl(self) -> str:
        """Serialize the run's provenance as a PROV-O Turtle bundle. References facts
        by content-addressed IRI only — the object VALUE is never embedded (so the
        provenance is not itself a leak channel)."""
        import rdflib
        from rdflib import Literal, Namespace, RDF, URIRef, XSD

        PROV, SEO = Namespace(_PROV), Namespace(_SEO)
        g = rdflib.Graph()
        g.bind("prov", PROV)
        g.bind("seocho", SEO)

        run = URIRef(f"{_SEO}run/{self.run_id}")
        agent = URIRef(f"{_SEO}agent/{_canon(self.agent).replace(' ', '_') or 'agent'}")
        doc = URIRef(f"{_SEO}doc/{self.source_doc}")
        plan = URIRef(f"{_SEO}ontology/{self.ontology_version}")

        g.add((run, RDF.type, PROV.Activity))
        g.add((run, PROV.used, doc))
        g.add((run, PROV.used, plan))
        g.add((run, PROV.wasAssociatedWith, agent))
        g.add((run, SEO.workspace, Literal(self.workspace_id)))
        if self.source_platform:
            g.add((run, SEO.sourcePlatform, Literal(self.source_platform)))
        if self.generated_at:
            g.add((run, PROV.startedAtTime, Literal(self.generated_at, datatype=XSD.dateTime)))
        g.add((agent, RDF.type, PROV.SoftwareAgent))
        g.add((doc, RDF.type, PROV.Entity))
        g.add((plan, RDF.type, PROV.Plan))

        for f in self.facts:
            fid = URIRef(f"{_SEO}{f.fact_id(self.workspace_id)}")
            g.add((fid, RDF.type, PROV.Entity))
            g.add((fid, PROV.wasGeneratedBy, run))
            g.add((fid, PROV.wasDerivedFrom, doc))
            g.add((fid, PROV.wasAttributedTo, agent))
            g.add((fid, SEO.confidence, Literal(float(f.confidence), datatype=XSD.decimal)))
            # NOTE: subject/predicate/object are NOT emitted — recover the triple from
            # the graph by fact_id; the provenance stays value-free.
        return g.serialize(format="turtle")


def build_run_from_extraction(
    *,
    run_id: str,
    workspace_id: str,
    source_doc: str,
    ontology_version: str,
    relationships: Sequence[Dict[str, object]],
    source_platform: str = "",
    generated_at: str = "",
    agent: str = "seocho-indexing-agent",
) -> ProvenanceRun:
    """Build a provenance run from an extraction's relationship list
    (``[{source, target, type, ...}]``) — each edge is one fact (subject, predicate,
    object). Facts get content-addressed ids; the run is one PROV Activity."""
    facts: List[Fact] = []
    for rel in relationships or []:
        if not isinstance(rel, dict):
            continue
        s, p, o = rel.get("source"), rel.get("type"), rel.get("target")
        if s and p and o:
            facts.append(Fact(str(s), str(p), str(o),
                              confidence=float(rel.get("confidence", 1.0) or 1.0)))
    return ProvenanceRun(
        run_id=run_id, workspace_id=workspace_id, source_doc=source_doc,
        ontology_version=ontology_version, source_platform=source_platform,
        agent=agent, generated_at=generated_at, facts=facts,
    )
