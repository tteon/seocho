# Prompt Optimization Observability

SEOCHO exposes prompt optimization as a content-free receipt. Operators can
verify what the context composer selected, why it omitted candidates, and
whether the result improved cost and answer quality without storing prompts,
memory bodies, wallet addresses, or model reasoning.

## User-facing receipt

An authorized debug or evaluation response may include
`PromptAssemblyReceipt.to_dict()`. Its `optimization` object reports:

- candidate, selected, and omitted section counts
- estimated candidate and selected tokens, token budget, and compression ratio
- cacheable stable-prefix token estimate and hash
- section IDs with bounded exclusion reasons such as `superseded_revision`,
  `below_relevance_threshold`, `disclosure_denied`, or `budget_pruned`
- selected evidence and provenance counts
- prompt stage, provider, query mode, and semantic precedence

The receipt contains no section content. Normal answer responses should expose
a compact summary; full exclusion details belong behind an authorized debug or
evaluation flag.

Example:

```json
{
  "stage": "answer_synthesis",
  "provider": "mara",
  "query_mode": "graph_cot",
  "stable_prefix_hash": "...",
  "optimization": {
    "strategy": "stage_aware_selection",
    "candidate_section_count": 18,
    "selected_section_count": 7,
    "omitted_section_count": 11,
    "estimated_candidate_tokens": 6200,
    "estimated_selected_tokens": 1900,
    "token_budget": 2048,
    "compression_ratio": 0.3065,
    "excluded_section_reasons": {
      "memory-rev-18": "superseded_revision",
      "edge-bundle-7": "below_relevance_threshold"
    },
    "evidence_count": 5,
    "provenance_count": 5
  }
}
```

## OpenTelemetry and Grafana

`PromptAssemblyReceipt.to_trace_attributes()` emits only bounded aggregate
attributes. Section IDs and exclusion maps deliberately do not enter traces.
The `context.assemble` span should attach these attributes. Actual provider
usage belongs on the child `gen_ai.chat` span so estimated and billed tokens
can be compared without duplicating prompt content.

Prometheus dashboards should aggregate:

- selected token estimate and actual input tokens
- compression ratio and stable-prefix/cache-hit ratio
- missing-slot, provenance coverage, and unsupported-answer rates
- answer latency, time to first token, and provider cost
- optimization policy and prompt version comparisons

Never use `workspace_id`, user ID, wallet, transaction hash, prompt hash, or
trace ID as Prometheus labels. Use Grafana exemplars to open a sampled Tempo
trace when a metric changes.

## Evaluation contract

Optimization is accepted only when an A/B run keeps the dataset, retrieval
result, model, output contract, and concurrency fixed. Compare baseline and
optimized arms on answer correctness, provenance coverage, disclosure
violations, input tokens, latency, and cost. Token reduction alone is not a
quality improvement.
