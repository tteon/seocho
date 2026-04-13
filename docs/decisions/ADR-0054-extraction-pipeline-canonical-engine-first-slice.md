# ADR-0054: Extraction Pipeline Canonical Engine First Slice

- Status: Accepted
- Date: 2026-04-13

## Context

`seocho/index/pipeline.py` and `extraction/pipeline.py` were both maintaining
their own extraction/linking prompt glue and payload normalization paths.

That duplication made ontology-driven extraction drift likely:

- SDK indexing used canonical SEOCHO prompt strategies and graph-write
  normalization
- extraction-side compatibility pipeline used `PromptManager`,
  `EntityExtractor`, `EntityLinker`, and `OntologyPromptBridge` directly

This did not yet justify a broad `runtime_ingest.py` rewrite, but it did leave
`extraction/pipeline.py` acting like a parallel product path.

## Decision

Introduce a shared canonical extraction engine under `seocho/index/` and make
both indexing paths use it for the extraction/linking seam.

- add `seocho/index/extraction_engine.py` as the shared graph-construction
  engine
- make `seocho/index/pipeline.py` delegate extraction, linking, and payload
  normalization through this shared engine
- make `extraction/pipeline.py` reuse the same canonical engine while keeping
  its existing graph loader, vector store, deduplicator, schema manager, and
  rule-constraint steps
- keep `extraction/runtime_ingest.py` out of scope for this slice

## Consequences

### Positive

- ontology-aware extraction prompts and normalization now come from one shared
  engine in both SDK indexing and extraction compatibility pipeline paths
- `extraction/pipeline.py` sheds direct prompt/extractor/linker glue without
  changing its outer orchestration responsibilities
- future `runtime_ingest.py` canonicalization has a clearer seam to adopt

### Negative

- `extraction/pipeline.py` remains a compatibility pipeline, not a full wrapper
  around `seocho/index/pipeline.py`
- `runtime_ingest.py` is still the largest remaining ingestion-side drift point

## Follow-Up

- continue with `runtime_ingest.py` canonicalization as a separate slice
- keep `extraction/` focused on transport, compatibility, and runtime
  composition rather than growing new business logic
