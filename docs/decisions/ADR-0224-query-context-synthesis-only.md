# ADR-0224: Preserve retrieval for ask-time query context

Date: 2026-09-13
Status: Proposed

## Context

PR #146 advertises synthesis-only `query_context`, but effective context skips
the semantic fast path and discards a computed deterministic answer. Forwarding
tests alone cannot demonstrate the advertised retrieval parity.

## Decision

Keep the existing retrieval contract. Context does not enter decomposition,
arbitration, planning, execution, fallback selection, or deterministic answer
calculation. For an already computed answer, perform one final reframing call
using that answer and the same rows. For existing LLM synthesis, augment the
existing prompt. Clarifications remain unchanged. Synthesis failures propagate
without re-entering retrieval.

Publish semantic response metadata from the current result, retaining its exact
executed Cypher and parameters. Do not query again to reconstruct metadata.
Refresh evidence and route on both answers and clarifications, and clear old
metadata on accepted requests. Preserve engine answer-source and usage fields
through the public response envelope. Report final-call provider tokens
separately from unavailable request-wide semantic usage.

Normalize effective empty context consistently for local, structured, and
remote surfaces. Reject effective context on structured and remote surfaces
before query I/O until those modes have their own contract.

## Consequences and validation

The original contribution and prompt rendering remain in place. Fast answers
with effective context incur one extra LLM call; final wording is model-generated
and factual preservation remains an instruction, not a deterministic guarantee.

The public `ask()` contract tests exercise real planning, semantic arbitration,
Cypher compilation, deterministic finance answering, and structured execution.
Only provider/DB I/O (and documented structured generator/synthesizer seams) are
doubled. Compare graph calls including Cypher/params/rows and pre-synthesis LLM
prompts. Cover empty context, semantic clarification and fallback, unsupported
mode rejection, and synthesis failures. These tests establish control-flow
parity, not live-service performance or answer quality.

See [SDK contract](../SDK_CONTRACT.md#26-ask-time-query-context).
