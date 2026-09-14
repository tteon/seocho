# Build your own seocho project

A short guide for users who want to take what they learned from the FinDER tutorial bundle and turn it into their own project — with a consistent name, traceable authorship, and metadata that flows into the graph and the traces automatically.

## 1. Naming convention

Name your project `seocho-{{model_provider}}` where `{{model_provider}}` is the provider whose LLM you used. Concrete examples:

| Project name | LLM you used |
|---|---|
| `seocho-openai` | `openai/gpt-4o-mini` |
| `seocho-grok` | `grok/grok-4.20-reasoning` |
| `seocho-kimi` | `kimi/kimi-k2.5` |
| `seocho-deepseek` | `deepseek/deepseek-chat` |
| `seocho-qwen` | `qwen/qwen-plus` |

Why this scheme: it pairs *what you built* with *the model that made it*. When somebody finds your project on GitHub or in a paper, they immediately know which model generated the extractions — important for reproducibility and for honest comparison against another project that used a different model.

If you ran a head-to-head between two providers, name your project `seocho-{{primary}}-vs-{{other}}` (e.g., `seocho-grok-vs-openai`).

## 2. Required `.env` fields

Copy `.env.project.example` to `.env` in your project root and fill it in:

```bash
# === Identity ===
SEOCHO_PROJECT_NAME=seocho-openai          # the seocho-{{model_provider}} name
SEOCHO_LLM=openai/gpt-4o-mini              # provider/model — flows everywhere

# === LLM provider key (the matching one — only one is required) ===
OPENAI_API_KEY=sk-...
# DEEPSEEK_API_KEY=sk-...
# MOONSHOT_API_KEY=sk-...                  # for kimi
# XAI_API_KEY=xai-...                      # for grok
# DASHSCOPE_API_KEY=sk-...                 # for qwen

# === Author metadata ===
SEOCHO_AUTHOR_NAME=Hardy Jeong
SEOCHO_AUTHOR_EMAIL=hardy.jeong@example.com
SEOCHO_AUTHOR_GITHUB=tteon                 # GitHub username (no @, no URL)
SEOCHO_AUTHOR_AFFILIATION=Independent      # company / lab / personal

# === Optional run metadata ===
SEOCHO_RUN_NOTES=baseline run on FinDER subset, no prompt tweaks
```

The `SEOCHO_AUTHOR_*` values describe your project provenance. Include the
fields you need explicitly in saved results or spans; setting environment
variables alone does not stamp every entity or trace.

## 3. How to attach metadata

**Workspace separation** — pass the chosen project workspace explicitly to your
`Seocho` client. Preserve `workspace_id` when invoking runtime APIs.

**Trace identity** — create a span around the work you want to observe:

```python
import os
from seocho.tracing import enable_tracing, start_span, disable_tracing

workspace_id = os.environ.get("SEOCHO_PROJECT_NAME", "seocho-project")
enable_tracing(backend="jsonl", output="./traces/project.jsonl")
try:
    with start_span("project.run", metadata={
        "workspace_id": workspace_id,
        "user_id": os.environ.get("SEOCHO_USER_ID", "anonymous"),
    }):
        pass  # Replace with the selected experiment.
finally:
    disable_tracing()
```

Only instrumented work produces spans. Provider costs, reasoning events and
quality scores require their own instrumentation or evaluation.

**Entity provenance** — Tutorial 2's advanced section demonstrates the `on_before_write` callback that stamps `extracted_at` and `extracted_by` on every entity. Extend it to also record the project name:

```python
def stamp_runtime_metadata(nodes, rels):
    for n in nodes:
        props = n.setdefault("properties", {})
        props.setdefault("project", os.environ["SEOCHO_PROJECT_NAME"])
        props.setdefault("extracted_at", RUN_AT)
        props.setdefault("extracted_by", f"{LLM_PROVIDER}/{LLM_MODEL}")
        props.setdefault("author", os.environ.get("SEOCHO_AUTHOR_GITHUB"))
    return nodes, rels
```

When this callback is attached to indexing, the added properties identify:
- which project it came from
- which model produced it
- when it was produced
- who ran the project

These properties aid attribution; keep input snapshots and run receipts for reproducibility.

## 4. Recommended project layout

```
seocho-openai/                          ← your project root
├── README.md                           ← describe what you built + what FinDER subset
├── .env                                ← (gitignored) your filled-in secrets + identity
├── .env.example                        ← copy of this guide's template, no secrets
├── notebooks/
│   ├── 01_my_extraction.ipynb          ← your customized version of T2
│   ├── 02_my_analytics.ipynb           ← your customized version of T3
│   └── ...
├── ontology/
│   ├── base.jsonld                     ← your ontology in JSON-LD
│   └── overlays/                       ← TTL overlays you compose in (`+` / `-`)
├── results/
│   ├── extraction_metrics.json         ← entity counts, confidence histograms
│   ├── network_metrics.json            ← PageRank top-N, communities, etc.
│   ├── traces/                         ← JSONL traces or OTLP export
│   └── viz/                            ← saved matplotlib figures
└── seocho_pinned_version.txt           ← `pip freeze | grep seocho` snapshot
```

The `seocho_pinned_version.txt` matters: seocho's API stabilizes over time but extraction prompts and default behaviors evolve. A reader six months later wants to know exactly which seocho they need to reproduce your numbers.

## 5. When you publish

Three things to include in your project README so others can build on your work:

1. **Provider + model** — already in `SEOCHO_PROJECT_NAME` and `SEOCHO_LLM`, but say it again in plain English at the top of the README.
2. **Ontology** — link to the file in your repo, *and* mention if you composed it from FIBO modules or TTL overlays. Other readers want to fork the ontology, not just the notebook.
3. **Cost / token / latency budget** — the metrics from your `results/` directory. Your replication baseline.

Optional but generous: link back to the upstream `tteon/seocho` repo and the FinDER tutorial bundle so readers know the origin of the patterns.

## 6. Sharing back

If you find a pattern that should be in the upstream tutorial — a new ontology module, a useful helper for `examples/finder/lib/`, a fix to one of the three notebooks — open a PR against `tteon/seocho`. Mark your project name and provider in the PR description so reviewers can see what context the change came from.

## 7. Local trace evidence

Use the supported `none|console|jsonl|otlp` backends. JSONL requires no account:

```python
from seocho.tracing import enable_tracing, flush_tracing, disable_tracing
enable_tracing(backend="jsonl", output="./traces/project.jsonl")
# Run the explicitly selected experiment here.
flush_tracing()
disable_tracing()
```

Keep USER_ID/workspace metadata with results. Content-bearing traces and reports
may include private documents; inspect and redact them before sharing. Use the
[observability example](../observability/README.md) for an operator-managed OTLP
collector. No vendor SDK, cloud account or shared vendor credentials are required.

## 8. Review saved experiments

Use `seocho runs compare` to check matched conditions and `seocho runs view` to
inspect local reports. Missing telemetry remains unavailable. Original experiment
receipts are retained; exporting a view does not rewrite them.
