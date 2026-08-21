# Rust-owned approved DozerDB projection

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds. This plan follows `.PLANS.md`.

## Purpose / Big Picture

SEOCHO accepts unstructured documents in Python, extracts and validates an ontology-shaped candidate, and only then projects approved graph facts. This plan moves the local operating-system socket and DozerDB Bolt write boundary into `seochod`, a Rust daemon. A user can start `seochod`, set `SEOCHO_RUST_PROJECTOR_SOCKET`, and run the normal `seocho run` CLI without allowing arbitrary Cypher or bypassing SEOCHO guardrails.

## Progress

- [x] 2026-08-21: Added a typed UDS protocol and Rust `neo4j` Bolt projection daemon.
- [x] 2026-08-21: Added the opt-in, fail-closed Python SDK adapter and Rust unit tests.
- [x] 2026-08-21: Connected the daemon to the live DozerDB instance and verified an approved node write through the Rust driver.
- [ ] Force a full document re-index through the daemon and compare receipt/latency with the Python writer.
- [ ] Move read-only query execution only after the read safety and evidence-bundle contract have parity evidence.

## Surprises & Discoveries

`apoc.cypher.parallel2` is registered in the current DozerDB setup and works for read-side parallel work through the Rust driver. Its worker transactions reject writes with an authorization violation, so it is not a canonical projection mechanism.

## Decision Log

The `professor_agent` lens keeps ontology selection, candidate approval, provenance, and evidence semantics in Python because they are SEOCHO's product contract. The `software_engineer_agent` lens chooses a typed `project` operation rather than an arbitrary-Cypher proxy, preserving label validation, workspace scope, and a small testable interface. The `computer_systems_agent` lens chooses a Unix-domain socket plus Rust Bolt driver to remove Python from the hot OS/Bolt write boundary while preserving observability at the control-plane boundary. The decision would change if a parity run shows altered provenance, workspace isolation, or write counters.

## Outcomes & Retrospective

The first vertical slice is complete: the Rust process owns UDS and Bolt writes, with an explicit opt-in environment variable. It is not yet the universal data plane: read queries, RDF writes, schema DDL, and ontology governance stay in Python/Oxigraph paths until separately validated.

## Context and Orientation

`src/seocho/index/` creates approved node and relationship dictionaries. `src/seocho/store/graph.py` is the canonical graph adapter. `dataplane/seochod/` is a local daemon, meaning a separate process communicating through a filesystem Unix socket. DozerDB is the canonical property-graph backend. An ontology signal, required slot, relation path, provenance, insufficiency, and typed evidence bundle remain Python control-plane artifacts as defined in `.PLANS.md`.

## SEOCHO Evidence Contract

The daemon receives only the approved node/relationship projection after validation. It preserves `workspace_id`, `_source_id`, `_writer_ts`, and `_writer_agent`; it does not assemble evidence bundles or synthesize answers. Query evidence remains read-safe and retains provenance in the existing Python path.

## SEOCHO Review Panel

The three review lenses and their decision are recorded above. The key tradeoff is an extra local IPC hop for explicit OS ownership and a narrower failure domain. The falsification condition is an E2E mismatch in graph facts, receipts, or tenant scope.

## Cost, Latency, and Provider Policy

The daemon adds no model calls. MARA `MiniMax-M2.7` remains the configured extractor/answer model in E2E. JSONL/OTel traces must distinguish LLM extraction/query latency from local projection latency; no throughput claim is made without a live DozerDB workload.

## Plan of Work

Build typed projection first, make it opt-in and fail closed, validate against DozerDB, then measure before moving broader paths. Do not use APOC parallel writers on DozerDB. A future batching implementation must use bounded client-side Rust concurrency with idempotent MERGE writes.

## Concrete Steps

From the repository root, set only local process credentials and run:

    SEOCHOD_BOLT_PASSWORD=... cargo run --offline --manifest-path dataplane/seochod/Cargo.toml -- /tmp/seochod.sock
    SEOCHO_RUST_PROJECTOR_SOCKET=/tmp/seochod.sock uv run seocho run examples/run/quickstart.yaml --force

## Validation and Acceptance

Run:

    cargo test --offline --manifest-path dataplane/seochod/Cargo.toml
    uv run pytest -q tests/seocho/test_seochod_projection.py
    SEOCHO_RUST_PROJECTOR_SOCKET=/tmp/seochod.sock uv run seocho run examples/run/quickstart.yaml --force --output-json

Acceptance is a successful CLI run whose indexing receipt has a Rust-driver projection receipt, and whose generated graph remains workspace scoped. Compare a JSONL trace before declaring performance improvement.

## Idempotence and Recovery

The daemon safely removes only its named socket before binding. Projections use scoped `MERGE` and writer timestamps, so rerunning is safe. If the daemon is unavailable, configured calls fail closed; unset `SEOCHO_RUST_PROJECTOR_SOCKET` to return to the established Python adapter. Do not delete graph data as recovery.

## Artifacts and Notes

The local E2E socket and trace files live under `/tmp` and are not repository artifacts. The daemon reads its secrets from environment only; no secret-bearing configuration file is committed.

## Interfaces and Dependencies

`seochod` reads newline-delimited JSON over an AF_UNIX socket. Its `project` request has `database`, `workspace_id`, `source_id`, `nodes`, and `relationships`. It returns created counters, errors, and `driver: rust-neo4j`. Dependencies are `neo4j` Rust crate, serde, and serde_json. Python uses `src/seocho/dataplane/seochod.py`.
