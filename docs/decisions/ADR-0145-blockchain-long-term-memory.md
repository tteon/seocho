# ADR-0145: Blockchain Long-Term Memory and Rebuildable Graph Projections

Date: 2026-07-11
Status: Proposed

## Context

The OKX-style risk preflight needs more than a growing property graph. A chain
can reorganize, the same event can be delivered repeatedly, and risk policy can
change after an earlier decision. In-place graph mutation cannot by itself show
which block and memory revision supported a historical answer.

FoundationDB is a candidate authoritative store because its ordered key space
and strict serializable transactions fit per-workspace event histories,
idempotency receipts, aggregates, and an outbox. Its native client is an
operational dependency, however, and cannot become mandatory for the SEOCHO
SDK or no-service CI.

## Decision

Store blockchain observations as append-only `TransactionEvent` revisions.
The stable identity is workspace, chain, transaction hash, and event index.
Each revision records block height/hash, canonical or orphaned status,
provenance, and opaque customer/counterparty references. Reconciliation of one
block is atomic: repeated delivery is a no-op; replacement of a block appends
orphan revisions; new canonical revisions, repeated-risk aggregates, and graph
projection outbox entries commit together.

Treat DozerDB as a rebuildable hot serving projection, not the authoritative
ledger memory. A projector consumes the transactional outbox and acknowledges
a monotonic causal watermark. Risk preflight can require a watermark at least
as new as its `CausalToken`; a stale projection fails to review instead of
silently using old graph state.

Use a small transaction-runner protocol. The deterministic in-memory runner
provides atomic rollback and the same tuple-key contract for unit tests. The
optional FoundationDB runner lazily imports the official Python binding, uses
the tuple layer for ordered namespaces, and executes the same memory function
through FoundationDB's retrying transaction decorator. No FoundationDB package
or server is required for default SEOCHO imports.

Bound each memory transaction by an explicit event count and by a 90 KiB value
limit. The first reference default is 128 events per observation. Oversized
observations fail before opening a transaction and must be partitioned by the
future ingestion coordinator. This reflects FoundationDB's 10 MB hard affected-
data limit, its guidance to redesign transactions above 1 MB, and its five-
second transaction lifetime. The reference API does not claim that an
arbitrarily large Bitcoin block can be reconciled in one transaction.

etcd remains coordination-only. It may publish the latest projection watermark
or shard lease, but transaction bodies, customer bindings, risk aggregates,
and outbox payloads remain in authoritative memory.

## Consequences

Reorg and replay behavior becomes auditable and deterministic. Graph state can
be rebuilt from canonical revisions, and an answer can name the exact memory
sequence it observed. The first implementation intentionally optimizes for a
correct contract rather than bulk archival analytics; cold raw blocks and
large payloads should later live in object storage with content-addressed
references.

The no-service test runner proves domain semantics but not FoundationDB client,
cluster, tenant, retry, or operational performance. A live opt-in integration
test and load profile remain required before calling this production-ready.
The current per-workspace integer sequence is also a deliberate correctness
baseline; a live scale milestone must compare it with FoundationDB commit
versionstamps or partitioned sequence allocation to remove a hot-key bottleneck.

## Validation

- Replaying the same block does not create a revision, aggregate increment, or
  outbox entry.
- Replacing a block appends orphan revisions and compensating projection work
  without deleting history.
- Repeated-risk aggregates count only currently canonical flagged events.
- Projection acknowledgement is monotonic and rejects a token from another
  workspace.
- Importing SEOCHO and running focused tests does not require FoundationDB.
- A bounded public-data run can read the current OFAC XBT labels and confirmed
  Blockstream Esplora transactions while keeping raw addresses out of its
  report.
