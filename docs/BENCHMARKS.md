# Benchmarks

SEOCHO should be measured with two benchmark tracks, not one blended score.

## Benchmark Tracks

### Track 1: FinDER

Use FinDER for:

- ontology-governed extraction quality
- graph write quality
- finance-domain question answering
- local SDK vs runtime overhead on the same workload

Current first-slice baseline command:

```bash
python scripts/benchmarks/run_finder_baseline.py --mode local
```

Supported modes:

- `local`
- `remote`
- `both`

Default dataset:

- `examples/datasets/finder_sample.json`

Optional dataset:

- gated Hugging Face FinDER split

### Track 2: GraphRAG-Bench

Use GraphRAG-Bench for:

- retrieval quality
- evidence quality
- reasoning quality
- query latency

This is the query/reasoning benchmark track. It should not be used as the only
measure of ingestion quality.

## Peer Systems

The current peer baseline set is:

- Graphiti
- Cognee
- mem0 graph memory

These are useful peer systems, but they are not identical products. Results
must be reported by track and by capability, not as a single overall winner.

## Measurement Order

Run benchmarks in this order:

1. `SEOCHO local SDK baseline`
2. `SEOCHO runtime HTTP baseline`
3. peer system baselines

Why:

- the SDK path is the canonical engine baseline
- the runtime path measures deployment overhead and policy/transport cost
- peer comparisons are not meaningful if the internal baseline is unstable

## FinDER Metrics

Report at minimum:

- documents per second
- add latency p50/p95
- ask latency p50/p95
- nodes per document
- relationships per document
- exact-match rate
- contains-match rate
- failure count

## GraphRAG-Bench Metrics

Report at minimum:

- retrieval quality
- evidence coverage
- answer quality
- unsupported-claim rate
- query latency p50/p95

## Artifact Rule

Benchmark outputs should be saved under:

- `outputs/evaluation/finder_benchmark/`
- `outputs/evaluation/graphrag_bench/`

JSON output should remain the default artifact so results can be compared over
time without depending on a specific trace backend.
