# ADR-0049: Pipeline Unification — Canonical Module Locations

**Date:** 2026-04-13
**Status:** Accepted
**Authors:** hadry

## Context

`seocho.add()` had two completely separate code paths depending on
whether the client was in local mode (`_local_mode=True`) or HTTP mode.
The local path (`seocho/index/pipeline.py` → `_LocalEngine`) lacked
rule inference, embedding-based linking, and semantic artifact support
that the server path (`extraction/runtime_ingest.py`) provided.

This meant identical inputs could produce different graph outputs
depending on which mode was used — an unacceptable parity gap.

## Decision

**Core logic moves into `seocho/` (the pip-installable SDK package).**
`extraction/` becomes a thin HTTP transport layer that imports from
`seocho/`.

### Canonical module locations

| Concern | Canonical Location | extraction/ status |
|---------|-------------------|-------------------|
| Rule inference/validation | `seocho/rules.py` | `extraction/rule_constraints.py` — re-export shim |
| Embedding-based linking | `seocho/index/linker.py` | `extraction/runtime_ingest.py` imports from SDK |
| Vector store abstraction | `seocho/store/vector.py` | `extraction/vector_store.py` — adapter shim |
| Indexing pipeline | `seocho/index/pipeline.py` | `extraction/runtime_ingest.py` (convergence ongoing) |
| Cosine similarity | `seocho/index/linker.py` (+ seocho-core Rust) | removed duplicate |

### Parity harness

`tests/test_parity_harness.py` runs the same ontology + text through
both paths and compares result contracts (nodes, relationships, rules,
embeddings).  This test is the regression guard for all future
refactoring.

## Consequences

- Local mode now produces `rule_profile` and `relatedness_summary` in
  `Memory.metadata`, matching the server contract.
- `extraction/` modules that are shims must not add new logic — all
  changes go to the canonical `seocho/` modules.
- Future features (semantic artifacts in local mode, fallback tracking)
  should be implemented in `seocho/` first, then exposed via HTTP in
  `extraction/`.

## Remaining Gaps (tracked as xfail in parity harness)

- `semantic_artifacts` — server-only draft/approved lifecycle
- `fallback_tracking` — server-only LLM failure fallback
