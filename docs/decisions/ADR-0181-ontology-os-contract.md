# ADR-0181: The OS contract — what an ontology file cannot carry, and where it goes

- Status: Proposed
- Date: 2026-08-16
- Related: `ADR-0114` (scorecard), `ADR-0115` (OntoClean critic), `ADR-0116`
  (corpus-aware tier), `ADR-0117` (snapshot store), `seocho-8v5` (`P` has no
  enum), `seocho-di8` (module naming)

## Context

A user arrives on one of two paths: they have an ontology (`.ttl`, or one of the
six formats `ontology_import` accepts — arrows.app, Cypher DDL, native, GraphQL,
LinkML, data-importer), or they have nothing and we cold-start from their corpus.

The assumption worth writing down and then correcting is that the first path is
finished once the file is loaded. It is not. Indexing 322 EnterpriseRAG-Bench
documents with a hand-authored ontology produced three failures, and **none of
them is expressible in the ontology file**:

**Vocabulary drift.** `Decision.status`, declared `P(str)`, came back as eight
values across 51 documents — `CURRENT` 88, `proposed` 21, `current` 8,
`applied` 8, `pending` 3, `mitigation` 3, `SUPERSEDED` 2, `superseded` 2. Two
faults at once: an invented vocabulary and a case split. A query filtering
`status = 'superseded'` misses half of them; one filtering `'current'` misses 88
of 96.

The clean contrast is in the same run. `Step.position`, declared `P(int)`,
renders into the prompt as `Step.position: datatype=INTEGER` and was set on
**175 of 175** Step nodes. *What could be declared was honoured; what had no
declarative home was not.* `P(type, unique, index, required, description,
aliases)` has no way to express an allowed set (`seocho-8v5`).

**No canonical addresses.** Extracted node ids are document-local (`org_1`,
`decision_1`), so the same person in two documents is two nodes. `identity_keys`
exists on `NodeDef` and is the right mechanism, but it is a SEOCHO concept that
no `.ttl` carries.

**Requirements absent from the prompt.** `Ontology(description=...)` was
accepted, stored, and never rendered — verified. So the extractor received the
formalised artefact with none of the requirements analysis that produced it.
Keet's micro-level methodologies (OntoSpec, OD101, DiDOn) treat purpose,
competency questions and modelling decisions as *determining* the axioms rather
than preceding them. This package already scores coverage against competency
questions (`competency_question_report`) and never told the extractor what they
were: CQs graded the ontology, they never built it.

### What `from_ttl` actually reads

Measured, not assumed. Across the whole function the only predicates consulted
are:

    OWL.Class  OWL.Ontology  OWL.versionIRI  OWL.versionInfo
    RDFS.Class  RDFS.subClassOf  RDFS.label  RDFS.comment
    SKOS.altLabel  SKOS.definition

**`owl:hasKey` and `owl:oneOf` are not read** — the strings do not appear
anywhere in `ontology.py` or `ontology_serialization.py`. So even the two OS
needs that OWL *could* express are not reachable today. Teaching `from_ttl` to
read them is worth doing and does not remove the need for a sidecar, because the
other three needs have no OWL equivalent at all.

## Decision

Three layers. The ontology file is wrapped, never replaced.

**Layer 1 — formalisation.** The user's `.ttl` or imported document, unchanged.
Classes, taxonomy, relations, axioms. SEOCHO reads it and does not author it.

**Layer 2 — the OS contract**, a sidecar (`<name>.seocho.yaml`) carrying what
Layer 1 cannot. This is what `Ontology.annotations` now holds:

```yaml
seocho_contract: 1
ontology: enterprise_decisions.ttl

purpose: >
  Recover who decided what and which decision is currently in force.

competency_questions:
  - id: cq1
    ask: For a given system, which value is CURRENTLY applied, and which did it replace?
    requires: [Decision.status, SUPERSEDES]
  - id: cq2
    ask: What caused an incident and what mitigated it?
    requires: [CAUSED_BY, MITIGATED_BY]

modelling_decisions:
  - SUPERSEDES runs newer -> older.
  - status is an attribute of Decision, not a class.

identity:                       # -> NodeDef.identity_keys
  Person: [name]
  Step:   [name, procedure]

vocabularies:                   # -> the enum P cannot declare
  Decision.status: [proposed, applied, superseded, reverted]
```

The `requires` key is not invented here: it is the shape
`competency_question_report` already consumes. The change is using it to build
rather than only to grade.

**Layer 3 — measured evidence.** `OntologySnapshotStore` already does this and
does it well: filesystem JSON at `<root>/<package_id>/<version>__<fp8>.json`,
immutable and content-addressed so re-saving a version under a different
fingerprint raises rather than silently mutating a published one, and carrying
the scorecard, OntoClean tags and corpus profile *with* the ontology so
`compare` reports a measured guardrail-value delta rather than a schema diff.

## The gap is wiring, not capability

`score_ontology` already names what is missing. Run against the hand-authored
enterprise ontology it reports grade B and:

    [major] 6 classes but no 'broader' hierarchy — consider an is-a taxonomy
    [minor] No corpus profile supplied — corpus-coverage was skipped.
            This is the signal that predicts guardrail value.
    [minor] No competency questions supplied

That is exactly the "tell the user what is missing" behaviour we want, and
**nothing forces anyone to look at it**. `OntologySnapshotStore` is referenced
from production code in precisely one place — a docstring in `fibo_catalog.py`.
The only code that calls `store.save(..., scorecard=...)` is
`scripts/benchmarks/ontology_versioning_demo.py`, a demo. The 322-document run
described above was indexed without ever scoring the ontology, and has no record
of which ontology version produced it.

So the decision is a gate:

    seocho ontology check <file> --corpus ./docs
      -> score_ontology(Layer 1 + Layer 2, corpus_profile)
      -> name what is missing, by element
      -> on pass, snapshot; indexing then references the snapshot id

Cold start becomes a special case rather than a separate product: Layer 1 is
empty, we propose Layer 2 from the corpus, and the user's acceptance creates
Layer 1. `OntologyImportResult`'s existing rule — *"a non-persisted draft:
nothing is saved until the user acts on it"* — carries over unchanged.

## Consequences

- An indexing run records the ontology version and fingerprint it used. Today it
  records nothing, and recovering that means reading the code that ran.
- `identity` and `vocabularies` become declarative, so the two failures above
  are prevented at author time rather than discovered in extracted data.
- Layer 2 is optional. An ontology without it renders and indexes exactly as
  before; the scorecard reports the absence rather than the pipeline refusing.

## What is not yet established

Whether Layer 2 changes *extraction* is being measured, not assumed. The
requirements channels reach the prompt now, and a 2x2 A/B (requirements on/off
crossed with the removed FinDER literals) is running against the 322-document
baseline. If it comes back null, the case for putting competency questions and
modelling decisions in the prompt is gone and Layer 2 narrows to validation
metadata — still needed for the gate and the scorecard, but not for extraction.
`identity` and `vocabularies` are unaffected by that result: both failures are
already demonstrated in extracted data.
