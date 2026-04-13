# ADR-0057: Runtime Ingest Deterministic Helper Seams First Slice

- Status: Accepted
- Date: 2026-04-13

## Context

`extraction/runtime_ingest.py` still owned a large set of deterministic helper
functions for:

- shaping extracted graphs into the runtime memory graph contract
- merging ontology and SHACL candidates
- building runtime vocabulary candidates
- producing rule-profile and relatedness summaries
- resolving semantic artifact policy decisions

These helpers were not transport-specific, but they still lived only in the
runtime module. That made parity harder because SDK/runtime code could share the
LLM extraction seam while still diverging in deterministic post-processing.

## Decision

Move deterministic runtime-ingest helpers into canonical SEOCHO index modules
without rewriting the remaining runtime orchestration.

- add `seocho/index/runtime_memory.py` for memory-graph shaping helpers
- add `seocho/index/runtime_artifacts.py` for ontology/SHACL merge, vocabulary,
  rule-profile, relatedness summary, and semantic-artifact policy helpers
- keep `RuntimeRawIngestor` wrappers stable so current runtime tests and callers
  do not need a public contract change

## Consequences

### Positive

- runtime deterministic shaping logic is now reusable outside
  `extraction/runtime_ingest.py`
- parity work can target narrower shared seams instead of only the full runtime
  module
- `runtime_ingest.py` loses a large block of pure helper logic without changing
  runtime transport behavior

### Negative

- `runtime_ingest.py` still owns orchestration, embedding-relatedness I/O, and
  DB loading flow
- compatibility wrappers remain in place until later cleanup slices remove them

## Follow-Up

- extract relatedness/linking decision helpers onto a canonical seam when the
  embedding path is ready
- continue shrinking `runtime_ingest.py` toward runtime composition only
