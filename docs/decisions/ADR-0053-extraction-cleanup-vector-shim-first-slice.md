# ADR-0053: Extraction Cleanup Vector Shim First Slice

- Status: Accepted
- Date: 2026-04-13

## Context

The canonicalization program makes `seocho/` the primary engine layer and keeps
`extraction/` as transport, provisioning, and compatibility code. One obvious
duplication point remained in `extraction/vector_store.py`, which still owned a
legacy OpenAI-coupled vector implementation.

## Decision

Replace `extraction/vector_store.py` with a compatibility adapter that keeps the
legacy extraction API while delegating to canonical SEOCHO vector primitives.

Current classification:

- shim now:
  - `extraction/rule_constraints.py`
  - `extraction/vector_store.py`
- keep as transport/composition:
  - `extraction/agent_server.py`
  - `extraction/public_memory_api.py`
  - `extraction/server_runtime.py`
- migrate later:
  - `extraction/runtime_ingest.py`
  - `extraction/pipeline.py`

## Consequences

### Positive

- extraction-side vector behavior now depends on canonical embedding/provider
  contracts
- duplicated OpenAI-specific code is removed from the extraction layer
- the cleanup lane now has an explicit migrate/shim/keep classification

### Negative

- `extraction/vector_store.py` still keeps compatibility-only methods such as
  `save_index()` and `load_index()`
- `runtime_ingest.py` and `pipeline.py` still carry larger canonicalization work

## Follow-up

- move more extraction ingestion paths to canonical `seocho.index/*` engines
- reduce remaining extraction-only runtime logic to transport or legacy adapters
