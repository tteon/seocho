# SEOCHO Usecases

SEOCHO is middleware for teams that want ontology-governed knowledge graphs
built from their own documents. The usecases below are the ones we maintain
working end-to-end demos for. Each links to a runnable example in the
repository plus a longer narrative walk-through (the blog post).

We deliberately keep this list short. A usecase only appears here when there
is actual code behind it that a reader can run in under five minutes.

## 1. Finance Compliance

**What**: Turn a folder of filings, inquiries, incidents, and policy docs
into an auditable compliance knowledge graph. Ask questions that cross
regulator / regulation / incident / control / policy boundaries.

**Who benefits**: Compliance teams, internal audit, second-line risk
functions — anyone who re-reads the same filings to reconcile "which rule,
which incident, which control."

**Try it**: [`examples/finance-compliance/`](../examples/finance-compliance/)
— 6 mock documents, 6 entity types, runs in under a minute on
`pip install "seocho[local]"`.

**What you will see**: The ontology declares the entities and relationships
you already think in (`Company → SUBJECT_TO → Regulation → ENFORCED_BY →
Regulator`, etc.). Ingestion populates the graph from plain text. Questions
like "which control evidence mitigates incident I-2026-007" return an
answer grounded in typed paths, not a free-text paragraph.

**Blog walk-through**: *(coming soon — drafted in `tteon.github.io`)*

## Contributing a new usecase

A new usecase entry lands here only when the accompanying repo example
actually runs. The order of work is:

1. Open a draft PR that adds `examples/<your-usecase>/` with at minimum a
   starter ontology, 5–10 sample docs, and a `quickstart.py` that runs
   end-to-end on `pip install "seocho[local]"`.
2. Have one other contributor run the quickstart against a fresh clone.
3. Add the usecase section to this file.
4. (Optional but encouraged) Drop a narrative walk-through into the blog.

Usecases we would welcome contributions for:

- **Healthcare consent tracking** — patients, consents, studies, revocations.
- **Supplier risk** — suppliers, contracts, incidents, alternates.
- **Engineering team memory** — services, incidents, runbooks, owners.
- **Legal contract obligations** — parties, obligations, deadlines, breaches.

None of these exist as working demos yet. The finance compliance example is
the pattern to copy.
