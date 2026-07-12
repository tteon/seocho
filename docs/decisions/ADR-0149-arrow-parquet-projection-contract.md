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

The initial live DozerDB 5.26.3.0 probe on 2026-07-12 returned no Arrow or
Parquet APOC procedures. After installing version-pinned APOC Extended 5.26.4
and `apoc-hadoop-dependencies-5.26.4-all.jar`, the same image registered all
Arrow/Parquet load, import, and export procedures. A live SEOCHO artifact was
then read through both `apoc.load.arrow` and `apoc.load.parquet`; workspace,
sequence, and payload hash matched.

The probe also established that Arrow stream and file framing are distinct:
`apoc.load.arrow(file)` requires Arrow file magic, while
`apoc.load.arrow.stream` consumes a stream payload. SEOCHO exposes separate
encoders so these paths cannot be confused.

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

The default local Compose stack runs a one-shot, idempotent installer before
Neo4j and stores the pinned JARs in the existing repo-local plugin volume. This
is a development/bootstrap mechanism. Production images must bake in verified
JAR checksums instead of downloading plugins during startup.

## Consequences

Arrow/Parquet becomes a stable boundary rather than a claim that every graph
engine supports zero-copy ingestion. APOC acceleration remains optional and
observable. Projection acknowledgement occurs only after graph write success;
creating an artifact never advances the watermark.
