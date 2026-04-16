# ADR-0063: Benchmark Track Split And FinDER Baseline First Slice

## Status

Accepted

## Context

Performance and quality discussions have been qualitative. We need a repeatable
benchmark structure that matches SEOCHO's product shape.

A single blended benchmark would distort results because SEOCHO spans:

- ontology-governed ingestion
- graph construction
- graph retrieval and reasoning
- local SDK and runtime deployment paths

## Decision

We will use two benchmark tracks:

1. `FinDER`
   - ingestion / ontology-governed extraction / finance QA
2. `GraphRAG-Bench`
   - graph retrieval / evidence quality / reasoning

Peer systems for comparison come from the current rotating set of
graph-memory and graph-RAG SDKs in the same category. Specific peer names
and versions are tracked in internal benchmark runs, not published here,
so the list cannot go stale or misrepresent third-party products.

Measurement order:

1. SEOCHO local SDK baseline
2. SEOCHO runtime HTTP baseline
3. peer systems

The first shipped slice is a runnable FinDER baseline harness for SEOCHO.

## Consequences

Positive:

- fairer comparisons
- clearer separation between engine quality and deployment overhead
- repeatable benchmark artifacts for future optimization work

Negative:

- benchmark maintenance becomes a first-class responsibility
- peer adapters may land later than the baseline harness

## Implementation Notes

- benchmark contract is documented in `docs/BENCHMARKS.md`
- FinDER baseline harness lives at `scripts/benchmarks/run_finder_baseline.py`
- benchmark helper logic lives at `seocho/benchmarking.py`
