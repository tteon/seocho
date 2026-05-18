# ADR-0090: Tiered NL→Cypher Strategy for QueryAgent

Date: 2026-05-19
Status: Proposed

## Context

After indexing, end users ask questions in natural language and expect the
SDK to translate them into safe Cypher against the workspace's DozerDB
database. `QueryAgent` (`seocho/agent/factory.py:150-177`) already wires
two relevant tools:

- `text2cypher` (`seocho/tools.py:245-300`) — a deterministic intent-based
  Cypher builder driven by `seocho.query.cypher_builder`. It covers a fixed
  set of intents (entity lookup, relationship lookup, neighbors, path,
  count, list-all).
- `execute_cypher` (`seocho/tools.py:303-349`) — read-only execution that
  binds `workspace_id` via closure and runs through `CypherQueryValidator`
  (`seocho/query/cypher_validator.py:9-74`).

Two failure modes remain in this surface:

1. **Schema drift.** `Neo4jGraphStore.get_schema(database, workspace_id)`
   (`seocho/store/graph.py:543-578`) returns the live label/relationship/
   property keys for a workspace, but it is not exposed as a tool. The
   agent cannot ground generation in what actually exists for the active
   workspace, so generated Cypher can target labels or properties that are
   absent.
2. **No learning loop.** Successful (NL, Cypher) pairs are not retrieved
   as few-shot context on subsequent questions. `search_similar`
   (`seocho/tools.py:352-383`) operates over the document corpus, not over
   query history. Novel questions always pay full generation cost and lose
   the benefit of prior validated translations.

`seocho-j965` (GraphAgenticLoop) is the parent orchestrator that will
consume the NL→Cypher contract. This ADR scopes the inner contract — the
agent + tool shape that GraphAgenticLoop calls into — without expanding
into routing, debate, or improvement-loop concerns.

## Decision

Evolve `QueryAgent` in place. Do not introduce a sibling agent class.
The agent surface grows with three new tools, an explicit exposure of
the existing read-only validator, and a tiered system-prompt policy.

New tools added to `seocho/tools.py` (each closing over `workspace_id`,
`graph_store`, and `ontology` in the same pattern as the existing
`make_*_tool` factories):

1. `cypher_template_lookup(intent, params)` — thin wrapper over
   `CypherBuilder` intents. Returns either a parameterized Cypher plan or
   an explicit miss. Cheap, deterministic, ontology-grounded.
2. `similar_query_search(question, k=5)` — k-NN over a per-workspace
   `NLCypherExampleStore` (new table in `seocho/store/vector.py`,
   alongside FAISS/LanceDB impls) keyed by question embedding, returning
   validated past (NL, Cypher) pairs as few-shot context. Used as
   context for generation, never as a direct answer.
3. `schema_introspect(database=None)` — thin wrapper over
   `Neo4jGraphStore.get_schema()` returning
   `{labels, relationship_types, property_keys}` for the active workspace.

Existing tools made explicit in the agent contract:

4. `validate_cypher` — direct exposure of
   `CypherQueryValidator.validate(plan, constraint_slice)` so the agent
   can pre-flight a generated query before execution.
5. `execute_cypher` — unchanged; remains read-only by default.

System prompt rewritten (replaces `seocho/agent/factory.py:35-71`) to
encode the tiered policy:

- **Tier 1** — call `cypher_template_lookup`. On hit, `validate_cypher`
  then `execute_cypher`.
- **Tier 2** — on miss, call `similar_query_search` and
  `schema_introspect`, then generate a schema-grounded query, then
  `validate_cypher` then `execute_cypher`.
- **Tier 3** — on validate or execute error, feed the error text back
  and regenerate once. After two failures, return a refusal with the
  diagnostic, do not retry further.

After a successful Tier 2/3 execution that returns a non-empty result,
write the (question, Cypher, workspace_id) triple to
`NLCypherExampleStore`. Template-tier hits do not write back (they are
already deterministic).

## Consequences

Positive:

- the cheap template path widens over time as the example store fills,
  shifting the cost curve toward Tier 1 hits
- schema introspection eliminates a large class of hallucinated label
  and property names by grounding generation in live workspace state
- read-only validation is centralized; no path bypasses
  `CypherQueryValidator`, preserving CLAUDE.md §8 Cypher safety
- `workspace_id` continues to flow end-to-end through tool closures,
  matching CLAUDE.md §6.1 propagation contract
- the agent shape stays compatible with `extraction/agents_runtime.py`
  and does not require a new `Runner.run` integration

Tradeoffs:

- more tools means more agent decision steps and higher token cost per
  call; mitigated by Tier 1 covering the common path
- `NLCypherExampleStore` introduces a new storage surface (per-workspace
  index, eviction policy, TTL semantics) that needs ownership and a
  bounded growth contract
- the write-back loop must not poison the store with bad pairs; the
  invariant is "only on successful execute with non-empty result"
- cross-workspace example sharing is deliberately forbidden by default;
  isolation matches the workspace_id contract but limits global learning

Open questions (deferred to a follow-up ADR if non-trivial):

- eviction policy for `NLCypherExampleStore` (LRU vs by-success-count)
- whether successful Tier 1 template hits should also be recorded for
  embedding retrieval, or kept template-only
- workspace-scoped vs ontology-scoped key shape for the example store

## Implementation Notes

- touch points:
  - `seocho/agent/factory.py` — system prompt rewrite, tool wiring
  - `seocho/tools.py` — three new `make_*_tool` factories plus explicit
    `validate_cypher` exposure
  - `seocho/store/vector.py` — `NLCypherExampleStore` alongside FAISS/
    LanceDB
  - `seocho/store/graph.py` — no change; `get_schema()` already exists
  - `seocho/query/cypher_validator.py` — no change
- safety skills to invoke during implementation: `refactor-safety` (the
  factory.py system prompt + multiple tools touch is multi-file),
  `workspace-id-audit` (new tools must close over and propagate
  `workspace_id`), `cypher-safety` (any path that builds or executes
  Cypher), `owlready-boundary` (ontology context use must stay offline)
- aligns with CLAUDE.md §6.1 (workspace-aware contracts), §8 (DozerDB
  safety, `elementId` preference), §15 (route Agents SDK execution
  through `extraction/agents_runtime.py`), §18 (cache-friendly system
  prompt ordering)
- parent tracking issue: `seocho-j965` (GraphAgenticLoop). This work is
  scoped as a subtask so the inner NL→Cypher contract ships before the
  outer evaluate-and-improve loop consumes it.
