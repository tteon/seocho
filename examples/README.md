# Examples

Jupyter notebooks for learning SEOCHO by doing.

## Notebooks

| Notebook | What you'll learn |
|----------|------------------|
| [quickstart.ipynb](quickstart.ipynb) | Recommended first entry route: ontology, indexing design YAML, agent design YAML, local indexing/query, observability, and four-provider comparison |
| [bring_your_data.ipynb](bring_your_data.ipynb) | Load your actual data: text files, CSV, JSON, and query it |

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
