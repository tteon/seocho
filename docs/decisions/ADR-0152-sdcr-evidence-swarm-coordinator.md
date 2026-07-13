# ADR-0152: SDCR evidence-swarm coordinator

Date: 2026-07-13
Status: Accepted

## Context

SDCR can select the smallest authorized set of graph views for required answer
slots, but routing alone does not execute specialists or produce the typed
evidence bundle required by SEOCHO's Graph-RAG handoff contract. SEOCHO already
has OpenAI Agents SDK construction, capability matchmaking, and typed
agent-exchange references. The missing component is a bounded query-time
coalition executor.

## Decision

Add a provider-neutral evidence-swarm coordinator under `src/seocho/query/`.
It uses SDCR for deterministic coalition selection, runs selected retrieval
specialists concurrently, validates declared slots, filters protected evidence,
exposes conflicts and missing slots, and emits one typed bundle for synthesis.

Specialists implement a narrow protocol, allowing deterministic database tools
and OpenAI Agents SDK-backed adapters to share the handoff. The default mode
assembles evidence rather than debating answers. Runtime metrics remain
low-cardinality; exact request causality stays in traces and receipts.

## Consequences

SEOCHO can prove multi-agent cooperation through slot completion and
provenance, not agent count. Partial failures and timeouts remain visible and
cannot authorize fabricated values. High-concurrency storage/retrieval
qualification remains provider-independent, while bounded MARA samples validate
answer synthesis separately.

The coordinator does not replace `Matchmaker`, `AgentExchange`, OpenAI Agents
SDK handoffs, or Graph-CoT. It is the evidence-assembly seam between routing
and those answer/runtime layers.
