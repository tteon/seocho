---
title: "Turning a Folder of Filings into a Compliance Graph in Under a Minute"
date: 2026-04-19
authors:
  - seocho
tags:
  - Usecases
  - Ontology
  - Finance
excerpt: A runnable SEOCHO usecase — declare a 6-entity compliance ontology, ingest six mock filings, and ask questions that cross regulator / incident / control / policy boundaries without writing Cypher.
---

Compliance teams spend an enormous amount of time re-reading the same filings.
Which regulation does this inquiry reference? Which control evidence covers this
incident? Which policy version was in effect last quarter? Those answers are
*already in the documents* — they just are not structured yet.

This post walks through a working SEOCHO usecase that shows one path out of
that loop: declare the ontology you already think in (regulation, regulator,
incident, control, policy), ingest a folder of filings, and ask questions that
cross those entity boundaries. The whole example fits in one directory of the
SEOCHO repository and runs on `pip install "seocho[local]"` with no server.

## The thesis in one sentence

> **In regulated finance, the ontology should carry the quality bar, not the model.**

Prompt-driven RAG can get away with vague answers in consumer-facing settings.
In compliance, "approximately correct" is not correct. If you want an answer
you can show to an auditor, the system must be able to point at a typed path
in a graph — not summarize a paragraph.

That is what the `examples/finance-compliance/` usecase demonstrates, using
the smallest ontology that still resembles real work: six entity types, six
relationships.

## The ontology

```text
Company ── SUBJECT_TO ──▶ Regulation ── ENFORCED_BY ──▶ Regulator
   │
   ├── REPORTED ──▶ ComplianceIncident ── RELATES_TO ──▶ Regulation
   │                     │
   │                     └── MITIGATED_BY ──▶ ControlEvidence
   │
   └── GOVERNED_BY ──▶ Policy
```

Every noun on that diagram is a node type; every arrow is a typed
relationship. Declared as code:

```python
from seocho import NodeDef, Ontology, P, RelDef

onto = Ontology(
    name="finance_compliance",
    nodes={
        "Company": NodeDef(properties={"name": P(str, unique=True), "ticker": P(str)}),
        "Regulator": NodeDef(properties={"name": P(str, unique=True)}),
        "Regulation": NodeDef(properties={"name": P(str, unique=True), "code": P(str)}),
        "ComplianceIncident": NodeDef(properties={
            "summary": P(str, unique=True), "date": P(str), "severity": P(str),
        }),
        "ControlEvidence": NodeDef(properties={
            "summary": P(str, unique=True), "date": P(str), "status": P(str),
        }),
        "Policy": NodeDef(properties={"name": P(str, unique=True), "version": P(str)}),
    },
    relationships={
        "SUBJECT_TO": RelDef(source="Company", target="Regulation"),
        "ENFORCED_BY": RelDef(source="Regulation", target="Regulator"),
        "REPORTED": RelDef(source="Company", target="ComplianceIncident"),
        "RELATES_TO": RelDef(source="ComplianceIncident", target="Regulation"),
        "MITIGATED_BY": RelDef(source="ComplianceIncident", target="ControlEvidence"),
        "GOVERNED_BY": RelDef(source="Company", target="Policy"),
    },
)
```

Full version in
[`examples/finance-compliance/ontology.py`](https://github.com/tteon/seocho/blob/main/examples/finance-compliance/ontology.py).

## Ingest six filings

The repo ships six small mock documents under `sample_docs/`:

1. A quarterly filing
2. A regulator inquiry (FMSA asks about MCR-401)
3. An incident report (I-2026-007 — client-data-access misconfiguration)
4. A control attestation (A-2026-014 remediating the incident)
5. Board meeting minutes (discussing the inquiry, approving a new policy)
6. A policy update (Trade Surveillance v2.0)

Each file is plain English, 50 to 80 words — the shape of the ambient text
a compliance team actually receives, not pre-structured JSON.

Running the example:

```bash
pip install "seocho[local]"
export OPENAI_API_KEY=...
python examples/finance-compliance/quickstart.py
```

SEOCHO reads each file, runs an ontology-guided extraction pass, and writes
nodes and typed relationships into an embedded LadybugDB file at
`.seocho/local.lbug`. No separate server. No Docker.

## Questions that cross entity boundaries

Once the graph is populated, the quickstart asks four questions designed to
only have good answers if the typed paths are right:

- *"Which regulations is Acme Financial Services subject to, and who enforces them?"*
  → `Company` → `SUBJECT_TO` → `Regulation` → `ENFORCED_BY` → `Regulator`
- *"What incidents have been reported, and which regulations do they relate to?"*
  → `Company` → `REPORTED` → `ComplianceIncident` → `RELATES_TO` → `Regulation`
- *"Which control evidence mitigates incident I-2026-007?"*
  → `ComplianceIncident` → `MITIGATED_BY` → `ControlEvidence`
- *"Which policies govern trade surveillance at Acme Financial Services?"*
  → `Company` → `GOVERNED_BY` → `Policy`

The answers are not free-text summaries. They are traces through a graph your
ontology constrained. Swap the company, regulator, or regulation name across
all six filings and the answers update consistently — because the *entity
identity* is a graph node, not a string in a paragraph.

## Why this beats vector search for compliance

Vector retrieval can find that documents 2 and 3 both talk about
"MCR-401". It cannot tell you that "the inquiry in document 2 references the
same regulation that document 3's incident relates to." The latter is a
join over typed edges — exactly what a graph with an ontology gives you and
what an embedding score approximates only noisily.

When an auditor asks "show me every incident in scope for MCR-401 and the
controls that mitigate each," a vector-only system can summarize. A
graph-backed system can enumerate. That is the wedge.

## Closed-network friendly on purpose

The same example runs against a local vLLM or any OpenAI-compatible endpoint
— the quickstart accepts `--llm deepseek/deepseek-chat`,
`--llm kimi/kimi-k2.5`, etc. Nothing in the ontology layer requires a hosted
model. For regulated environments where prompts cannot leave the network,
swap the `--llm` flag and keep the rest of the pipeline identical.

Runtime-side, SEOCHO's
[ontology readiness gate](https://github.com/tteon/seocho/blob/main/docs/decisions/ADR-0087-ontology-readiness-gate.md)
can refuse rule-profile promotion when validation reports a blocked verdict —
so the ontology is not just context for prompts, it is a gate on governance
actions. Closed-network deployments lean on that gate to keep model
dependency low.

## Try it

```bash
git clone https://github.com/tteon/seocho && cd seocho
pip install "seocho[local]"
export OPENAI_API_KEY=...
python examples/finance-compliance/quickstart.py
```

The full walkthrough, including the mock filings, lives at
[`examples/finance-compliance/`](https://github.com/tteon/seocho/tree/main/examples/finance-compliance).

## Next usecases — contribute one

We deliberately keep [`docs/USECASES.md`](https://github.com/tteon/seocho/blob/main/docs/USECASES.md)
short. A usecase only appears there when there is a runnable example
behind it. Finance compliance is the first. We would welcome
contributions for:

- **Healthcare consent tracking** — patients, consents, studies, revocations
- **Supplier risk** — suppliers, contracts, incidents, alternates
- **Engineering team memory** — services, incidents, runbooks, owners
- **Legal contract obligations** — parties, obligations, deadlines, breaches

The pattern to copy is the finance example. Pick a domain you know, write a
six-entity starter ontology, drop five to ten mock docs next to it, and open
a pull request — the ontology shape is the interesting part.

## What this is not

This example is not a production compliance system. The mock filings are
intentionally short and synthetic. Real compliance work adds authentication,
auditable evidence storage, retention policies, change-tracking, and
regulator-specific formats that a six-file toy cannot cover.

What it is: a concrete, runnable shape. If "ontology-governed" has felt
abstract in previous SEOCHO posts, this is the shortest working demo of the
idea. Fork it, rename the entities, point it at your docs.
