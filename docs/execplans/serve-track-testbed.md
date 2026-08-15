# Serve-Track Testbed — and the case for a measurement ontology

This plan follows `docs/maintainers/EXECPLAN_SPEC.md`.

Two things live here. First, the serve-track rig written in PR #497, stated as a
**testbed** rather than a set of scripts. Second, the reason it kept producing
wrong intermediate conclusions, and a proposal that follows from the repository's
own prior finding.

## Part 1 — What the testbed is

A rig that takes one graph-agentic-RAG run apart and attributes serving-engine
behaviour to the pipeline stage that caused it.

| Component | Answers |
|---|---|
| `launch_vllm.sh` | the system under test, parameterised by model / TP / memory so 1x RTX 3070 and 4x H200 run identical code |
| `kv_windows.py` | when each LLM call ran, and which stage issued it |
| `cache_probe/kv_events_probe.py` | which KV blocks were stored/removed, at which storage tier |
| `vllm_metrics.py` | offload bytes and transfer time, per stage, by differencing `/metrics` at window boundaries |
| `vllm_plugin/` | per-request `request_id`, `num_cached_tokens`, prefill/queue/decode split, from inside the engine process |
| `correlate_kv.py` | the join, offline |
| `profile_rag.py` | drives the real pipeline against DozerDB and records the `rag.*` span tree |

### What it has already established

- Prefix reuse is real and measurable end to end: a cold stage stored 55 blocks
  (880 of 885 prompt tokens) at 789 ms; the warm stage stored 1 block and ran in
  542 ms — 98.2% reuse.
- Cold vs warm prefill on the same 847-token prompt: **47.5 ms → 11.7 ms**. This
  is the number an offloading decision turns on, since recall only pays when
  moving the bytes beats the prefill it avoids.
- Constrained decoding costs nothing in prefix reuse. Plain,
  `response_format=json_object`, and two distinct `guided_json` schemas all
  reported 832/847 cached. The cost is a one-time ~240 ms initialisation.
- A prompt-ordering defect worth 5x: question-scoped hints sat before the ~2.7 KB
  ontology body, capping the shared prefix at ~500 chars. Moving them to the tail
  took it to 2,699 chars and the engine's own hit rate from 55.5% to 76.2%.

### What it deliberately cannot do

- **Concurrency.** Window attribution is a containment test, so overlapping calls
  are ambiguous and `WindowRecorder` refuses them. The stat-logger plugin removes
  this constraint in principle (`request_id` per request); wiring the join
  through it is not done.
- **Answer quality.** The pipe-cleaner model exists to prove the harness records
  what it claims. Quality numbers from it would be noise.
- **Correctness of retrieved values.** The span tree counts records; it does not
  carry them. That is why an inverted edge direction (seocho-k5n) survived every
  instrument in this list and was caught by reading an answer.

## Part 2 — Why this testbed kept lying, and what follows

The rig produced four wrong intermediate conclusions before the right ones. They
are worth listing because they share one shape.

| Wrong conclusion | What was actually true | Why it survived |
|---|---|---|
| "prefix caching is not engaging" | it was; 55/56 blocks reused | asserted on `cached_tokens`, which vLLM never populates |
| "the stage reused nothing" (`stable_prefix_chars: 0`) | 2,699 bytes were stable | the metric compared section *sizes*, blind to stability inside a section |
| "MARA extracted the entities" | MARA returned 401 throughout | guided enforcement silently fell back and the run still reported success |
| "retrieval returned nothing" | `n_records: 2` | tracing defaulted to `none`, so the span that says so was never written |

Each is a **claim about the system that was not anchored to the artifact that
could refute it**. None was a reasoning failure; all four were provenance
failures.

### The repository already solved this, one layer up

`ADR-0154` decided, for extraction:

1. provenance-first ingestion — values are anchored to their source at write time
2. **alignment keys on provenance, not names**

The measurement layer does the opposite of both. Concretely, a single
serve-track run today:

- writes `kv_windows.jsonl` keyed by a 12-hex `trace_id` that is the run id
- writes `spans.jsonl` keyed by a 32-hex `trace_id` that is a per-`ask()` id
- **intersection of the two sets: empty**

Same field name, different identity space, silently un-joinable — the exact
name-alignment failure ADR-0154 measured and rejected. Repository-wide the
fragmentation is larger: 51 distinct `schema_version` strings under `scripts/`,
in inconsistent namespaces (`seocho.*` alongside bare `okx-*`), each a private
vocabulary.

### Proposal: model the measurement domain as an ontology

Not because ontologies are good, but because this is the same problem SEOCHO
exists to solve, and the product is the natural instrument for it. A first cut of
the TBox:

```
Run          --HAS_STAGE-->      Stage
Stage        --ISSUED-->         LLMCall
LLMCall      --PRODUCED-->       Artifact       (window, span, kv event, request stat)
Artifact     --ANCHORS-->        Measurement    (n_records, cached_tokens, blocks_stored)
Claim        --RESTS_ON-->       Measurement
Claim        --REFUTED_BY-->     Measurement
SystemUnderTest --SERVED-->      Run            (model, TP, engine version, graph)
```

The load-bearing edge is `Claim -RESTS_ON-> Measurement`. Under
`enforcement="strict"` a claim with no anchor is **refused at write**, exactly as
an untyped fact is refused today. That is the mechanism, not the schema:
"prefix caching is not engaging" would have had to name the artifact it rested
on, and naming it would have shown `cached_tokens` was absent rather than zero.

Honest counter-arguments, since this is a proposal and not a decision:

- An ontology does not prevent a wrong claim; it makes an unanchored one
  impossible to record. The four failures above were all recordable-but-unanchored,
  so this is the right shape — but a claim anchored to the *wrong* measurement
  still passes.
- It adds a write path to every experiment. If it is opt-in it will be off, which
  is precisely the failure that produced the fourth row of the table.
- Retrofitting 51 existing schemas is not free and probably not worth it; the
  value is in new measurement, with old artifacts left as-is.

### Relation to the AgenticOS CFP

This maps onto two of the three axes the workshop calls for. As a **testbed** it
is Part 1. As **observability, provenance, and auditability of autonomous agent
behaviour** it is Part 2, with a claim that is measured rather than argued:

> Observability that is opt-in is observability you do not have.

`rag.retrieve_ctx` already recorded `n_records`; `SEOCHO_TRACE_BACKEND` defaulted
to `none`; a one-line-refutable claim went unrefuted through an entire
investigation. The fix was a default, not a feature.

## Status

Part 1 is built and measured (PR #497). Part 2 is a proposal — nothing in it is
implemented. Tracked as **seocho-bm2** so the design argument is settled before
any schema is written; its five open questions (ownership, opt-in vs default-on,
wrong-anchor typing, retrofit scope, identity shape) are the gate.
