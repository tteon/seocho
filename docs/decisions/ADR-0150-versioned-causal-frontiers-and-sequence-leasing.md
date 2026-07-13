# ADR-0150: Versioned Causal Frontiers and Sequence Leasing

Date: 2026-07-13
Status: Proposed

## Context

The v1 authoritative memory contract assigns one gapless sequence to every
revision in a workspace. It is simple to audit, but all writers update one
`agent_memory_heads` row. A live 10,000-event, 64-writer uniform run measured
684.51 full commits/s with p95 267.71 ms; `sequence_allocate` alone was p95
261.37 ms. A hot-wallet run exposed a different boundary: aggregate-lock p95
was 117.80 ms while sequence allocation was 0.29 ms, because revisions of one
logical wallet must remain ordered.

Native PostgreSQL sequences and leased ranges remove the per-event head update,
but reservations are not rolled back and unused numbers create gaps. Sharding
also replaces a total order with a partial order. Reusing the v1 scalar causal
token or a single projection watermark would therefore allow false freshness.

## Decision

Keep `strict_workspace` and the scalar `memory.v1` causal token as the default.
Optimize it with a bounded Psycopg pool and a single steady-path
`UPDATE ... RETURNING`; do not advertise it as horizontally scalable.

Add an opt-in `memory.v2` contract with:

- deterministic aggregate-to-shard routing;
- fenced, audited sequence-range leases per `(workspace, domain, shard)`;
- `CausalPosition(domain, shard, sequence)` and a sorted `CausalFrontier`;
- projection watermarks per domain and shard;
- freshness only when every position in the required frontier is satisfied.

V2 schema objects are separate from v1. Reserved-but-unused positions remain
visible in lease audit rows and are never interpreted as committed events.
Sharding does not weaken per-aggregate revision order: one transaction/account
domain remains serialized. Cross-domain answers carry all required frontier
positions.

Use Rust only after the database contract is equal. The Tokio/SQLx probe writes
the same v1 revision, idempotency receipt, outbox event, strict head, advisory
aggregate lock, and transaction boundary as Python. It is evidence for residual
client scheduling/serialization cost, not evidence that Rust removes a database
lock.

## Consequences

The strict path remains migration-compatible and observably faster, while v2
can test distributed allocation without corrupting v1 semantics. Causal tokens
become larger and consumers must compare a map, not one integer. Lease size and
shard count require workload tuning: a 500-allocation run with 64 shards and
128-position leases reserved 7,680 positions and left 7,180 unused. At 10,000
uniform allocations, 16-position leases left 144 unused across 16 shards and
512 across 64 shards.

The allocator-only rate is not an end-to-end memory-write rate. Revision,
idempotency, outbox, WAL, projection, crash recovery, and answer freshness remain
separate acceptance gates before v2 can become a production write policy.

Low-cardinality phase histograms expose `connection_scope`,
`idempotency_lookup`, `aggregate_lock`, `sequence_allocate`, `revision_lookup`,
and `memory_writes` in Prometheus/Grafana. They never label workspace, wallet,
transaction, prompt, or trace identifiers.
