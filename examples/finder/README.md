# FinDER tutorial bundle

Four runnable Jupyter notebooks teaching how SEOCHO turns ontology-driven prompts into a knowledge graph, and how to analyze that graph.

| # | Notebook | What you'll learn |
|---|---|---|
| 1 | [`01_vector_vs_graph_rag.ipynb`](01_vector_vs_graph_rag.ipynb) | Vector RAG (LanceDB) vs Graph RAG (Neo4j), plus a text2Cypher variant where the LLM writes the Cypher itself |
| 2 | [`02_fibo_module_impact.ipynb`](02_fibo_module_impact.ipynb) | The ontology *is* the prompt — see seocho's live extraction prompt, compare a generic baseline vs FIBO on the same doc, then add your own class and watch the new label appear |
| 3 | [`03_network_analytics.ipynb`](03_network_analytics.ipynb) | Pull T1's graph into NetworkX and find impactful entities/relationships via degree, PageRank, betweenness, and community detection |
| 4 | [`04_private_opik.ipynb`](04_private_opik.ipynb) | Personal template — your USER_ID + metadata threaded through ontology design (TTL +/-), LLM backend, agent tool_use, and pattern design; every span tagged for Opik |

The notebooks build on each other: T1 populates a Neo4j workspace that T3 reads. T2 and T4 are self-contained.

## Layout

```
finder/
├── 01_vector_vs_graph_rag.ipynb
├── 02_fibo_module_impact.ipynb
├── 03_network_analytics.ipynb
├── 04_private_opik.ipynb
├── Dockerfile               ← seocho[ci,local] + tutorial deps + JupyterLab
├── requirements.txt         ← networkx, matplotlib, rdflib, …
├── datasets/
│   ├── finder_tutorial_subset.json   ← synthetic 10-K excerpt (offline-safe)
│   ├── ttl/                          ← OWL/Turtle samples for Tutorial 4
│   └── fibo_modules/{be,fbc,sec,fnd,ind}.yaml + compose.py
└── lib/
    ├── lance_graph_store.py          ← forward-compat reference for upstream lance-graph#91
    ├── graph_viz.py                  ← NetworkX visualization helpers
    └── ontology_io.py                ← thin compat shim re-exporting from seocho core
```

## Running

```bash
# From the repo root
echo 'OPENAI_API_KEY=sk-...' >> .env

make tutorials-up         # JupyterLab + Neo4j (DozerDB + apoc + n10s)
open http://localhost:8888/lab/tree/examples/finder    # notebooks
open http://localhost:7474                              # Neo4j Browser
```

Tutorials read `SEOCHO_LLM=provider/model` from `.env`. Default `openai/gpt-4o-mini`; supports `deepseek/...`, `kimi/...`, `grok/...`, `qwen/...`. Tutorial 1 still requires `OPENAI_API_KEY` for embeddings (only OpenAI supports embeddings in seocho today).

To run all notebooks headlessly (sanity-check the bundle):
```bash
make tutorials-test
```

## Running order

For a clean pass:

1. T1 first — it populates the `finder_tutorial` workspace in Neo4j that T3 then reads
2. T2 — self-contained; uses `ontology_demo_*` workspaces
3. T3 — reads `finder_tutorial` (set `SEOCHO_NETWORK_WORKSPACE` in env to point elsewhere)
4. T4 — self-contained; uses `private-<USER_ID>` workspace

## Notes

- **lance-graph upstream**: `lib/lance_graph_store.py` is a tutorial-only property-graph adapter on two LanceDB tables. It's kept as a forward-compatible reference for [lance-graph#91](https://github.com/lance-format/lance-graph/issues/91); current tutorials use Neo4j because lance-graph isn't usable yet.
- **Per-ontology Neo4j databases**: Seocho derives a database name from `ontology.name + graph_model` by default. Since the bundled DozerDB only has `neo4j`, the notebooks pin `client.default_database = "neo4j"` after construction and rely on `workspace_id` for separation.
