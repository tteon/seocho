# ADR-0218: The agentic-RAG bottleneck is LLM round-trips — and the agentic layer is the removable slice

- Status: experimental
- Date: 2026-08-17
- Tickets: seocho-5ny (breakdown epic)
- Related: ADR-0144 (rag.ask span tree), ADR-0214 (answer-quality axis),
  ADR-0216/0217 (Agents SDK coupling / orchestration), #606/#607/#609 (controlled path)

## Context

Goal: identify **where the agentic-RAG bottleneck is**. Ran an EnterpriseRAG-Bench
question e2e through the OpenAI Agents SDK on MARA MiniMax-M2.7 + live DozerDB
5.26.3, with SEOCHO's OTLP tracing → otel-collector → Tempo. Two arms, same
question, same indexed graph, 3 runs each:

- **agentic** — `create_supervisor_agent` → SDK hand-off → controlled QueryAgent → tool
- **direct** — `create_controlled_query_agent` via Runner (no supervisor)

Durations read back from the Tempo trace API per stage
(`rag.compile_cypher`, `rag.synthesize`, `gen_ai.chat`, `db.query`).

Chart (shareable): https://claude.ai/code/artifact/6d49ed3c-7ff9-40ed-8ee9-19dcc0016d89

## Findings (real traces)

1. **The graph database is NOT the bottleneck: ~0.1% of latency.** `db.query` +
   traversal totalled ~20–40 ms per query in both arms. GraphRAG's graph tier is
   effectively free.
2. **LLM round-trips are the entire cost.** Per-call `gen_ai.chat` averaged ~11 s
   on MiniMax-M2.7, with large variance.
3. **The agentic layer adds removable orchestration turns.** The direct arm made
   **0** orchestration LLM calls; the agentic arm made **≥1** per query (routing /
   hand-off / relay) — pure overhead the direct path does not incur.
4. **The worst tail lives in the agentic layer:** one supervisor orchestration
   turn took **177 s** (the reasoning model spun), vs typical ~17 s. RAG stages
   (compile ~8–17 s, synth ~4–7 s) never showed that tail.

Per-arm gen_ai.chat calls captured (seconds):

| stage | agentic | direct |
|---|---|---|
| compile_cypher | 10.0, 9.4, 7.6 | 17.6, 15.9, 5.2 |
| synthesize | 7.1, 4.1, 3.6 | 6.0, 4.9, 4.9 |
| orchestration (agent-loop) | 177.3, 16.8 | *(none)* |
| graph ops (per run) | ~0.042 s | ~0.020 s |

## Decision / implications

- **The bottleneck is LLM round-trips, and the single most *removable* slice is the
  agentic orchestration itself.** For single-intent queries, prefer the direct
  controlled query agent (#607) over the supervisor hand-off; it eliminates the
  orchestration turns and their tail with no loss (both routed to the same
  QueryAgent). Where a manager agent is needed, prefer `Agent.as_tool` (control
  returns, no relay turn) over a handoff transfer.
- **Model latency dominates the rest.** MiniMax-M2.7 at ~11 s/call sets the floor;
  a faster model or the self-hosted vLLM target (reference: vLLM verified end to
  end) attacks every remaining slice.
- **Do not optimize the graph.** It is 0.1% of latency; effort there is wasted.

## Caveats (binding)

n=3 per arm with large per-call API-latency variance — magnitudes are
order-of-magnitude, not precise. The 177 s point is one real outlier, kept
visible. Answer *quality* is a separate axis (the generic single-Entity ontology
did not surface this ERB fact — ADR-0214); latency takes the same path regardless
of correctness, so the bottleneck attribution holds independent of the answer.

## Consequences / follow-ups (seocho-5ny)

- Measure `Agent.as_tool` vs handoff latency directly (quantify the relay-turn saving).
- Re-run the arms on the self-hosted vLLM / a faster model to confirm the floor shifts.
- Broaden to more ERB questions for tighter distributions.
- Data: `ADR-0218-agentic-rag-bottleneck.json`.
