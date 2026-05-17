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

**Blog walk-through**: *(coming soon — site narrative draft pending)*

## 2. FinDER tutorial set — Vector + Graph RAG, FIBO impact, RDF vs LPG

**What**: Three runnable notebooks built around the **FinDER** SEC 10-K Q&A
benchmark. They show three orthogonal questions a graph-RAG team has to
answer when adopting SEOCHO on a finance corpus.

**Who benefits**: Teams evaluating SEOCHO on financial filings; researchers
benchmarking RAG architectures; anyone deciding how much of FIBO they
actually need.

**Try them**: the four FinDER notebooks live under
[`examples/finder/`](../examples/finder/) with their own
[README](../examples/finder/README.md), helper modules, datasets, and
Docker env. Bring up the bundle with `make tutorials-up`.

- `01_vector_vs_graph_rag.ipynb` — side-by-side Vector RAG (LanceDB) and
  Graph RAG (Neo4j) over FinDER.
- `02_fibo_module_impact.ipynb` — sweep five FIBO module compositions
  (none / BE / BE+FBC / BE+FBC+SEC / full) and measure how each changes
  KG volume, coverage, SHACL-style violations, and FinDER QA score.
- `03_rdf_vs_lpg.ipynb` — index the same corpus as LPG (Neo4j) and
  RDF/OWL (owlready2); score both on Golden Standard, Data-Driven,
  Application/Task, User-based, and Structure-based tracks.
- `04_private_opik.ipynb` — personal template threading USER_ID /
  metadata through ontology design (TTL +/-), LLM backend, agent
  tool_use, and pattern design; every span tagged for Opik.

All four default to a synthetic 10-K subset that ships with the bundle
so they run end-to-end without external data; set `FINDER_PATH` to your
real FinDER JSON when ready.

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
