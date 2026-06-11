# SEOCHO Quick Start

Goal: one successful local run in under 5 minutes.

If you only read one runtime document first, read this one.

This page is the onboarding path. Use it for first success and local product
verification, not for benchmark claims.

If you want the Python SDK path immediately, continue with
`docs/PYTHON_INTERFACE_QUICKSTART.md`.
If you want the bring-your-own-data path immediately, continue with
`docs/APPLY_YOUR_DATA.md`.
If you are measuring quality or latency, continue with `docs/BENCHMARKS.md`.

## 1. Prerequisites

- Docker and Docker Compose
- `OPENAI_API_KEY` recommended
- `curl` and `jq` for optional API checks

Without `OPENAI_API_KEY`, SEOCHO can still boot in local fallback mode for
basic verification.

Important:

- `pip install seocho` alone does not provision DozerDB/Neo4j for you.
- local runtime success still depends on the graph backend being reachable.
- `make up` starts the core local stack, not every legacy service in the repo.
- `make up` rebuilds an image-backed `extraction-service`, so `localhost:8001`
  reflects a known source snapshot instead of a dirty bind-mounted checkout.

## 1.1 Execution Modes Matter

SEOCHO exposes multiple query surfaces and they do not all use the same engine.

| Surface | Execution path | Use this when |
|---|---|---|
| `Seocho.local(...).ask(...)` | local SDK query path | you want a serverless ontology-first local run |
| `Seocho(base_url=...).ask(...)` | runtime primary query facade | you want one public surface that auto-routes to chat or semantic graph QA |
| `client.semantic(...)` | runtime advanced semantic graph QA | you want direct control of the semantic lane for debugging or expert use |
| `client.react(...)` | runtime router agent | you want provider-native reasoning plus tool use |
| `client.advanced(...)` / `client.debate(...)` | runtime debate orchestrator | you want explicit multi-agent comparison/synthesis |

Important implications:

- local `ask()` is not the same benchmark target as runtime `react()` or
  `advanced()`
- `reasoning_mode` + `repair_budget` belong to the semantic path
- `max_steps` + `tool_budget` belong to the runtime agentic paths
- if you are comparing providers fairly on tool use, do it on runtime
  `react/debate`, not local `ask()`

## 2. Setup

```bash
git clone https://github.com/tteon/seocho.git
cd seocho
make setup-env
```

## 3. Start the Runtime

```bash
make up
docker compose ps
```

If you explicitly want a live bind-mounted edit loop instead:

```bash
make up-live
```

Or through the local CLI:

```bash
pip install -e ".[dev]"
seocho serve
```

Published-package local engine path:

```bash
pip install "seocho[local]"
```

The default core stack is:

- `neo4j`
- `extraction-service`
- `evaluation-interface`

Expected local surfaces:

- Platform UI: `http://localhost:8501`
- Backend API docs: `http://localhost:8001/docs`
- DozerDB browser: `http://localhost:7474`

## 3.1 Isolated Per-Worktree Runtime (multi-instance)

When more than one worktree (or agent) needs to drive the runtime at the same
time, boot an **isolated instance** instead of a second full stack. SEOCHO
follows a single-neo4j, multi-database model: the heavyweight graph backend
started by `make up` stays **shared**, while each instance gets its own
ephemeral logical database, its own app-tier containers, and offset ports.

```bash
make up                       # shared stack once (neo4j + default app tier)
make up INSTANCE=alice        # isolated app tier: offset ports + ephemeral DB
make up INSTANCE=bob          # a second, concurrent, non-colliding instance
```

Each instance is derived deterministically from its id (`src/seocho/instance.py`):

| id      | API port | UI port | ephemeral DB     | compose project |
| ------- | -------- | ------- | ---------------- | --------------- |
| `alice` | `8880`   | `9180`  | `wt522b276a356b` | `seocho-alice`  |
| `bob`   | `8890`   | `9190`  | `wt48181acd22b3` | `seocho-bob`    |

The ephemeral database name is validated against the runtime contract
(`^[a-z][a-z0-9]{2,62}$`) before it is created on the shared neo4j. Teardown
removes **only that instance's** app tier and drops **only its** logical
database — the shared neo4j and every other instance are untouched:

```bash
make down INSTANCE=alice      # docker compose down (alice) + DROP DATABASE wt522…
make down                     # stop the shared stack
```

Equivalent SDK CLI form (and `--dry-run` to preview without docker):

```bash
seocho serve --instance alice
seocho stop  --instance alice
seocho serve --instance alice --dry-run --json   # inspect ports/DB/command
```

App-tier ports are hash-derived over 200 slots, so two distinct ids can in rare
cases land on the same slot (P(collision) ≈ 1% at 2 worktrees, ≈ 14% at 8);
`InstanceLayout.collides_with(...)` detects this. A port-slot collision is an
*availability* concern only — **data isolation is unaffected** because the data
plane routes by the ephemeral database key, which is derived independently of
the port slot (a 48-bit space; collisions negligible). This is validated in
`scripts/experiments/isolation_experiment.py`. Pick distinct short ids per
worktree (a branch name or tenant id works well).

## 4. First Success: UI Path

1. Open `http://localhost:8501`
2. Use the ingest panel or click the sample flow
3. Ask a semantic question

The default product path is:

- ingest data
- run semantic retrieval
- use bounded repair only when needed
- reserve debate for explicit advanced use

## 5. First Success: Direct API Path

Ingest two records:

```bash
curl -sS -X POST http://localhost:8001/platform/ingest/raw \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id": "default",
    "target_database": "kgruntime",
    "records": [
      {"id": "r1", "content": "ACME acquired Beta in 2024."},
      {"id": "r2", "content": "Beta provides risk analytics to ACME."}
    ]
  }' | jq .
```

Ask through the semantic endpoint:

```bash
curl -sS -X POST http://localhost:8001/run_agent_semantic \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id": "default",
    "query": "What is ACME related to?",
    "databases": ["kgruntime"],
    "query_mode": "graph_cot",
    "reasoning_mode": true,
    "repair_budget": 2
  }' | jq '{route, response, reasoning: .semantic_context.reasoning}'
```

## 6. First Success: Python SDK Path

```python
from seocho import Seocho

client = Seocho(base_url="http://localhost:8001", workspace_id="default")

client.raw_ingest(
    [
        {"id": "r1", "content": "ACME acquired Beta in 2024."},
        {"id": "r2", "content": "Beta provides risk analytics to ACME."},
    ],
    target_database="kgruntime",
)

receipt = client.ask_response(
    "What is ACME related to?",
    databases=["kgruntime"],
    cot_mode=True,
    reasoning_mode=True,
    repair_budget=2,
)

print(receipt.response)
print(receipt.runtime_mode)
print(receipt.semantic_context["reasoning"])
print(receipt.support.status)
print(receipt.strategy.next_mode_hint)
print(receipt.graph_cot["guardrail_verdict"]["decision"])

recent = client.semantic_runs(limit=5, route="lpg")
print(recent[0].run_id)
```

## 7. Use React Or Debate Only As Explicit Agentic Modes

If you want the runtime agent loop with bounded turns and tool usage:

```python
react = client.react(
    "What changed in ACME's graph?",
    graph_ids=["kgruntime"],
    max_steps=6,
    tool_budget=2,
)

print(react.response)
```

This is the correct surface for provider-native reasoning/tool-use experiments.

## 8. Use Debate Only as an Advanced Mode

If you explicitly want cross-graph comparison:

```python
advanced = client.advanced(
    "Compare what each graph knows about ACME.",
    graph_ids=["kgnormal", "kgfibo"],
    max_steps=8,
    tool_budget=3,
)

print(advanced.debate_state)
```

Stay on the semantic path first. Inspect `semantic.support`, `semantic.strategy`,
and `semantic.evidence` before reaching for debate.

## 9. Inspect Runtime Semantic History

```bash
curl -sS "http://localhost:8001/semantic/runs?workspace_id=default&limit=5&route=lpg" | jq .
```

## 10. Validate the Runtime

```bash
make e2e-smoke
```

## 11. Keep One Ontology Contract

If you author locally with `schema.jsonld` but ingest/query through the runtime,
do not maintain a second hand-written runtime payload. Use the same ontology to
build runtime-safe artifacts:

```python
from seocho import Ontology, Seocho

ontology = Ontology.load("schema.jsonld")
client = Seocho(ontology=ontology)

artifacts = client.approved_artifacts_from_ontology()
prompt_context = client.prompt_context_from_ontology()
```

`Ontology.load(...)` also accepts `.ttl`, so local authoring and governance can
start from Turtle directly.

If you want the explicit local engine instead of `Seocho.local(...)`, construct
`Seocho(ontology=..., graph_store=..., llm=...)`. All three are required to
activate in-process extraction and query execution.

For ontology version review before rollout:

```bash
seocho ontology check --schema schema.ttl
seocho ontology diff --left schema_v1.ttl --right schema_v2.ttl
seocho ontology report --schema schema_v2.ttl --output outputs/ontology_report.json
```

## 12. Troubleshooting

Check service state:

```bash
docker compose ps
docker compose logs --tail=200 extraction-service
docker compose logs --tail=200 evaluation-interface
```

Common issues:

- `OPENAI_API_KEY` missing or placeholder only
- port collision on `8001`, `8501`, `7474`, or `7687`
- graph database not ready yet

## 13. Know Where Your Files Go

The main local locations are:

- ontology file: usually `schema.jsonld`
- local graph data: `data/neo4j/`
- semantic artifacts: `outputs/semantic_artifacts/`
- rule profile registry: `outputs/rule_profiles/rule_profiles.db`
- semantic run metadata: `outputs/semantic_metadata/`
- JSONL tracing: path from `SEOCHO_TRACE_JSONL_PATH`

See [FILES_AND_ARTIFACTS.md](FILES_AND_ARTIFACTS.md) for the full map and
inspection commands.

## 14. GOPTS Layer-1 PROFILE Oracle (ADR-0097, optional)

The GOPTS cost-ranked Cypher emitter (ADR-0097) ships with a Layer-1
ranking-quality evaluation harness. The PROFILE oracle is opt-in — it
needs a live DozerDB to collect real `db_hits` per candidate plan.

**Start DozerDB**

```bash
docker compose up -d neo4j
# wait a few seconds for the bolt port to come up
```

**Load the FIBO-lite test corpus** (idempotent, workspace-scoped):

```bash
NEO4J_PASSWORD=... python -m scripts.eval.load_gopts_fibo_corpus
# tear down: python -m scripts.eval.load_gopts_fibo_corpus --teardown
```

**Run the live Layer-1 integration test**:

```bash
NEO4J_PASSWORD=... pytest -m integration_gopts tests/seocho/integration/
```

The test session auto-loads the corpus on entry and tears it down on
exit. It skips cleanly when `NEO4J_PASSWORD` is unset or the bolt
endpoint is unreachable — CI without Docker stays green.

**What the harness reports**

For each fixture, the harness compares the GOPTS cost model's ranking
to Neo4j PROFILE's `db_hits`-driven ranking:

- `avg_top1_accuracy` — fraction of fixtures where cost model picks
  PROFILE's top plan.
- `avg_ndcg_at_k` — graded relevance fallback for near-ties.
- `avg_kendall_tau` — full-ranking agreement diagnostic.

On today's catalog the harness reports 1.0 across all three — cost
model and PROFILE agree everywhere. That is a positive result: it
proves the cost-model defaults are well-calibrated against actual
`db_hits`. The harness exists to *catch* future calibration drift
when F8 (multi-plan execution) or richer alternatives change the
picks.

**Skipped fixtures**: the compose stack ships uniqueness constraints
on `FinancialMetric.name` (and similar metric subclasses). The Layer-1
live runner filters out fixtures 05/06 (`finance_metric_lookup`,
`finance_metric_delta`) because they need multi-year metric rows that
the constraint forbids loading. Mock-oracle Layer-1
(`pytest tests/seocho/test_gopts_ranking.py`) still covers them.

## 13. Read Next

- `docs/PYTHON_INTERFACE_QUICKSTART.md`
- `docs/APPLY_YOUR_DATA.md`
- `docs/TUTORIAL_FIRST_RUN.md`
- `docs/BEGINNER_PIPELINES_DEMO.md`
- `docs/ARCHITECTURE.md`
