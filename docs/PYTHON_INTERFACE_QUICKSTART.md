# Python Interface Quickstart

SEOCHO's Python SDK is designed around a simple rule:

- default to the semantic layer
- turn on bounded repair when retrieval is insufficient
- use debate only as an explicit advanced mode

If you already have the runtime running at `http://localhost:8001`, you can
start here immediately.

If your first question is how to load your own records safely, read
`APPLY_YOUR_DATA.md` alongside this guide.

## 1. Install

Pick the mode first:

- HTTP client mode only:

```bash
pip install seocho
```

- local SDK engine from the published package:

```bash
pip install "seocho[local]"
```

- repository development or local CLI authoring from a clone:

```bash
pip install -e ".[dev]"
```

- offline ontology governance helpers:

```bash
pip install "seocho[ontology]"
```

Important:

- `pip install seocho` is enough for remote HTTP client mode.
- `pip install "seocho[local]"` is the simplest published-package path for direct `Neo4jGraphStore(...)` workflows.
- local engine mode requires a real graph backend and local-mode dependencies.
- `pip install -e ".[dev]"` remains the right path when you are editing the repo itself.

If you are iterating on schema evolution, use the offline governance CLI:

```bash
seocho ontology check --schema schema.jsonld
seocho ontology export --schema schema.jsonld --format shacl --output shacl.json
seocho ontology diff --left schema_v1.jsonld --right schema_v2.jsonld
```

Recommendation:

- keep a stable `package_id` on the ontology and bump `version` semantically
- treat `seocho ontology diff` as the first migration warning gate before runtime rollout

## 2. Configure

You have two primary SDK shapes:

| Mode | Constructor | When to use it |
|------|-------------|----------------|
| HTTP client | `Seocho(base_url="http://localhost:8001", workspace_id="default")` | consume a running SEOCHO runtime |
| Local engine | `Seocho(ontology=..., graph_store=..., llm=...)` | SDK authoring, experiments, direct graph access |

Fastest script-style setup:

```python
import seocho

seocho.configure(base_url="http://localhost:8001", workspace_id="default")
```

Application-style setup:

```python
from seocho import Seocho

client = Seocho(base_url="http://localhost:8001", workspace_id="default")
```

Important parameters:

- `base_url`: root URL of the running SEOCHO runtime
- `workspace_id`: required logical scope for runtime-facing data and queries
- `user_id`, `agent_id`, `session_id`: optional scope fields for memory/runtime tracing

Local engine example:

```python
from seocho import Seocho, Ontology
from seocho.store import Neo4jGraphStore, OpenAIBackend

client = Seocho(
    ontology=Ontology(name="demo"),
    graph_store=Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password"),
    llm=OpenAIBackend(model="gpt-4o"),
    workspace_id="default",
)
```

Local-mode constructor parameters you should understand:

- `graph_store`: Bolt-backed DozerDB/Neo4j connection used by the SDK
- `llm`: OpenAI-compatible extraction/query backend
- `ontology`: schema contract that drives extraction, validation, and query generation

## 2.1 Use One Ontology Across Local And Runtime Paths

This is the intended product shape:

1. author one ontology
2. use it directly in local SDK mode
3. convert that same ontology into typed runtime artifacts when you need the server/runtime path

```python
from seocho import Ontology, Seocho

ontology = Ontology.from_jsonld("schema.jsonld")
client = Seocho(ontology=ontology)

artifacts = client.approved_artifacts_from_ontology()
prompt_context = client.prompt_context_from_ontology(
    instructions=["Prefer finance ontology labels and constraints."]
)
draft = client.artifact_draft_from_ontology(name="finance_core_v1")
```

Use the helpers like this:

- `approved_artifacts_from_ontology()`: build runtime-safe `ApprovedArtifacts`
- `prompt_context_from_ontology()`: build typed semantic prompt context from the same ontology
- `artifact_draft_from_ontology()`: build a draft payload for the semantic artifact lifecycle APIs

That keeps `schema.jsonld`, local SDK prompts, SHACL validation, and runtime semantic artifacts on one contract.

## 3. Put Your Data In

### 3.1 Add one memory

Use this when you want the shortest path from text to graph-backed memory.

```python
memory = client.add(
    "Alex manages the Seoul retail account and prefers ontology-aware retrieval.",
    metadata={"source": "sdk_quickstart"},
)

print(memory.memory_id)
```

If you are indexing through the runtime path but still want the same ontology to
govern the request:

```python
result = client.add_with_details(
    "ACME acquired Beta in 2024.",
    prompt_context=client.prompt_context_from_ontology(),
    approved_artifacts=client.approved_artifacts_from_ontology(),
)

print(result.ingest_summary)
```

### 3.2 Ingest a batch of raw records

Use this when you want SEOCHO to extract entities and relationships into a
target graph.

```python
result = client.raw_ingest(
    [
        {"id": "r1", "content": "ACME acquired Beta in 2024."},
        {"id": "r2", "content": "Beta provides risk analytics to ACME."},
    ],
    target_database="kgruntime",
)

print(result.status)
print(result.records_processed)
```

## 4. Ask the Simple Way

If you only want a string answer:

```python
print(client.ask("What do you know about Alex?"))
```

If you also want evidence:

```python
response = client.chat("What do you know about Alex?")

print(response.assistant_message)
print(response.evidence_bundle)
```

## 5. Use the Semantic Layer First

This is the main graph-QA path. Prefer `graph_ids` instead of raw database names
when you know the intended graph target.

```python
semantic = client.semantic(
    "Who manages the Seoul retail account?",
    graph_ids=["kgnormal"],
)

print(semantic.route)
print(semantic.response)
print(semantic.support.status)
print(semantic.evidence.grounded_slots)
print(semantic.evidence.missing_slots)
print(semantic.run_record.run_id)
```

## 6. Turn On Repair When the Query Is Hard

If the question is harder, ambiguous, or likely to need relation-path repair,
keep the semantic path but enable bounded reasoning mode.

```python
semantic = client.semantic(
    "What is Neo4j related to GraphRAG?",
    graph_ids=["kgnormal"],
    reasoning_mode=True,
    repair_budget=2,
)

print(semantic.route)
print(semantic.semantic_context["reasoning"])
print(semantic.strategy.executed_mode)
print(semantic.strategy.next_mode_hint)
print(semantic.evidence.missing_slots)
```

What `reasoning_mode` does:

- keeps the semantic layer as the first execution path
- validates constrained Cypher before execution
- retries with a small repair budget when slot fill is insufficient
- avoids jumping straight to multi-agent debate

Parameter guidance:

- `reasoning_mode=False`: fastest default path
- `reasoning_mode=True`: enable bounded semantic repair for harder questions
- `repair_budget=0`: no retry
- `repair_budget=1..2`: practical default for hard graph questions
- `graph_ids=[...]`: preferred public routing key when you know the graph target
- `databases=[...]`: physical database scope when you are working at DB level

## 7. Use the Builder Surface

For application code, the builder can be easier to read.

```python
result = (
    client.plan("Who manages the Seoul retail account?")
    .on_graph("kgnormal")
    .with_repair_budget(2)
    .run()
)

print(result.route)
print(result.support.status)
print(result.strategy.executed_mode)
```

Mode selection rules:

- `plan(...).run()` defaults to semantic execution
- `plan(...).react()` uses graph-scoped single-agent routing
- `plan(...).advanced()` or `plan(...).debate()` uses explicit debate mode
- `result.strategy.advanced_debate_recommended` tells you when debate is worth trying

## 8. Use Advanced Mode Only When You Really Need Debate

Debate is not the default retrieval path. It is for cross-graph comparison or
cases where you explicitly want multiple graph specialists to answer.

```python
advanced = client.advanced(
    "Compare what the baseline and finance graphs know about Alex.",
    graph_ids=["kgnormal", "kgfibo"],
)

print(advanced.debate_state)
print(advanced.response)
print(advanced.debate_results)
```

Equivalent explicit call:

```python
advanced = client.debate(
    "Compare what the baseline and finance graphs know about Alex.",
    graph_ids=["kgnormal", "kgfibo"],
)
```

## 9. Inspect Graph Targets

```python
for graph in client.graphs():
    print(graph.graph_id, graph.database, graph.ontology_id, graph.vocabulary_profile)
```

Expected fields:

- `graph_id`
- `database`
- `ontology_id`
- `vocabulary_profile`
- `description`

## 10. Module-Level Convenience API

If you prefer `import seocho` style scripting:

```python
import seocho

seocho.configure(base_url="http://localhost:8001", workspace_id="default")

print(seocho.ask("What do you know about Alex?"))

semantic = seocho.semantic(
    "Who manages the Seoul retail account?",
    graph_ids=["kgnormal"],
    reasoning_mode=True,
    repair_budget=2,
)
print(semantic.response)

advanced = seocho.advanced(
    "Compare what the baseline and finance graphs know about Alex.",
    graph_ids=["kgnormal", "kgfibo"],
)
print(advanced.debate_state)

recent_runs = seocho.semantic_runs(limit=5, route="lpg")
print(recent_runs[0].run_id)
```

## 11. Async API

```python
import asyncio
from seocho import AsyncSeocho


async def main() -> None:
    client = AsyncSeocho()
    answer = await client.ask("What does SEOCHO do?")
    print(answer)
    await client.aclose()


asyncio.run(main())
```

## 12. Publish A Portable SDK Runtime

When one developer builds a local SDK configuration and wants other developers
to consume it over HTTP client mode, export a portable runtime bundle and serve
it behind the small bundle HTTP adapter.

Author the bundle:

```python
from seocho import DeepSeekBackend, LanceDBVectorStore, Seocho

client = Seocho(
    ontology=ontology,
    graph_store=graph_store,
    llm=DeepSeekBackend(model="deepseek-chat"),
    vector_store=LanceDBVectorStore(uri="./.lancedb", table_name="team_memory"),
    agent_config=agent_config,
)

bundle = client.export_runtime_bundle(
    "portable.bundle.json",
    app_name="team-memory-runtime",
    default_database="neo4j",
)

print(bundle.app_name)
```

You can also do the same from the CLI:

```bash
seocho bundle export \
  --schema schema.jsonld \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password password \
  --provider deepseek \
  --model deepseek-chat \
  --database neo4j \
  --output portable.bundle.json
```

Serve the bundle:

```bash
seocho serve-http --bundle portable.bundle.json --host 0.0.0.0 --port 8010
```

Consume it from another process or machine with the standard HTTP client mode:

```python
from seocho import Seocho

remote = Seocho(base_url="http://localhost:8010", workspace_id="default")

print(remote.ask("What do you know about Alex?"))
semantic = remote.semantic("Who manages Seoul retail?", databases=["neo4j"])
print(semantic.route)
```

Portable bundle limits in the current implementation:

- supported backend export: `Neo4jGraphStore`
- supported LLM export: OpenAI-compatible backends
  - `OpenAIBackend`
  - `DeepSeekBackend`
  - `KimiBackend`
  - `GrokBackend`
- portable config only; custom Python strategies are rejected
- intended HTTP surface: `add`, `ask`, `chat`, `search`, and basic `semantic`
- not a replacement for the full main server runtime when you need debate,
  governance, or the complete artifact lifecycle

## 13. Choose A Provider And Vector Backend

OpenAI-compatible providers are available through the same local SDK surface:

```python
from seocho import DeepSeekBackend, GrokBackend, KimiBackend, OpenAIBackend

openai_llm = OpenAIBackend(model="gpt-4o-mini")
deepseek_llm = DeepSeekBackend(model="deepseek-chat")
kimi_llm = KimiBackend(model="kimi-k2.5")
grok_llm = GrokBackend(model="grok-4.20-reasoning")
```

For semantic search, choose an in-memory or persistent vector backend:

```python
from seocho import FAISSVectorStore, LanceDBVectorStore

faiss_vectors = FAISSVectorStore(model="text-embedding-3-small")
lancedb_vectors = LanceDBVectorStore(
    uri="./.lancedb",
    table_name="team_memory",
    model="text-embedding-3-small",
)
```
## 14. Inspect Semantic Run History

The runtime now keeps a SQLite-backed semantic run registry outside the graph
store. You can inspect it from the SDK without touching internal files.

```python
recent_runs = client.semantic_runs(limit=10, route="lpg")
print(recent_runs[0].run_id)
print(recent_runs[0].support.status)

run_record = client.semantic_run(recent_runs[0].run_id)
print(run_record.strategy.executed_mode)
print(run_record.evidence_summary)
```

## 15. Run Manual-Gold Evaluation

Use the SDK evaluation harness when you want a small regression matrix over
`question_only`, `reference_only`, `semantic_direct`, and `semantic_repair`.

```python
from seocho import ManualGoldCase, SemanticEvaluationHarness

harness = SemanticEvaluationHarness(client)
summary = harness.run_matrix(
    [
        ManualGoldCase(
            case_id="neo4j-1",
            question="What is Neo4j connected to?",
            graph_ids=["kgnormal"],
            expected_intent_id="relationship_lookup",
            required_slots={
                "source_entity": "Neo4j",
                "target_entity": "Cypher",
                "relation_paths": "USES",
            },
            preferred_relations=["USES"],
            repair_budget=2,
        )
    ]
)

print(summary.aggregate_metrics["semantic_direct"])
print(summary.aggregate_metrics["semantic_repair"])
```

These metrics are intentionally narrow:

- `intent_match_rate`
- `support_rate`
- `required_answer_slot_coverage_manual`
- `preferred_evidence_hit_rate`

## 14. CLI Equivalents

```bash
seocho serve
seocho add "Alex manages the Seoul retail account."
seocho search "Who manages the Seoul retail account?"
seocho chat "What do you know about Alex?"
seocho graphs
seocho stop
```

## 15. Mental Model

Use this decision rule:

1. Start with `ask` or `chat` for memory-first use.
2. Use `semantic(...)` for graph-grounded retrieval.
3. Add `reasoning_mode=True` before reaching for debate.
4. Use `advanced()` only when you explicitly want multi-agent comparison.

## 16. Agent-Level Sessions

Sessions maintain context across indexing and querying operations.
Three execution modes are available via ``AgentConfig``:

```python
from seocho import Seocho, Ontology, AgentConfig, RoutingPolicy, AGENT_PRESETS
from seocho.store import Neo4jGraphStore, OpenAIBackend

onto = Ontology.from_jsonld("schema.jsonld")
store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")
llm = OpenAIBackend(model="gpt-4o-mini")

# Pipeline mode (default) — deterministic, no LLM reasoning about flow
s = Seocho(ontology=onto, graph_store=store, llm=llm)

with s.session("analysis") as sess:
    sess.add("ACME acquired Beta in 2024.")
    sess.add("Beta provides risk analytics to ACME.")
    # QueryAgent sees structured context derived from the same ontology
    answer = sess.ask("What does ACME know about Beta?")
```

If the same ontology must also govern runtime ingest, reuse it instead of
authoring a second payload:

```python
runtime_artifacts = s.approved_artifacts_from_ontology()
runtime_prompt_context = s.prompt_context_from_ontology()
```

### Agent mode (LLM decides tool execution order)

```python
s = Seocho(
    ontology=onto, graph_store=store, llm=llm,
    agent_config=AgentConfig(execution_mode="agent"),
)
```

### Supervisor with hand-off (explicit opt-in)

```python
s = Seocho(
    ontology=onto, graph_store=store, llm=llm,
    agent_config=AgentConfig(
        execution_mode="supervisor",
        handoff=True,
        routing_policy=RoutingPolicy.thorough(),
    ),
)

with s.session("auto") as sess:
    sess.run("ACME acquired Beta in 2024.")      # → IndexingAgent
    sess.run("What does ACME know about Beta?")  # → QueryAgent
```

### Routing policy presets

| Preset | Latency | Tokens | Quality | Use when |
|--------|---------|--------|---------|----------|
| `RoutingPolicy.fast()` | 70% | 20% | 10% | Speed matters most |
| `RoutingPolicy.balanced()` | 33% | 33% | 34% | General use |
| `RoutingPolicy.thorough()` | 10% | 10% | 80% | Accuracy matters most |

## 17. Ontology Merge

Combine two ontologies when integrating new domains:

```python
finance = Ontology.from_jsonld("finance.jsonld")
legal = Ontology.from_jsonld("legal.jsonld")

# Union: combine properties from both
combined = finance.merge(legal)
# → Company has revenue (finance) + jurisdiction (legal)

# Strict: raise on type conflicts
combined = finance.merge(legal, strategy="strict")

combined.to_jsonld("combined.jsonld")
```

Strategies: ``union`` (default), ``left_wins``, ``right_wins``, ``strict``.

## 18. Where Ontology And Runtime Files Live

Common locations:

- default ontology file in CLI examples: `schema.jsonld`
- local graph state for docker stack: `data/neo4j/`
- semantic artifact store: `outputs/semantic_artifacts/`
- rule profile registry: `outputs/rule_profiles/rule_profiles.db`
- semantic run metadata: `outputs/semantic_metadata/`
- trace file path: `SEOCHO_TRACE_JSONL_PATH`

Use these commands to inspect them directly:

```bash
seocho ontology check --schema schema.jsonld
seocho ontology export --schema schema.jsonld --format shacl --output shacl.json
seocho artifacts list --status approved
curl -sS "http://localhost:8001/semantic/artifacts?workspace_id=default" | jq .
```

See `FILES_AND_ARTIFACTS.md` for the full map.

The practical ontology file flow is:

1. save the ontology as `schema.jsonld`
2. inspect it with `seocho ontology check --schema schema.jsonld`
3. derive SHACL with `seocho ontology export --schema schema.jsonld --format shacl --output shacl.json`
4. use the same ontology to build runtime artifacts with `approved_artifacts_from_ontology()`

## 19. Read Next

- `APPLY_YOUR_DATA.md`
- `QUICKSTART.md`
- `GRAPH_MEMORY_API.md`

That keeps the default path deterministic and inspectable while still leaving a
high-power advanced mode available.
