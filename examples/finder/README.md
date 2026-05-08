# FinDER tutorial bundle

Four runnable Jupyter notebooks that drill into the orthogonal questions
a graph-RAG team has to answer when adopting SEOCHO on a financial
corpus, plus the supporting code, datasets, and Docker env.

| # | Notebook | Question it answers |
|---|---|---|
| 1 | [`01_vector_vs_graph_rag.ipynb`](01_vector_vs_graph_rag.ipynb) | Vector RAG (LanceDB) vs Graph RAG (Neo4j) on the same corpus |
| 2 | [`02_fibo_module_impact.ipynb`](02_fibo_module_impact.ipynb) | How does each FIBO module (BE / FBC / SEC / FND / IND) change KG quality? |
| 3 | [`03_rdf_vs_lpg.ipynb`](03_rdf_vs_lpg.ipynb) | RDF (owlready2) vs LPG (Neo4j) across five evaluation tracks |
| 4 | [`04_private_opik.ipynb`](04_private_opik.ipynb) | Personal template — your USER_ID + metadata threaded through ontology design (TTL +/-), LLM backend, agent tool_use, and pattern design; every span tagged for Opik |

## Layout

```
finder/
├── 01_vector_vs_graph_rag.ipynb
├── 02_fibo_module_impact.ipynb
├── 03_rdf_vs_lpg.ipynb
├── 04_private_opik.ipynb
├── Dockerfile               ← seocho[ci,local] + tutorial deps + JupyterLab
├── requirements.txt         ← networkx, matplotlib, rdflib, owlready2, …
├── datasets/
│   ├── finder_tutorial_subset.json   ← synthetic 10-K excerpt (offline-safe)
│   ├── ttl/                          ← OWL/Turtle samples for Tutorial 4
│   └── fibo_modules/{be,fbc,sec,fnd,ind}.yaml + compose.py
└── lib/
    ├── lance_graph_store.py          ← forward-compat reference for upstream lance-graph#91
    ├── owlready_graph_store.py       ← OWL/RDF GraphStore over owlready2
    ├── graph_viz.py                  ← NetworkX visualization helpers
    ├── lpg_metrics.py                ← Network metrics (Tutorial 3)
    ├── rdf_lpg_comparison.py         ← Five-track scoring helpers
    ├── fibo_module_metrics.py        ← Coverage / SHACL / QA scoring (Tutorial 2)
    └── ontology_io.py                ← thin compat shim re-exporting from seocho core
```

## Running

```bash
# From the repo root
echo 'OPENAI_API_KEY=sk-...' >> .env

make tutorials-up        # JupyterLab + Neo4j (DozerDB + apoc + n10s)
open http://localhost:28888/lab/tree/examples/finder    # notebooks
open http://localhost:7474                              # Neo4j Browser
```

Tutorials read `SEOCHO_LLM=provider/model` from `.env`. Default
`openai/gpt-4o-mini`; supports `deepseek/...`, `kimi/...`, `grok/...`,
`qwen/...`. Tutorial 1 still requires `OPENAI_API_KEY` for embeddings
(only OpenAI supports embeddings in seocho today).

To run all notebooks headlessly (sanity-check the bundle):
```bash
make tutorials-test
```

## Out-of-scope notes

- **lance-graph upstream**: `lib/lance_graph_store.py` is a tutorial-only
  property-graph adapter on two LanceDB tables. It's kept as a forward-
  compatible reference for [lance-graph#91](https://github.com/lance-format/lance-graph/issues/91);
  current tutorials use Neo4j because lance-graph isn't usable yet.
- **Tutorial 3 OWL reasoner**: the bonus reasoning cell needs a JVM (HermiT).
  The slim image doesn't ship one — install via
  `apt-get install -y default-jre-headless` inside the container if you
  want that cell to populate inferred triples.
