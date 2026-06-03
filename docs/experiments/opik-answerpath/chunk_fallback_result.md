# Graph-native chunk fallback — measured graph contribution

The answerability diagnosis found structured Cypher returns 0 records on
7/10 FinDER cases (cypher↔extracted-structure mismatch), while the facts
sit in `Chunk.text`. Fix: when structured retrieval is empty (and no
vector_store supplied context), query the graph's own Chunk nodes by
question keywords and feed that text to synthesis. Opt-in via
`SEOCHO_CHUNK_FALLBACK`.

## Result — chunk-grounding (prior-independent)

For each FinDER case, does the fallback retrieve chunk text that CONTAINS
the gold answer? (This measures the GRAPH supplying the answer, not the
LLM's priors.)

| metric | value |
|--------|-------|
| structured-Cypher answerability | 3/10 = 0.30 |
| **chunk-fallback grounding (gold answer in retrieved chunk)** | **6/10 = 0.60** |

All 10 cases got a chunk hit; 6 retrieved text containing the exact gold
answer (Apple→Cupertino, MSFT→$211.9B, NVDA→$395M, Alphabet→John L.
Hennessy, Meta→$725M, Berkshire→General Re). The 4 misses (Tesla auto
$82.4B, JPM 15.0%, Amazon→Andy Jassy, Berkshire-Apple $174.3B) hit a
chunk but the gold figure/name wasn't matched by the keyword retrieval —
a retrieval-tuning gap, not a structural one.

## Why this matters

This is the first prior-independent improvement in graph contribution
this session. It converts "graph contributes ~0, LLM answers from priors"
(structured answerability 0.30) into "the graph's chunk store supplies the
gold answer in the majority of cases" (0.60). It directly targets the
measured bottleneck — unlike the query-lane sophistication
(GOPTS / RouteProfile / multi-plan / grounding) which all measured null
because there was no non-empty structured result to operate on.

## Honest limits

- On this FinDER subset (famous public companies), an end-to-end
  LLM-judge cannot confirm the gain because the model answers correctly
  from priors regardless (judge held 0.70 in the AnswerShape A/B). The
  chunk-grounding metric (answer present in *retrieved graph text*) is
  the prior-independent proxy; a judge-confirmed e2e gain needs a
  prior-resistant / non-synthetic corpus (the professor's standing
  demand).
- Keyword retrieval is naive (lowercased token CONTAINS); the 4 misses
  are tunable. Default-off pending that tuning + a prior-resistant A/B.

Raw harness: chunk-grounding measured via `_graph_chunk_fallback` over
the 10-case FinDER subset on DozerDB.
