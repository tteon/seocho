# ADR-0063: Benchmark Track Split And Private Finance Corpus Contract

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

1. `private finance corpus`
   - ingestion / ontology-governed extraction / finance-domain QA
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

The first shipped slice is a runnable finance-domain benchmark harness for SEOCHO.

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
- finance-domain benchmark harness lives at `scripts/benchmarks/run_finance_benchmark.py`
- benchmark helper logic lives at `seocho/benchmarking.py`
- the bundled tutorial sample is onboarding-only and must not be cited as benchmark evidence
