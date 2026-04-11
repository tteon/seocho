# Examples

Jupyter notebooks for learning SEOCHO by doing.

## Notebooks

| Notebook | What you'll learn |
|----------|------------------|
| [quickstart.ipynb](quickstart.ipynb) | Full walkthrough: ontology → inspect → index → extract → validate → query → denormalize |
| [bring_your_data.ipynb](bring_your_data.ipynb) | Load your actual data: text files, CSV, JSON, and query it |

## Prerequisites

```bash
pip install seocho neo4j openai jupyter
```

A running Neo4j or DozerDB instance (default: `bolt://localhost:7687`).

## Running

```bash
cd examples
jupyter notebook
```

Then open the notebook and run cells top to bottom.

## What each notebook covers

### quickstart.ipynb
1. Define an ontology (NodeDef, RelDef, P)
2. Inspect derived prompts, SHACL shapes, Cypher constraints
3. Save as JSON-LD
4. Connect to Neo4j and index articles
5. Extract and score quality
6. Query with natural language
7. Use reasoning mode (auto-retry)
8. Run raw Cypher
9. Index from files
10. Denormalize for export

### bring_your_data.ipynb
1. Define your own ontology (editable template)
2. Save as JSON-LD for version control
3. Path A: Index .txt/.md files from a directory
4. Path B: Index CSV (auto-detects content column)
5. Path C: Index JSON / API responses
6. Query your data
7. Check graph status
