Date: 2026-05-30
Status: Accepted

# ADR-0097: Experiment Data/Query-Plane Hardening (DozerDB online-wait, Opik version guard, ontology-aware financial lookup)

## Context

The FinDER vector-vs-graph experiment (CLAUDE.md §19) surfaced three correctness
defects that were hot-fixed live and initially lived only in the benchmark
scripts. They affect every SDK user of the data/query plane, not just the
experiment, so per CLAUDE.md §3 (control vs data plane) the fixes belong in the
SDK:

1. **DozerDB `CREATE DATABASE` is asynchronous.** `Neo4jGraphStore.ensure_database`
   returned immediately, so an immediate write hit
   `Neo.ClientError.Database.DatabaseNotFound` ("Graph not found"). Multiple
   ontology arms silently landed 0 nodes.
2. **Opik SDK/server version skew.** With Opik SDK 1.11 against a 2.x self-hosted
   server, `OpikBackend.log_span` called `client.trace(...)` then `trace.end()`
   immediately; the batched message manager raced and the create payload
   (name/tags/metadata) was dropped — traces landed all-null. The
   version mismatch was silent.
3. **Brittle hardcoded financial retrieval.** `cypher_builder._financial_metric_lookup`
   hardcoded `(:Company)-[]-(:FinancialMetric {year})`. FIBO-governed extraction
   produces `:LegalEntity` reporting `:Revenue`/`:NetIncome` subclasses with a
   `period` property, so structured retrieval returned empty even when the data
   was present. Question-derived `metric_scope_tokens` (often stopwords) were an
   `ALL(...)` hard filter that eliminated every metric row.

## Decision

1. `Neo4jGraphStore.ensure_database(name, *, wait_online=True, timeout=30.0)` polls
   `SHOW DATABASES YIELD name, currentStatus` until the database is ONLINE before
   returning. The poll delay goes through a module-level `_sleep` indirection so
   tests run instantly. The benchmark `_ensure_db_ready` becomes a thin caller.
2. `OpikBackend.log_span` passes `end_time` in the single `client.trace(...)` call
   and never calls `trace.end()` after creation (avoids the batching race). A
   one-time `_warn_opik_version_once` logs a clear warning when the installed Opik
   SDK major version is older than current servers.
3. `cypher_builder` derives metric labels from ontology nodes carrying a `value`
   property and anchor labels from the sources of metric-bearing relationships
   (`_metric_anchor_labels`), passed as **parameters** (no label interpolation —
   §8 safe, read-only). `metric_aliases`/`metric_scope_tokens` are demoted from
   `WHERE` filters to a soft `ORDER BY` ranking. The anchor also matches by
   `ticker` so ticker-only questions ("UR", "JKHY") resolve to full-name nodes;
   the extraction prompt stores `ticker` on company nodes.

## Consequences

- SDK users get correct DozerDB database provisioning and non-null Opik traces by
  default; `ensure_database` keeps a back-compatible default (callers that relied
  on immediate return now wait, which is the intended correctness behavior).
- Financial graph retrieval works against ontology-governed graphs (any value-
  bearing metric label) instead of a fixed `Company/FinancialMetric` schema.
- All three fixes are locked by no-service regression tests wired into
  `scripts/ci/run_basic_ci.sh` (test_graph_ensure_database, test_tracing_opik_regression,
  test_cypher_builder_ontology_aware).

## Follow-up (Proposed, not built)

- **Insufficiency-gated retrieval fall-back ladder** (structured graph →
  graph-as-context → vector → hybrid) reusing `query/insufficiency.py` and
  `answering.synthesize(vector_context=...)`. Pre-condition: a deterministic,
  max-iteration-guarded `iterate()` seam (QA requirement).
- **Fact-vs-reasoning query classifier** as an `agent/enrichment_router.py` stage
  mapping to `agent_config` (`reasoning_mode`/`repair_budget`); extends
  `strategy_chooser.IntentSupportValidator`.
- **Graph-as-context serialization contract** + a distinct `graph_chunks` retrieval
  mode (never contaminate the pure structure-only `graph` lane).
- Extraction-recall probe (question-aware re-extraction; the graph lane is bounded
  by extraction completeness, not retrieval).
