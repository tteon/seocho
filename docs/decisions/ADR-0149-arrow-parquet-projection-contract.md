# ADR-0149: Arrow and Parquet Projection Contract

Date: 2026-07-12
Status: Proposed

## Context

The authoritative agent-memory store is PostgreSQL and the graph is a replayable
projection. The current projector materializes Python dictionaries and sends
lists through Bolt `UNWIND`. This is portable but adds serialization and object
allocation on every replay.

APOC Extended provides `apoc.load.arrow`, `apoc.load.arrow.stream`,
`apoc.import.arrow`, `apoc.load.parquet`, and `apoc.import.parquet`. Arrow can
therefore remove the Python row conversion when the deployed graph engine has
the compatible procedures. Parquet also provides a compact immutable replay
artifact. These procedures are not APOC Core features. Parquet additionally
requires the version-matched Hadoop dependency JAR.

The live DozerDB 5.26.3.0 probe on 2026-07-12 returned no Arrow or Parquet APOC
procedures. Installing Neo4j APOC Extended into a DozerDB image is not assumed
safe without an isolated compatibility test.

## Decision

Define `seocho.projection.arrow.v1` as the language-neutral outbox batch
contract. It has deterministic workspace/sequence ordering, canonical payload
JSON, per-payload SHA-256, and schema metadata. Arrow IPC is the streaming
transport. Zstandard Parquet is the durable rollback, replay, and audit
artifact with a content-addressed receipt.

Use a capability-gated graph sink:

1. `apoc.load.arrow.stream` plus explicit workspace-scoped Cypher when the
   procedure is present and its compatibility suite passes;
2. `apoc.load.parquet` for bulk recovery when Extended and the matching Hadoop
   dependency are installed;
3. the existing typed Bolt `UNWIND` sink as the portable fallback.

Do not use `apoc.import.arrow/parquet` blindly for the memory projection. Those
procedures are optimized for APOC graph-export layouts, while SEOCHO must retain
its own identity, workspace, fencing, idempotency, and watermark semantics.

## Consequences

Arrow/Parquet becomes a stable boundary rather than a claim that every graph
engine supports zero-copy ingestion. APOC acceleration remains optional and
observable. Projection acknowledgement occurs only after graph write success;
creating an artifact never advances the watermark.

