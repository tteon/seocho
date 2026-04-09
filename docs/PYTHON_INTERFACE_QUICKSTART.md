# Getting Started

## Python Interface Quickstart

Get started with SEOCHO's official Python SDK in under 5 minutes.

This interface now has two layers:

- module-level convenience functions for quick scripts:
  - `seocho.configure()`
  - `seocho.ask()`
  - `seocho.chat()`
  - `seocho.debate()`
- explicit client objects for applications and libraries:
  - `Seocho`
  - `AsyncSeocho`

Underneath, SEOCHO still uses graph-backed retrieval, provenance, and semantic routing.

## Prerequisites

- Python 3.11 or higher
- running SEOCHO backend at `http://localhost:8001`

Start SEOCHO first:

```bash
make setup-env
make up
```

Install from PyPI when it is published:

```bash
pip install seocho
```

If you are developing from this repository, use the editable install instead:

```bash
pip install -e ".[dev]"
```

## 1. Configure The SDK

Fastest script-style setup:

```python
import seocho


seocho.configure(base_url="http://localhost:8001", workspace_id="default")
```

Then call the module directly:

```python
import seocho


answer = seocho.ask("What do you know about Alex?")
print(answer)
```

## 2. Initialize An Explicit Client

For applications and libraries, prefer an explicit client object:

```python
from seocho import Seocho


seocho = Seocho()
```

Default configuration:

- `base_url="http://localhost:8001"`
- `workspace_id="default"`

You can override them:

```python
seocho = Seocho(base_url="http://localhost:8001", workspace_id="team_alpha")
```

## 3. Add A Memory

```python
memory = seocho.add(
    "Hi, I'm Alex. I work on retail accounts and prefer graph-based reasoning.",
    metadata={"source": "python_quickstart"},
)

print(memory.memory_id)
```

## 4. Search Memories

```python
results = seocho.search("What do you know about me?")

for result in results:
    print(result.memory_id, result.score, result.content)
```

## 5. Ask From Memory

If you want the simplest possible response:

```python
answer = seocho.ask("What do you know about Alex?")
print(answer)
```

If you also want the retrieval evidence:

```python
response = seocho.chat("What do you know about Alex?")
print(response.assistant_message)
print(response.memory_hits)
print(response.evidence_bundle)
```

## 6. Semantic And Debate Runtime Calls

Semantic graph QA:

```python
semantic = seocho.semantic(
    "Tell me about Neo4j",
    databases=["kgnormal"],
)

print(semantic.route)
print(semantic.response)
```

Graph debate:

```python
debate = seocho.debate(
    "Compare what each graph knows about Alex",
    graph_ids=["kgnormal", "kgfinance"],
)

print(debate.debate_state)
print(debate.response)
print(debate.debate_results)
```

Platform chat mode with session history:

```python
turn = seocho.platform_chat(
    "Show graph labels in kgnormal",
    mode="semantic",
    session_id="demo-session",
    databases=["kgnormal"],
)

print(turn.assistant_message)
print(turn.history[-1].content)
```

## 7. Async Usage

If you prefer an async interface:

```python
import asyncio

from seocho import AsyncSeocho


async def main():
    seocho = AsyncSeocho()
    await seocho.add("SEOCHO turns memories into graph-backed retrieval.")
    results = await seocho.search("What does SEOCHO do?")
    for result in results:
        print(result.content)
    print(await seocho.ask("Summarize SEOCHO."))
    await seocho.aclose()


if __name__ == "__main__":
    asyncio.run(main())
```

## 8. Inspect Available Graphs

```python
graphs = seocho.graphs()

for graph in graphs:
    print(graph.graph_id, graph.database)
```

Expected graph descriptor fields:

- `graph_id`
- `database`
- `uri`
- `ontology_id`
- `vocabulary_profile`
- `description`

You can also inspect runtime registries:

```python
print(seocho.databases())
print(seocho.agents())
```

## 9. Archive A Memory

```python
result = seocho.delete(memory.memory_id)
print(result.status, result.archived_nodes)
```

## 10. CLI Alternative

The same workflow is available from the console:

```bash
seocho serve
seocho doctor
seocho add "SEOCHO turns memories into graph-backed retrieval."
seocho search "What does SEOCHO do?"
seocho chat "Summarize SEOCHO."
seocho graphs
seocho stop
```

If your `.env` still has the example OpenAI key placeholder, `seocho serve` switches to a local fallback key automatically so the stack can still boot for basic verification.

Use JSON output when you want raw payloads:

```bash
seocho add "hello" --json
seocho search "hello" --json
```

## 11. Build And Publish The Package

From the repository root:

```bash
pip install -e ".[dev]"
uv build
twine check dist/*
```

That produces both an sdist and a wheel under `dist/`.

If you are ready to publish, upload the checked artifacts to PyPI using your
normal release process.

## 12. Advanced Ontology / Vocabulary Injection

For advanced developers, there are two supported paths:

- governed path: pass `approved_artifact_id` or `approved_artifacts`
- per-ingest override path: pass `prompt_context`

Recommended order of use:

1. put durable ontology and vocabulary into semantic artifacts
2. approve them through the artifact lifecycle
3. reference them with `approved_artifact_id`
4. use `prompt_context` only for record- or workflow-specific overrides

Typed SDK variant:

```python
from seocho import (
    ApprovedArtifacts,
    KnownEntity,
    OntologyCandidate,
    OntologyClass,
    OntologyProperty,
    SemanticPromptContext,
    ShaclCandidate,
    ShaclPropertyConstraint,
    ShaclShape,
    VocabularyCandidate,
    VocabularyTerm,
)

prompt_context = SemanticPromptContext(
    instructions=[
        "Prefer our customer ontology labels.",
        "Use approved retail account vocabulary when aliases appear.",
    ],
    known_entities=[
        KnownEntity(name="ACME Holdings", label="Company"),
        KnownEntity(name="Seoul Retail Account", label="RetailAccount"),
    ],
    vocabulary_candidate=VocabularyCandidate(
        terms=[
            VocabularyTerm(
                pref_label="Retail Account",
                alt_labels=["Store Account", "Account"],
                sources=["developer_override"],
            )
        ]
    ),
)

approved_artifacts = ApprovedArtifacts(
    ontology_candidate=OntologyCandidate(
        ontology_name="customer",
        classes=[
            OntologyClass(
                name="RetailAccount",
                description="Customer account in the retail segment",
                properties=[OntologyProperty(name="owner", datatype="string")],
            )
        ],
    ),
    shacl_candidate=ShaclCandidate(
        shapes=[
            ShaclShape(
                target_class="RetailAccount",
                properties=[ShaclPropertyConstraint(path="owner", constraint="required")],
            )
        ]
    ),
)
```

Example:

```python
memory = seocho.add(
    "ACME acquired Beta in 2024. The Seoul retail account moved under ACME Holdings.",
    metadata={"source": "advanced_quickstart"},
    approved_artifact_id="sa_approved_finance_v1",
    prompt_context={
        "instructions": [
            "Prefer our customer ontology labels.",
            "Use approved retail account vocabulary when aliases appear.",
        ],
        "known_entities": ["ACME Holdings", "Seoul Retail Account"],
        "ontology_candidate": {
            "classes": [
                {
                    "name": "RetailAccount",
                    "description": "Customer account in the retail segment",
                    "aliases": ["Account"],
                    "properties": [{"name": "owner", "datatype": "string"}],
                }
            ],
            "relationships": [
                {
                    "type": "OWNS_ACCOUNT",
                    "source": "Company",
                    "target": "RetailAccount",
                    "aliases": ["manages_account"],
                }
            ],
        },
        "vocabulary_candidate": {
            "terms": [
                {
                    "pref_label": "Retail Account",
                    "alt_labels": ["Store Account", "Account"],
                    "sources": ["developer_override"],
                }
            ]
        },
    },
)
```

What this does at runtime:

- graph target metadata (`graph_id`, `ontology_id`, `vocabulary_profile`) is injected automatically when available
- approved ontology/vocabulary artifacts are injected first
- `prompt_context` is merged on top for this specific ingest
- the merged context is used in ontology extraction, SHACL extraction, entity extraction, and entity linking

CLI equivalent:

```bash
seocho add "ACME acquired Beta in 2024." \
  --approved-artifact-id sa_approved_finance_v1 \
  --prompt-context '{"instructions":["Prefer our customer ontology labels."]}'
```

Artifact lifecycle from the CLI:

```bash
seocho artifacts list --status approved
seocho artifacts get sa_approved_finance_v1 --json
seocho artifacts approve sa_draft_1 --approved-by reviewer
seocho artifacts deprecate sa_approved_finance_v1 --deprecated-by reviewer
```

Governance helpers from the CLI:

```bash
seocho artifacts validate --artifact-file artifact.json
seocho artifacts diff \
  --left-artifact-id sa_approved_finance_v1 \
  --right-artifact-file artifact.json
seocho artifacts apply sa_approved_finance_v1 "ACME acquired Beta in 2024."
```

Create a draft artifact from a JSON file:

```bash
seocho artifacts create-draft --artifact-file artifact.json
```

`artifact.json` shape:

```json
{
  "name": "finance_v2",
  "ontology_candidate": {
    "ontology_name": "finance",
    "classes": [],
    "relationships": []
  },
  "shacl_candidate": {
    "shapes": []
  },
  "vocabulary_candidate": {
    "schema_version": "vocabulary.v2",
    "profile": "skos",
    "terms": []
  }
}
```

The SDK exposes the same helpers locally:

```python
validation = seocho.validate_artifact(approved_artifacts)
diff = seocho.diff_artifacts({"name": "finance_v1", **approved_artifacts.to_dict()}, artifact_json_payload)
created = seocho.apply_artifact(
    "sa_approved_finance_v1",
    "ACME acquired Beta in 2024.",
    prompt_context=prompt_context,
)
```

## 10. Multi-Instance Graph Configuration

Graph targets are configured by `SEOCHO_GRAPH_REGISTRY_FILE`.

Default file:

```bash
extraction/conf/graphs/default.yaml
```

Each entry can point at a different Neo4j/DozerDB instance:

```yaml
graphs:
  - graph_id: customer360
    database: customer360
    uri: bolt://customer360-neo4j:7687
    user: neo4j
    password: password
    ontology_id: customer
    vocabulary_profile: vocabulary.v2
    description: Customer memory graph
```

You can pass graph routing hints when searching or asking:

```python
results = seocho.search("What does customer360 know about Alex?", graph_ids=["customer360"])
answer = seocho.ask("Summarize Alex in customer360.", graph_ids=["customer360"])
```
