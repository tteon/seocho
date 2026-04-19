# Finance Compliance Usecase

A complete, runnable example showing how a regulated finance team can turn a
folder of filings, inquiries, incidents, and policy documents into an
ontology-governed knowledge graph — and ask questions that cross entity
boundaries.

This is intentionally small (6 mock documents, 6 entity types) so you can read
it end-to-end in a few minutes, then swap in your own ontology and docs.

## What you get

- `ontology.py` — the starter finance-compliance ontology
  (`Company`, `Regulator`, `Regulation`, `ComplianceIncident`,
  `ControlEvidence`, `Policy`) with six relationships.
- `sample_docs/` — six short mock filings covering a quarterly disclosure,
  a regulator inquiry, an incident, control attestation, board minutes,
  and a policy update.
- `quickstart.py` — ingests the docs into an embedded local graph and asks
  four cross-entity questions.

## Run it

```bash
pip install "seocho[local]"
export OPENAI_API_KEY=...
python examples/finance-compliance/quickstart.py
```

Swap to another provider:

```bash
python examples/finance-compliance/quickstart.py --llm deepseek/deepseek-chat
```

Only ingest (skip Q&A, useful for inspecting the resulting graph):

```bash
python examples/finance-compliance/quickstart.py --skip-query
```

The graph is written to `.seocho/local.lbug` (embedded LadybugDB) by default.

## Example questions the graph answers

- Which regulations is Acme Financial Services subject to, and who enforces
  them?
- What incidents have been reported, and which regulations do they relate to?
- Which control evidence mitigates incident I-2026-007?
- Which policies govern trade surveillance at Acme Financial Services?

The ontology forces the answer into a shape you can audit — each answer
traces back to a typed path in the graph, not a free-text summary.

## Why finance compliance

Compliance teams already think in terms of "which regulation, which control,
which incident, which policy." Those nouns are the ontology. Once they're
declared, every new filing either fits the schema (and enriches the graph)
or fails loudly enough to be fixed. That is the self-benefit: analysts stop
re-reading the same filings and instead query a structured history.

## Contributing

Open a PR that adds:

- A starter ontology for a new domain (e.g. `healthcare-consent`,
  `logistics-supplier-risk`) following the same pattern.
- Additional mock docs for this usecase — we specifically want adversarial
  examples (conflicting incident severities, regulator name aliases,
  overlapping policy versions).
- Real Q&A evaluations against a labeled answer set.

See `docs/USECASES.md` for the broader usecase plan.
