# SEOCHO Tutorials

Curated, guided notebooks that teach one SEOCHO capability end to end. Each is
self-contained, runnable with **no infrastructure** (no graph database required),
and ships cached artifacts so it runs as-is — set an API key only to re-run the
LLM steps live. For the broader grab-bag of examples, datasets, and demos, see
[`../examples/`](../examples/).

| Notebook | What you'll learn | Needs a key? |
|----------|-------------------|--------------|
| [`ontology_guardrail_before_after.ipynb`](ontology_guardrail_before_after.ipynb) | Profile a corpus → score candidate ontologies with the corpus-aware scorecard → let `select_guardrail` pick the best guardrail → **measure LLM answer quality BEFORE vs AFTER** applying it, and read the result honestly. | Optional (ships cached results; MARA to re-run live) |

## Running

```bash
pip install seocho            # or work from a repo checkout (the notebook prefers in-repo src/)
jupyter lab tutorials/        # then open the notebook
```

Or click the **Open in Colab** badge at the top of a notebook.

### Conventions these notebooks follow

- **Colab badge** in the first cell; **self-installing** setup cell.
- **No hardcoded secrets** — keys are read from the environment via an
  optional `getpass` prompt (SEOCHO is MARA-first; the offline core needs no key).
- **Cached artifacts** under [`data/`](data/) make the whole notebook reproducible
  without external infrastructure (the LLM steps fall back to a recorded run).
- **Honest outputs** — committed cell outputs are from a real run, including
  cases where a feature did *not* help and why.

## Data

[`data/`](data/) holds small, tracked artifacts the tutorials read:

- `finder_tutorial_open_graphs.json` — open (schema-free) extraction of the 10-case
  FinDER tutorial subset, used to build the corpus profile offline.
- `finder_tutorial_eval_cached.json` — a recorded BEFORE/AFTER answer-quality run
  (MARA DeepSeek-V3.1) so the measurement reproduces without a key.
