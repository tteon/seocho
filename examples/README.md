# Examples

Jupyter notebooks for learning SEOCHO by doing.

## Notebooks

| Notebook | What you'll learn |
|----------|------------------|
| [quickstart.ipynb](quickstart.ipynb) | Recommended first entry route: ontology, indexing design YAML, agent design YAML, local indexing/query, observability, and four-provider comparison |
| [bring_your_data.ipynb](bring_your_data.ipynb) | Load your actual data: text files, CSV, JSON, and query it |
| [finder/](finder/) | FinDER tutorial bundle — four notebooks (Vector vs Graph RAG, FIBO module impact, RDF vs LPG, private Opik workflow) plus their helper modules and Docker env. See [finder/README.md](finder/README.md) for the bundle's index. |

## Prerequisites

```bash
uv pip install "seocho[local]" python-dotenv jupyter
cp ../.env.example ../.env
```

`quickstart.ipynb` loads provider credentials from `../.env`.

- default first run: embedded LadybugDB
- optional production-like path: set both `NEO4J_URI` and
  `NEO4J_PASSWORD` in `../.env` and the notebook switches to Bolt-backed
  Neo4j/DozerDB automatically
- if `NEO4J_URI` is present but `NEO4J_PASSWORD` is empty, the notebook keeps
  using LadybugDB and prints the fallback reason

## Running

```bash
cd examples
jupyter notebook
```

Then open the notebook and run cells top to bottom.

## Running the FinDER tutorials in Docker (recommended)

The four FinDER notebooks have a packaged Docker environment with JupyterLab
plus a bundled **Neo4j (DozerDB + apoc + n10s)** for the LPG graph backend.
T1, T3, and T4 talk to Neo4j via Bolt; T2 stays on the embedded LadybugDB; T3's
RDF side uses embedded **owlready2**. Vector search is **LanceDB**.

```bash
# 1. Once: put your OpenAI key in the repo .env
echo 'OPENAI_API_KEY=sk-...' >> ../.env

# 2. Bring up JupyterLab
make tutorials-up
# or: docker compose -f docker-compose.tutorials.yml up -d --build

# 3. Open JupyterLab (token disabled in this dev image)
open http://localhost:28888/lab/tree/examples
```

What ships:

- `tutorials-jupyter` — JupyterLab on `localhost:28888` (chosen to dodge the 8888-range that local IDEs and notebook servers commonly grab). Bind-mounts `examples/` and `seocho/` so edits on the host show up live in the container.
- `tutorials-neo4j` — DozerDB 5.26 with `apoc` + `n10s` plugins.
  - **Neo4j Browser:** http://localhost:7474  (login `neo4j` / `tutorialspw`)
  - **Bolt URI:** `bolt://tutorials-neo4j:7687` (container-internal — notebooks read it from `NEO4J_URI`)
  - If the main `make up` stack is also running it'll claim 7474/7687 first;
    set `TUTORIALS_NEO4J_HTTP_PORT` / `TUTORIALS_NEO4J_BOLT_PORT` in `.env`
    to move the tutorial stack out of the way.
- LanceDB tables, owlready2 SQLite, JSONL traces all live under `./.seocho/`.

Customize via `.env`:

```bash
TUTORIALS_JUPYTER_PORT=28888
TUTORIALS_NEO4J_HTTP_PORT=7474
TUTORIALS_NEO4J_BOLT_PORT=7687
TUTORIALS_NEO4J_PASSWORD=tutorialspw
FINDER_PATH=/workspace/examples/datasets/finder_tutorial_subset.json
```

Useful commands:

```bash
make tutorials-logs     # tail container logs
make tutorials-shell    # bash inside the Jupyter container
make tutorials-down     # stop everything (data persists in ./.seocho)

make tutorials-build    # rebuild the image (no container start)
make tutorials-smoke    # fast import-check for all four notebooks (~10s, no API calls)
make tutorials-pytest   # run the seocho/tests/test_ontology_ttl.py suite in the container
make tutorials-test     # headless nbconvert run of every notebook (needs OPENAI_API_KEY)
```

`tutorials-test` skips `finder_rdf_vs_lpg_evaluation.ipynb` because the OWL
reasoner cell needs a JVM (HermiT) which the slim image doesn't ship; open
that notebook in JupyterLab to run it interactively, or install `default-jre-headless`
in the container first (`make tutorials-shell` then
`apt-get update && apt-get install -y default-jre-headless`).

The bonus *OWL reasoning* cell in Tutorial 3 invokes HermiT (Java). The cell
reports gracefully if no JVM is present in the image; install one with
`apt-get install -y default-jre-headless` inside the container if you want
to run that step.

## What each notebook covers

### quickstart.ipynb
1. Load and inspect one ontology-first domain contract
2. Inspect indexing design specs and choose an LPG-first local path
3. Inspect agent design specs and choose a reflection-chain pattern
4. Index finance-compliance sample docs into embedded LadybugDB or optional
   Neo4j/DozerDB from `.env`
5. Query with natural language and inspect observability metadata
6. Compare the same workflow across OpenAI, DeepSeek, Kimi, and Grok
7. Use the notebook output as the basis for further tuning

### bring_your_data.ipynb
1. Define your own ontology (editable template)
2. Save as JSON-LD for version control
3. Path A: Index .txt/.md files from a directory
4. Path B: Index CSV (auto-detects content column)
5. Path C: Index JSON / API responses
6. Query your data
7. Check graph status
