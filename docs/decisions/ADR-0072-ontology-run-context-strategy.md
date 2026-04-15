# ADR-0072: OntologyRunContext Strategy

Date: 2026-04-15
Status: Accepted

## Context

SEOCHO's differentiator is not just that it stores ontology metadata. The
strategic value is that ontology context can align indexing, querying, agent
tool use, debate, session carryover, and graph database access.

Recent slices exposed `ontology_context_mismatch` across local and runtime
query paths. That is necessary but not sufficient. Without a canonical run
context, each agent mode can still interpret graph scope, ontology profile,
tool policy, and evidence health differently.

## Decision

Define `OntologyRunContext` as the target middleware contract for SDK and
runtime agent paths.

The contract should carry workspace, user/session, graph IDs, resolved
databases, ontology ID/profile, vocabulary profile, compact context hashes,
reasoning settings, tool scope, policy decision, mismatch metadata, and
evidence state.

The first implementation should stay in Python and remain lightweight. It
should not introduce Rust, Arrow, GraphAr, DataBook, vineyard, or request-time
Owlready2 reasoning.

## Consequences

- Single-turn and multi-turn agent paths get the same ontology/database scope
  language.
- Tool middleware can enforce database scope and policy before Cypher execution.
- Debate can compare graph-agent answers with ontology/profile provenance.
- Reasoning and repair paths can use ontology intent slots and missing-slot
  status rather than treating ontology metadata as passive trace decoration.
- The public SDK can keep a simple user interface while carrying deeper
  alignment metadata beside responses.

## Implementation Order

1. Add a canonical context model under `seocho/ontology_run_context.py`.
2. Wire graph tools and runtime `ServerContext` to receive and enforce it.
3. Persist a compact context summary across SDK sessions and platform chat
   turns.
4. Add debate per-agent context summaries.
5. Extend evidence-bundle and benchmark work only after context failures are
   inspectable.

## Related Documents

- `docs/ONTOLOGY_RUN_CONTEXT_STRATEGY.md`
- `docs/GRAPH_RAG_AGENT_HANDOFF_SPEC.md`
- `docs/decisions/ADR-0068-ontology-context-cache-and-agent-middleware-seam.md`
- `docs/decisions/ADR-0069-ontology-context-graph-write-and-query-guardrail.md`
- `docs/decisions/ADR-0070-runtime-ontology-context-response-contract.md`
- `docs/decisions/ADR-0071-runtime-agent-ontology-middleware-contract.md`
