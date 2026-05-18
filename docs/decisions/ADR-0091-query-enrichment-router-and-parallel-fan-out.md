# ADR-0091: QueryEnrichmentRouter Pre-Stage and Parallel Backend Fan-Out

Date: 2026-05-19
Status: Proposed

## Context

`seocho.routing.RoutingPolicy` (`seocho/routing/__init__.py:126`) already
exists as a policy layer. Its `decide()` method takes an `augmentation`
dict (intent, entity, topic, rewrite signals) and returns a
`RoutingDecision` with intent + backend weights. The docstring is
explicit that it returns a decision object describing *which backends to
use* — it does not perform augmentation itself, and it does not execute
the search.

Two consumers exist today:

- `seocho/agent/graph_loop.py:237-239` — `GraphAgenticLoop` calls
  `augmentation = self.augment_fn(question)` then
  `self.policy.decide(augmentation=augmentation, ...)`. The `augment_fn`
  is an injected callback; there is no canonical implementation.
- `seocho/agent_config.py:74` — instantiates `RoutingPolicy` for default
  3-axis trade-offs but routes nothing.

`QueryAgent` (`seocho/agent/factory.py:150`) currently goes straight to
`text2cypher` / `execute_cypher` for every question, with no upstream
enrichment. For under-specified questions ("show me revenue around the
acquisition period" with no company named) this single-backend, single-
intent path returns empty or wrong results.

The gap is the **augmentation actor**: the component that converts a raw
NL question into the `augmentation` dict that `RoutingPolicy.decide()`
expects, then **executes** the resulting decision across the chosen
backends in parallel and fuses ranked results before handing to the
answer-generation step.

This ADR scopes that actor. Pairs with ADR-0090 (tiered NL→Cypher inside
`QueryAgent`) and ADR-0092 (Graph-CoT-oriented LPG property schema that
provides the signals enrichment reads from). Subtask of `seocho-j965`.

## Decision

Introduce `QueryEnrichmentRouter` as a pre-stage in `Session.ask()`
(`seocho/session.py:504`) that runs before `QueryAgent` is invoked, and
as the canonical implementation of the `augment_fn` callback in
`GraphAgenticLoop`. One implementation, two callers.

The router runs four stages:

1. **Augmentation** — produce the `augmentation` dict that
   `RoutingPolicy.decide()` already expects:
   - **entity** — candidate entity resolution via the fulltext-first path
     established by ADR-0010 (`extraction/fulltext_index.py:73`) plus
     alias bundles emitted by ADR-0092 indexing
   - **intent** — light LLM classifier returning
     `{intent: lookup|relationship|path|aggregation|analytics|explanation,
     confidence: float}`; falls through to `intent_fallback` weights when
     confidence is below `RoutingPolicy.thresholds["intent_fallback"]`
   - **topic** — ontology-driven topic tag from
     `seocho/ontology_context.py` plus per-entity topic labels written by
     ADR-0092 (`semanticRole`, `domainScope`)
   - **rewrite** — optional question rewrite (HyDE-style hypothetical
     document) for the vector channel; never replaces the original
     question

2. **Routing decision** — call existing
   `RoutingPolicy.decide(augmentation=..., ...)`. No change to the policy
   contract.

3. **Parallel fan-out** — based on `RoutingDecision.weights`, run the
   selected backends concurrently via `asyncio.gather`:
   - **Cypher** — hand to `QueryAgent` (ADR-0090 tiered NL→Cypher)
   - **Vector** — `seocho/store/vector.py` similarity search over
     `embeddingText` populated by ADR-0092
   - **Fulltext** — alias-aware fulltext via `extraction/fulltext_index.py`
   - **GDS analytics** — `seocho.gds` session for centrality/community
     queries when intent is `analytics`
   Backends with weight below `0.10` are skipped. Each backend has a
   per-call timeout sourced from `RoutingPolicy.thresholds`.

4. **Fusion** — combine the ranked lists with **Reciprocal Rank Fusion**:
   `score(doc) = Σ_b weight_b / (k + rank_b(doc))` with `k = 60` as the
   standard RRF constant. Backend weights from `RoutingDecision.weights`
   scale each list's contribution. Output is a single ranked list of
   `EvidenceItem` objects passed to answer synthesis.

**Short-circuit:** when intent confidence ≥ `intent_high` (default 0.80)
**and** intent ∈ {`lookup`, `relationship`, `path`} **and** a single
high-confidence entity match exists, skip fan-out and go straight to
`QueryAgent`'s Tier-1 template path. The router records the short-circuit
reason in the trace so the cost saving is auditable.

**Tracing:** every router pass emits a JSONL/Opik trace span with
`workspace_id`, the `augmentation` payload, the `RoutingDecision`, the
set of backends actually run, per-backend latency, the fusion output's
top-K, and the short-circuit reason if any. Aligns with CLAUDE.md §9.

## Consequences

Positive:

- one canonical augmentation implementation; `Session.ask()` callers and
  `GraphAgenticLoop` both benefit without duplication
- `RoutingPolicy.decide()` becomes a real load-bearing call instead of a
  policy waiting for an actor
- under-specified questions get multi-backend recall while specific
  questions short-circuit to the cheapest path
- RRF is deterministic and adds no LLM cost beyond the augmentation step
- enrichment trace makes routing decisions auditable end-to-end

Tradeoffs:

- augmentation adds an LLM classifier hop on every `Session.ask()`; the
  intent classifier must stay small (Haiku-class) and cache by
  `(workspace_id, normalized_question)` to keep p50 latency acceptable
- parallel fan-out raises peak token spend per question; mitigated by
  the weight ≥ 0.10 cutoff, per-backend timeouts, and the short-circuit
- RRF ignores absolute relevance scores; works well as a baseline but
  may underweight one backend's strong-signal hit — fusion is therefore
  pluggable behind a `Fusion` interface so LLM-rerank or weighted-merge
  can replace RRF without touching callers
- the router depends on ADR-0092 signals (alias bundles, embeddingText,
  semanticRole) to do meaningful augmentation; ADR-0092 must land first
  or the router degrades to intent-only routing

Open questions (deferred):

- whether the intent classifier should be a small fine-tuned model or a
  prompt over the default backend (start: prompt; measure; revisit)
- per-workspace tuning of `RoutingPolicy` thresholds vs global defaults
- whether the short-circuit should also apply when intent is `analytics`
  with a single high-confidence GDS metric match

## Implementation Notes

- touch points:
  - new module: `seocho/agent/enrichment_router.py` — `QueryEnrichmentRouter`
    class, `augment()`, `route()`, `fan_out()`, `fuse()` methods
  - `seocho/session.py:504` — call router before `QueryAgent`; pass
    fused evidence into the agent context
  - `seocho/agent/graph_loop.py:237` — replace the injected `augment_fn`
    default with `QueryEnrichmentRouter.augment`; keep the callback hook
    for tests
  - `seocho/routing/__init__.py` — no change; the policy contract is
    already correct
  - `extraction/fulltext_index.py` — no change; consumed read-side
  - new module: `seocho/agent/fusion.py` — `Fusion` interface +
    `ReciprocalRankFusion` default impl
- safety skills to invoke: `refactor-safety` (multi-module change),
  `workspace-id-audit` (router must propagate `workspace_id` into every
  backend call), `owlready-boundary` (topic enrichment uses the
  pre-computed `OntologyRunContext`, never invokes owlready2 at request
  time)
- aligns with CLAUDE.md §6.1 (workspace-aware contracts), §6.3
  (Owlready2 stays offline), §9 (vendor-neutral tracing), §15 (route
  via `extraction/agents_runtime.py`), §18 (cache-friendly augmentation
  cache key)
- depends on: ADR-0092 (LPG property schema) for the signals
  enrichment reads
- composes with: ADR-0090 (tiered NL→Cypher inside `QueryAgent`)
- parent tracking: `seocho-j965` (GraphAgenticLoop), as a sibling
  subtask to ADR-0090's task. The router ships the canonical
  `augment_fn` that j965's loop already calls.
