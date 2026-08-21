# ADR-0219: Rust daemon owns approved DozerDB projections

## Status

Accepted (first vertical slice), 2026-08-21.

## Context

SEOCHO's Python control plane owns ontology extraction, approval, provenance,
and query safety. The local graph write path also needs direct OS lifecycle and
high-throughput Bolt ownership without exposing arbitrary Cypher to a proxy.

## Decision

Add `dataplane/seochod`, a local Rust Unix-domain-socket daemon using the Rust
`neo4j` driver. It accepts only typed, approved LPG projections and preserves
`workspace_id` and provenance stamps. `SEOCHO_RUST_PROJECTOR_SOCKET` opts the
existing graph adapter into this path and fails closed. Python continues to own
read queries, DDL, RDF/n10s writes, LLMs, SHACL/Oxigraph governance, and policy.

APOC Extended `parallel2` is limited to read-side work on DozerDB because its
parallel worker transactions cannot perform canonical writes in the tested
deployment.

## Consequences

The first slice adds a local IPC hop and operational process to supervise, but
gives a narrow native boundary that can later gain bounded client-side batching
and backpressure. It does not claim a performance gain until a live parity and
profile comparison is recorded.
