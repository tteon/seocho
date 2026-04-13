# ADR-0049: Canonical Agent Engine First Slice

## Status

Accepted

## Context

SEOCHO agent behavior exists across multiple entry points:

- `seocho/agents.py`
- `seocho/session.py`
- `seocho/client.py`

This makes the local agent runtime harder to treat as a first-class engine than
indexing or the emerging canonical query surface.

## Decision

Introduce a canonical agent package under `seocho/agent/`:

- `seocho/agent/contracts.py`
- `seocho/agent/context.py`
- `seocho/agent/factory.py`

The first slice moves agent/session contracts and agent factory logic behind
this canonical package while preserving the existing public imports:

- `seocho.agents` remains a compatibility shim
- `seocho.session` imports canonical session context and agent factories

## Consequences

Positive:

- local agent/session logic has a clearer canonical home
- `seocho/agents.py` becomes compatibility surface instead of implementation home
- session execution-mode normalization is centralized

Tradeoffs:

- server-side `extraction/agent_server.py` is not yet migrated to the canonical
  agent package
- this slice improves local structure first; cross-runtime parity remains
  follow-up work

## Follow-up

- continue with ontology subdomain split
- split `seocho/client.py` façade responsibilities
- split `extraction/agent_server.py` transport from runtime services
