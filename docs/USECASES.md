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

**Try them**:

- [`examples/finder_lance_vector_vs_graph_rag.ipynb`](../examples/finder_lance_vector_vs_graph_rag.ipynb)
  — side-by-side Vector RAG (vanilla `LanceDBVectorStore`) and Graph RAG
  (a tutorial-only `LanceGraphStore` adapter, forward-compatible with
  upstream [lance-graph#91](https://github.com/lance-format/lance-graph/issues/91)).
- [`examples/finder_fibo_module_impact.ipynb`](../examples/finder_fibo_module_impact.ipynb)
  — sweep five FIBO module configurations (none / BE / BE+FBC / BE+FBC+SEC /
  full) over the same FinDER corpus and measure how each module changes
  KG volume, coverage, SHACL-style violations, and FinDER QA score.
- [`examples/finder_rdf_vs_lpg_evaluation.ipynb`](../examples/finder_rdf_vs_lpg_evaluation.ipynb)
  — index the same corpus twice (LPG and RDF via n10s) and score both
  on Golden Standard, Data-Driven, Application/Task, User-based, and
  Structure-based evaluation tracks.

All three default to `examples/datasets/finder_tutorial_subset.json` so
they run end-to-end without external data; set `FINDER_PATH` to your
real FinDER JSON when you're ready. Notebook 3 additionally requires
Neo4j+neosemantics (RDF path).

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
