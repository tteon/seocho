# SEOCHO Beginner Guide

This guide is for a first-time user who wants to understand SEOCHO by running a
small end-to-end workflow.

If you only need the shortest command path, use [QUICKSTART.md](QUICKSTART.md).
If you want the full system architecture, use [ARCHITECTURE.md](ARCHITECTURE.md).

## 1. The One-Minute Mental Model

SEOCHO sits between an agent and a graph database.

The user provides:

- data: text, filings, notes, records, or documents
- ontology: the schema contract for entities, relationships, and graph shape
- optional YAML designs: indexing and agent behavior presets

SEOCHO does:

- indexes the data into an ontology-aligned graph
- keeps provenance and artifact metadata visible
- routes questions through ontology-aware graph retrieval
- lets the same ontology shape local SDK and runtime behavior

The core product contract is:

```text
data + ontology + optional YAML design
        -> SEOCHO indexing
        -> graph + artifacts
        -> ontology-grounded query / agent answer
```

## 2. How The Architecture Maps To Your Code

Most users should start with the public `Seocho` facade:

```python
from seocho import Ontology, Seocho

ontology = Ontology.from_jsonld("schema.jsonld")
client = Seocho.local(ontology, llm="openai/gpt-4o-mini")

client.add("ACME acquired Beta in 2024.", database="financekg")
answer = client.ask("Who did ACME acquire?", database="financekg")
```

That small script crosses five architecture layers:

| User action | Internal layer | Canonical module |
|---|---|---|
| `Ontology.from_jsonld(...)` | schema contract | `seocho/ontology.py` |
| `Seocho.local(...)` | public SDK facade + local engine | `seocho/client.py`, `seocho/local_engine.py` |
| `client.add(...)` | indexing/data plane | `seocho/index/` |
| graph write/query | graph store adapter | `seocho/store/graph.py` |
| `client.ask(...)` | query/control plane | `seocho/query/` |

The same call shape can later move behind the runtime API. The boundary is:

| SDK owns | Runtime owns |
|---|---|
| ontology shaping, indexing, query planning, agent semantics | request validation, workspace/database registry, policy, route wiring |

SEOCHO also has internal seams that explain the system design. You normally do
not import these in beginner code, but they are the architecture anchors.

Indexing/data-plane seam:

```python
from seocho.index.ingestion_facade import IngestRequest

request = IngestRequest(
    content="ACME acquired Beta in 2024.",
    workspace_id="finance-dev",
    database="financekg",
    category="filing",
    metadata={"source": "tutorial"},
)
```

Query/control-plane seam:

```python
from seocho.query.query_proxy import QueryRequest

request = QueryRequest(
    cypher="MATCH (c:Company)-[:ACQUIRED]->(t:Company) RETURN c, t",
    workspace_id="finance-dev",
    database="financekg",
    ontology_profile="finance-core",
)
```

Event/observability seam:

```python
from seocho.events import DomainEvent

event = DomainEvent(
    kind="query.succeeded",
    workspace_id="finance-dev",
    payload={"database": "financekg", "result_count": 1},
)
```

This is why SEOCHO is described as a modular monolith: the deployment can stay
simple, while indexing, querying, runtime policy, and observability have clear
internal seams.

## 3. Pick Your First Runtime

| Path | Use when | Requirement |
|---|---|---|
| Embedded local SDK | You want the fastest learning loop | `pip install "seocho[local]"` and an LLM key |
| Local platform stack | You want UI, API docs, and DozerDB together | `make setup-env && make up` |
| Remote HTTP client | Someone else is running SEOCHO | `pip install seocho` and `base_url` |

Start with embedded local SDK unless you explicitly need the UI or HTTP APIs.

## 4. Install

```bash
uv pip install "seocho[local]"
```

Set one provider key. OpenAI is the default path:

```bash
export OPENAI_API_KEY=...
```

Provider presets also support OpenAI-compatible APIs such as DeepSeek, Kimi,
Grok/xAI, and Qwen when the matching environment variable is present.

## 5. First Local Run

```python
from seocho import NodeDef, Ontology, P, RelDef, Seocho

ontology = Ontology(
    name="company_memory",
    nodes={
        "Company": NodeDef(properties={"name": P(str, unique=True)}),
        "Product": NodeDef(properties={"name": P(str, unique=True)}),
    },
    relationships={
        "BUILDS": RelDef(source="Company", target="Product"),
    },
)

client = Seocho.local(
    ontology,
    llm="openai/gpt-4o-mini",
    workspace_id="beginner",
)

client.add(
    "Acme builds RadarBox, a product for monitoring supply chain risk.",
    database="beginnerkg",
)

answer = client.ask(
    "What product does Acme build?",
    database="beginnerkg",
    reasoning_mode=True,
    repair_budget=1,
)

print(answer)
```

What happened:

- `Ontology(...)` defined the graph contract.
- `Seocho.local(...)` created a local SDK client with embedded LadybugDB.
- `add(...)` extracted ontology-shaped graph records.
- `ask(...)` generated and executed ontology-aware graph retrieval.

## 6. Bring A JSON-LD Ontology

Most real teams should keep the ontology in version control.

```python
from seocho import Ontology, Seocho

ontology = Ontology.from_jsonld("schema.jsonld")

client = Seocho.local(
    ontology,
    llm="openai/gpt-4o-mini",
    workspace_id="finance-dev",
)
```

Use the sample finance JSON-LD files as a starting point:

- `examples/datasets/fibo_base.jsonld`
- `examples/datasets/fibo_plus.jsonld`
- `examples/datasets/fibo_minus.jsonld`

## 7. Add YAML Indexing Design

Indexing design YAML lets a user declare how graph-model-specific ingestion
should behave.

```python
from seocho import Ontology, Seocho

ontology = Ontology.from_jsonld("examples/datasets/fibo_plus.jsonld")

client = Seocho.from_indexing_design(
    "examples/indexing_designs/lpg_finance_provenance.yaml",
    ontology=ontology,
    llm="openai/gpt-4o-mini",
    workspace_id="finance-dev",
)

client.add(
    "Cboe Data and Access Solutions revenue increased from 427.7 million to 539.2 million.",
    database="financekg",
    metadata={"source": "tutorial"},
)
```

The YAML must include an `ontology:` block. If it does not, SEOCHO raises a
`ValueError` instead of silently running without a schema contract.

Important YAML fields:

| Field | Meaning |
|---|---|
| `graph_model` | `lpg`, `rdf`, or `hybrid` |
| `storage_target` | `ladybug`, `neo4j`, or `dozerdb` |
| `ontology.profile` | stable ontology binding name |
| `ingestion.extraction_strategy` | extraction behavior hint |
| `materialization.provenance_mode` | how much source metadata to preserve |
| `reasoning_cycle` | anomaly-driven inquiry defaults |

## 8. Add YAML Agent Design

Agent design YAML lets a user declare an agent pattern while keeping ontology
binding explicit.

```python
from seocho import Ontology, Seocho

ontology = Ontology.from_jsonld("examples/datasets/fibo_plus.jsonld")

client = Seocho.from_agent_design(
    "examples/agent_designs/planning_multi_agent_finance.yaml",
    ontology=ontology,
    llm="openai/gpt-4o-mini",
    workspace_id="finance-dev",
)

answer = client.ask(
    "What changed in the company's revenue trend?",
    database="financekg",
    reasoning_mode=True,
    repair_budget=2,
)
```

Supported beginner-facing agent patterns:

| Pattern | Use when |
|---|---|
| `planning_multi_agent` | split a complex finance task into retrieval and synthesis |
| `reflection_chain` | verify answer support before final response |
| `memory_tool_use` | combine graph memory with external tools |

## 9. Know The Parameters

| Parameter | Where used | Why it matters |
|---|---|---|
| `workspace_id` | local and runtime | tenant or project scope |
| `llm` | local SDK | provider/model, e.g. `openai/gpt-4o-mini` |
| `graph` | local SDK | omit for embedded LadybugDB, pass Bolt URI for Neo4j/DozerDB |
| `database` | `add()` / `ask()` | target graph database or local graph namespace |
| `metadata` | `add()` | source, provenance, tags, design defaults |
| `reasoning_mode` | `ask()` / semantic runtime | bounded query repair |
| `repair_budget` | `ask()` / semantic runtime | maximum repair attempts |
| `indexing_design` | YAML construction | graph-model-aware ingestion defaults |
| `agent_design` | YAML construction | reusable agent pattern defaults |

## 10. Inspect What SEOCHO Wrote

Beginner local state usually lives under:

```text
.seocho/local.lbug
.seocho/benchmarks/local/
outputs/
```

For a full artifact map, read [FILES_AND_ARTIFACTS.md](FILES_AND_ARTIFACTS.md).

For runtime API surfaces:

```bash
make setup-env
make up
open http://localhost:8001/docs
```

## 11. Common Beginner Mistakes

| Symptom | Likely cause | Fix |
|---|---|---|
| empty or generic answer | graph slots did not preserve the answer evidence | inspect graph writes and source spans |
| `ValueError` about ontology binding | YAML omitted `ontology.profile` or equivalent | add an ontology binding |
| provider auth error | missing provider key | set `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, etc. |
| slow reasoning model run | reasoning model used for every indexing/query call | start with a chat model, then compare reasoners selectively |
| benchmark result looks poor | benchmark uses reference QA, not demo success | inspect diagnosis codes before changing models |

## 12. What To Read Next

Recommended learning path:

1. [QUICKSTART.md](QUICKSTART.md)
2. [BEGINNER_PIPELINES_DEMO.md](BEGINNER_PIPELINES_DEMO.md)
3. [PYTHON_INTERFACE_QUICKSTART.md](PYTHON_INTERFACE_QUICKSTART.md)
4. [INDEXING_DESIGN_SPECS.md](INDEXING_DESIGN_SPECS.md)
5. [AGENT_DESIGN_SPECS.md](AGENT_DESIGN_SPECS.md)
6. [ARCHITECTURE.md](ARCHITECTURE.md)
7. [presentations/SEOCHO_OVERVIEW_DEEP_DIVE.md](presentations/SEOCHO_OVERVIEW_DEEP_DIVE.md)
