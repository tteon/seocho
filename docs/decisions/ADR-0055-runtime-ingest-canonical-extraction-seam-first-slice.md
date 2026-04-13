# ADR-0055: Runtime Ingest Canonical Extraction Seam First Slice

- Status: Accepted
- Date: 2026-04-13

## Context

`extraction/runtime_ingest.py` still owned a large part of the runtime ingest
path, and its LLM-based extraction setup was constructing legacy prompt glue
directly through `PromptManager`, `EntityExtractor`, and `EntityLinker`.

That meant the interactive runtime path was still more coupled to historical
extraction modules than the SDK indexing path, even after canonicalization work
had already started under `seocho/index/`.

## Decision

Move the runtime ingest LLM prompt seam to the canonical SEOCHO extraction
engine without attempting a full `runtime_ingest.py` rewrite.

- build the runtime ingest extraction/linking path on
  `seocho/index/extraction_engine.py`
- keep compatibility adapters for the old extractor/linker method names so
  `SemanticPassOrchestrator` and other runtime code can continue to call the
  same interface
- leave the rest of `runtime_ingest.py` orchestration, memory graph shaping,
  rule application, and relatedness logic in place for now

## Consequences

### Positive

- runtime ingest now shares the same prompt rendering and graph payload
  normalization seam as SDK indexing
- prompt-context changes around graph metadata and developer instructions are
  less likely to drift between local SDK and interactive runtime ingestion
- the semantic orchestrator can continue working without a large rewrite

### Negative

- `runtime_ingest.py` is still large and still owns too many orchestration
  responsibilities
- this slice does not yet move rule, memory-graph, or relatedness orchestration
  into canonical `seocho/*` modules

## Follow-Up

- continue shrinking `runtime_ingest.py` into a runtime composition shell
- extract shared memory-graph shaping and rule-annotation seams when the next
  parity slice is ready
