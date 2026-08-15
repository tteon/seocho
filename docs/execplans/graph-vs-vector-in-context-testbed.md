# Why graph, why vector, why both — a serving-side testbed

This plan follows `docs/maintainers/EXECPLAN_SPEC.md`.

Supersedes the framing in `serve-track-testbed.md`, which described the rig
without saying what it is for. This says what it is for.

## The question everyone is arguing about, and nobody is answering

"Graph RAG or vector RAG" is argued as a retrieval-quality contest. The
repository already measured that contest to a tie twice: `ADR-0112` found
retrieval quality generalises across two GraphRAG-Bench domains with graph ≈
vector, and `ADR-0154` found ontology guidance in the extraction prompt
*lowered* cross-model agreement. Running the same benchmark a third time will
produce a third tie.

The unanswered question is not *which wins*. It is **why either would**, and
therefore **when each does** — and that question lives inside the model's
in-context processing, not in the retrieval metric.

Reframed:

> A graph serialization and a vector passage set are two different **contexts**
> presented to the same model. They differ in redundancy, in token order, in how
> far a needed fact sits from the tokens that need it, and in whether relations
> are stated or must be inferred. Those are properties the model's own
> mechanics — attention over the prompt, expert routing, prefix reuse — respond
> to differently. Measure the response, and the *when* falls out of the *why*.

Target answer shape: **sometimes graph, sometimes vector, sometimes both**, each
with a mechanism, not a leaderboard.

## Why this needs a GPU, and why almost nobody has done it

Every comparison in this literature is run over a hosted API. An API returns
tokens and a usage dict. It cannot tell you:

- which prompt tokens the answer actually attended to
- which experts a token routed to, and whether a graph context routes differently
- what the KV cache did — what was reused, what was evicted, what was recomputed
- where prefill ends and decode begins, per stage

All of that is visible only from inside the serving engine. So the differentiator
is not a better benchmark; it is **running the serving stack ourselves** — vLLM,
our own GPUs, `vllm-hook` — and paying the trial-and-error cost in public:
which hooks attach, which artifacts are actually written, which flags are honoured,
which stability caveats bite. That accumulated operational knowledge is the moat,
and today already produced several instances of it (see "What is already known").

## Three measurement layers

Each layer answers a different kind of question. They are not substitutes, and
mixing them is how today's session produced four wrong conclusions.

| Layer | Instrument | Answers | Causal? |
|---|---|---|---|
| **Behaviour** | ablation / evidence-conditional eval | does context form change the answer | **yes** — it intervenes |
| **Mechanism** | attention over prompt spans; MoE routed experts | where in the context the model acts | no — observation |
| **Serving** | KV events, `/metrics`, stat-logger plugin | what it costs to serve that context | no — observation |

The discipline that follows: a mechanism observation is only worth collecting
once the behaviour layer has found an effect to explain. `ADR-0154` supplies one
such effect. Everything else waits.

## The experiment the whole thing is built toward

**Same question set, same corpus, same model. Three context forms:**

- `vector` — top-k passages, no explicit relations
- `graph` — serialized subgraph, relations stated, entity-deduplicated
- `both` — the hybrid people actually ship

Then, per question, measure all three layers and look for the *joint* pattern:

| Observation | Reading |
|---|---|
| graph wins behaviourally **and** attention concentrates on the relation tokens | graph is supplying the relation the model could not infer |
| graph wins behaviourally but attention is flat across the subgraph | graph is winning on **redundancy removal**, not on structure |
| vector wins and attention is on one passage | the answer was extractive; structure was overhead |
| `both` wins with attention split | complementary, and the merge policy is the lever |
| routing differs by context form beyond the null | the forms are being processed differently, not merely read differently |

The stratifier that makes this legible is **how far the needed fact sits from
the tokens that need it**, plus whether the question needs a relation that is
*stated* in the graph form and only *implied* in the passages. Pooled averages
hide exactly this — the same mistake the median hid in the FinBench db_hits data
(see below).

**Correction, 2026-08-15.** An earlier draft said GraphRAG-Bench "carries no
gold triples, so hop count is not derivable". That was wrong. The Novel subset
has an `evidence_triple` field holding literal triples, and counting them tracks
the dataset's own labels closely: Fact Retrieval mean 1.19 (901 of 971 items
exactly one), Complex Reasoning 2.67, Contextual Summarize 3.25, Creative
Generation 6.16 with every item >= 4. Medical has no such field and supplies
dispersion only (2.26 / 3.46 / 6.11 / 13.17 statements by the same four types),
so the two subsets must not be pooled on the hop axis.
`scripts/serve_track/annotate_graphrag_bench.py` derives this over all 4,072
items; 2,009 carry a hop count.

So the division of labour is narrower than first stated: real data supplies hops,
dispersion and reasoning type, and the synthetic set is needed only for the four
strata the benchmark cannot express — aggregation fan-out, absence/negation,
entity ambiguity, and distractor density. Those four are precisely what separate
"structure helped" from "redundancy removal helped" and from "retrieval
precision helped".

**What the GraphRAG-Bench paper already shows, and does not explain.** Across
nine GraphRAG methods on GPT-4o-mini: average accuracy 69.30-73.58 against a
70.68 no-retrieval baseline. Two methods (DALK, G-Retriever) *degrade* it;
BM-25 and TF-IDF beat five of the nine; the winner is RAPTOR, a **tree**, not a
graph. By question type, multi-choice accuracy drops for most methods while
true/false and open-ended improve, and in the mathematics domain **every**
GraphRAG method degrades accuracy. The paper attributes these to "retrieval
noise" narratively. That is the phenomenon this testbed exists to give a
mechanism to: the *when* is already visible in their numbers, the *why* is not.

## What is already known — carry these forward

Established today or earlier, and load-bearing for the above:

- **The graph is not the latency bottleneck; query planning is.** Measured on the
  real pipeline: `compile_cypher` 3417 ms (70%), `synthesize` 1488 ms (30%),
  `rag.execute` 2.1 ms (0.04%). Caveat: three-node graph — at FinBench SF1000 the
  same code timed out at 60 s, so this inverts with scale and the crossover is
  governed by sargability, not node count.
- **Planning is decode-bound, and ~94% of its generated tokens are reasoning
  tokens** for a 43-character JSON output (MARA MiniMax-M2.7, n=1 — indicative).
  Prefill is a low single-digit share, which bounds what any prefix-caching work
  can buy.
- **Prefix reuse is real and measurable**: cold 55 blocks / 789 ms vs warm 1 block
  / 542 ms; prefill 47.5 ms → 11.7 ms on an 847-token prompt.
- **Constrained decoding costs nothing in prefix reuse** (98.2% cached across
  plain, `json_object`, and two `guided_json` schemas). This matters here: it
  means output form can be held constant across arms for free.
- **The two caches' working sets diverge** (`ADR-0156`), so co-managing the graph
  page cache and the KV cache is off the table. KV-side work stands alone.
- **The ontology's measured value is in the tail, not the median.** FinBench, 819
  episodes: median db_hits identical across `labels`/`ontology`/`guardrail` at
  every scale, but at SF10 labels-only reaches 52.1M db hits against the
  ontology arm's 2.17M. Reporting medians hid this, in this repo's own write-up.
  Treat the tail claim as provisional until a paired test over the 13 questions
  with bootstrap CIs replaces the max-of-39 statistic.

## Operational knowledge banked so far

The trial-and-error this plan exists to accumulate, already paid for:

- vLLM's KV events carry **no request id**; `FinishedRequestStats` does, via the
  documented `vllm.stat_logger_plugins` group. Patching `vllm.v1.core` instead
  would reach nothing — `EngineCoreProc` is a separate process.
- `routed_experts` is a first-class output (`[seq_len, layer_num, topk]`,
  covering prompt *and* generated tokens) behind
  `--enable-return-routed-experts`. MiniMax-M2 is MoE on every layer, so layer
  indices map 1:1; DeepSeek-V2/V3 is hybrid and would not.
- vLLM 0.27.1 does **not** populate `prompt_tokens_details.cached_tokens`; the
  block side is the only usable cache signal there.
- `IBM/vLLM-Hook` installs and its hooks attach on 0.27.1 despite a stale
  `<=0.21` pin, but the QK capture path writes nothing — porting work, scoped in
  `seocho-8hb`.
- vLLM 0.27.1 needs Python 3.12; flashinfer subscripts `array.array` on 3.10.

## Two corpora, and the question that needs both

One benchmark answers "does the distinction show up here". Two answer "is the
distinction useful", which is the one worth asking.

| | GraphRAG-Bench | EnterpriseRAG-Bench |
|---|---|---|
| corpus | textbooks, novels, curated | 512k synthetic enterprise docs, 9 sources |
| noise | none by design | misfiled ~8%, near-duplicates, conflicts, jargon |
| supplies | **hop count** (Novel `evidence_triple`), dispersion, reasoning type | dispersion exactly (gold-set size), conflict, absence, completeness |
| cannot supply | conflict, absence, aggregation, ambiguity | **hop count** — no gold triples |

ERB is closer to real company data and the paper measures rather than asserts
it: top-10 local cosine 0.83 for both ERB and a real Onyx corpus, against 0.69
for open-web BrowseComp-Plus. Dense neighbourhoods mean abundant distractors.

Its categories cover three strata the synthetic set had to invent — Conflicting
Info, Info Not Found, Completeness — and its gold-set sizes give dispersion by
construction (1 for Basic/Semantic/Intra-Doc/Misc, exactly 2 for Conflicting,
mean 4.2 for Project Related, mean 6.5 for Completeness). It adds one axis we
did not have: **Intra-Doc Reasoning**, dispersion *inside* one long document,
which stresses chunking rather than retrieval.

Neither corpus supplies everything, and they must not be pooled on the hop axis
— only GraphRAG-Bench Novel has it, the same rule that separates Novel from
Medical.

**Both benchmarks already show the intuitive story failing.** On GraphRAG-Bench
a *tree* (RAPTOR) beat every graph method and BM25/TF-IDF beat five of nine. On
ERB, vector loses to BM25 on *Semantic* questions — 32.8 against 44.8
correctness — the one category built to favour embeddings by suppressing keyword
overlap. Both papers explain this narratively and leave it to future work. Two
independent datasets, same shape of unexplained result: that is the gap.

The comparison to run is therefore not the pooled score but the **stratum
profile across corpora**. A distinction that survives only on clean textbook
data is not useful. Tracked as `seocho-9ea.1`; the ERB slice and adapter already
exist under `seocho-vdw.6` and must be reused, not rebuilt.

## Method rules, learned the expensive way

1. **Two controls, never one.** A ceiling control (identical inputs → 1.000) and
   a floor control (two different inert inputs → the null spread). Publish the
   null next to the effect. A routing experiment today had the ceiling, lacked
   the floor, and its 0.015 effect was uninterpretable — with a token-alignment
   artifact worth 0.029 hiding inside it. Retracted as `seocho-02t`.
2. **Build prompts from token ids, not string concatenation**, and assert token
   equality over any compared window. That artifact came from a newline merging
   differently per arm.
3. **Report every pair computed.** The same run produced four comparisons; two
   were reported, and the full four pointed the other way.
4. **Do not constrain output on the pass that validates the controls.** A control
   whose output shape differs is a broken control; constraining it hides the
   signal without fixing it.
5. **Observability that is opt-in is observability you do not have.**
   `rag.retrieve_ctx` already recorded `n_records`; `SEOCHO_TRACE_BACKEND`
   defaulted to `none`; a one-line-refutable claim survived a whole investigation.

## Sequence

1. **Behaviour first.** Three-arm ablation (`vector` / `graph` / `both`) on a
   question set stratified by fact-distance and by stated-vs-implied relation.
   No mechanism instruments yet.
2. **Mechanism only where behaviour found an effect.** Routing via
   `routed_experts` (needs the deployed MoE, so H200 + MiniMax-M2.7); attention
   over prompt spans if the vLLM-Hook port lands. Both under the two-control
   rule.
3. **Serving cost on the same runs.** Per-stage KV attribution and per-request
   prefill/decode split — already built, `scripts/serve_track/`.
4. **Synthesise the when.** Map each stratum to a recommendation with its
   mechanism, and state the strata where the honest answer is "no difference".

## Second behaviour run — 2026-08-16, MiniMax-M2.7, 20 items, 10 strata

The first run (below) was a ceiling, not a tie. This one discriminates, and the
answer to *when graph, when vector, when both* falls out of it with mechanisms.

| arm | correct | prompt tokens | TTFT median |
| --- | --- | --- | --- |
| `vector` | 20/20 | 435.4 | 116 ms |
| `graph` | 16/20 | 102.5 | 114 ms |
| `both` | 17/20 | 438.2 | 117 ms |
| `graph_unstructured` | 16/20 | 86.9 | 112 ms |

| stratum | n | vector | graph | both | graph_unstr |
| --- | --- | --- | --- | --- | --- |
| S1 extractive | 2 | 2 | 2 | 2 | 2 |
| S2 joined (2 hop) | 2 | 2 | 2 | 2 | 2 |
| S3 deep join (3 hop) | 3 | 3 | 3 | 3 | 3 |
| S3b four hop | 3 | 3 | 3 | 3 | 3 |
| S4 aggregation (fan-out 5) | 3 | 3 | 3 | 3 | 3 |
| S5 absence | 1 | 1 | **0** | 1 | **0** |
| S6 ambiguous entity | 1 | 1 | 1 | 1 | 1 |
| S7 distractor | 1 | 1 | 1 | 1 | 1 |
| S7b near-miss names | 1 | 1 | 1 | 1 | 1 |
| S8 negation | 3 | 3 | **0** | 1 | **0** |

### Stated structure is worth nothing

`graph` and `graph_unstructured` score **16/20 each, identical in every
stratum**. The two arms carry the same triples in the same order and differ
only in whether the markup is present: `(A) -[REL]-> (B)` against `A REL B`.
This is the control `vector_matched` failed to be, and it returns a clean null.

So the graph form's value is **not** that relations are typographically stated.
Strip the markup and nothing changes — except that the flat form costs 86.9
prompt tokens against 102.5, i.e. **15% cheaper for identical answers**.

### What the graph form does buy: 4x prefill

435.4 prompt tokens for `vector` against 102.5 for `graph`, at parity on every
positive-query stratum including four-hop joins and five-wide aggregation.
That is a 4.25x reduction — prefill the engine never runs — and it is the one
number in this run that belongs to the serve track rather than the retrieval argument.

TTFT remains flat at 112-117 ms across all four arms. Over an API the 4x token
difference is invisible; measuring the prefill saving needs KV-block counts on
the H200 box.

### Where the graph form is categorically wrong

`graph` scores **0 of 4** across negation and absence, while `vector` scores
4 of 4 and the model answers `NOT STATED` every time. This confirms
`seocho-2gq`, which was a one-item hypothesis after the first run, and rules
out the alternative explanation: the model can do these — it does them from
prose — so this is the representation, not the task.

The mechanism: a triple list asserts positives. "Which products are not sold in
Norland" requires the complement, and relevance-filtered retrieval is precisely
what discards it. Adding hops does not help — S8's join-and-negate item fails
the same way as its one-hop sibling.

`both` does not rescue it either (1 of 3), while costing 438.2 prompt tokens —
the same as `vector` — and the most output tokens of any arm at 255.8. The
naive hybrid is the worst cost profile in the set for a partial fix.

### The guidance this produces

1. **Route by question type, not by system.** Positive lookup, join to any
   depth, aggregation, entity disambiguation: graph, at a quarter of the
   prefill. Negation, absence, "which did not": vector, or the graph lane must
   emit the complement rather than the matched edges.
2. **Stop paying for serialization.** The markup is 16% of the graph arm's
   tokens and buys nothing measurable.
3. **`both` is not a safe default.** It costs like `vector` and does not fix
   what `graph` gets wrong.

### Three harness defects this run exposed, all in scoring

Fixed before the numbers above were taken, and each pinned by a test:

- The world was too small. Four suppliers over three plants put three arms at
  the ceiling; scaled to 12 suppliers, 6 plants, 6 products, a material layer
  for four-hop chains, and fan-out of 5 (`seocho-eer`).
- The four-hop questions were **ambiguous by construction** in the first draft:
  `Silica` reached both Sudmark and Norland, and a dict built from the pairs
  silently kept the last supplier, producing an answer that looked right and
  was unverifiable by hand. The generator now asserts chain convergence and
  refuses to emit an ambiguous item.
- Set answers cannot be scored by substring containment, and an LLM judge told
  to reject extra items over-rejects instead. Containment marked all three
  correct negation answers wrong because the model wrote "K2, M3 **and** R4";
  the judge then marked the same answers wrong because they went on to say
  where each product *is* sold. The generator now carries the complement, and
  the check is an exact set test — every gold item present, no excluded item —
  with no judge involved. Both failure directions are pinned.

## First behaviour run — 2026-08-15, MiniMax-M2.7, 12 synthetic items

Superseded by the run above; kept because its nulls and its four retracted
harness defects are why the second run is readable.


Four arms, temperature 0, scored by token containment (short golds) with a
`gpt-oss-120b` blind judge as the fallback. Reproduce with:

```
python3 scripts/serve_track/make_question_set.py  --out outputs/serve_track/questions.jsonl
python3 scripts/serve_track/make_context_arms.py  --questions outputs/serve_track/questions.jsonl \
    --out outputs/serve_track/arms.jsonl --budget 2000
python3 scripts/serve_track/run_arms.py --arms outputs/serve_track/arms.jsonl \
    --out outputs/serve_track/results.jsonl
```

| arm | correct | prompt tokens | chars | TTFT median |
| --- | --- | --- | --- | --- |
| `vector` | 12/12 | 166.0 | 467.5 | 115 ms |
| `graph` | 11/12 | 86.3 | 79.5 | 112 ms |
| `both` | 12/12 | 168.8 | 456.8 | 115 ms |
| `vector_matched` | 1/12 | 73.5 | 71.0 | 113 ms |

**Accuracy separates nothing, and that is the honest headline.** Three arms sit
at or beside the ceiling, so this set cannot support any claim that one context
form answers better than another. It was built as a hand-checkable probe rather
than a powered comparison, and raising item difficulty comes before raising n
(`seocho-eer`) — the same wall `ab_reasoning.py` already recorded for its own
set.

**The one measured difference is compression — 1.9x, not 5.9x.** The first
write-up of this run quoted 5.9x, which is the ratio in *characters*. In prompt
tokens, the unit prefill is actually billed in, the same two arms are 166.0 vs
86.3 — **1.92x**. Triples are punctuation-dense and tokenize badly; prose does
not. The character figure overstated the effect by three times and must not be
quoted. This was avoidable: `make_context_arms.py` was written with a
`budget_unit` field precisely so the two units could never be confused, and the
run was then done at the character default and reported in characters anyway.

**TTFT shows no prefill effect at all.** Median time-to-first-token is 112-115 ms
for every arm, with a standard deviation of 127-155 ms across only 12 items. Over
a network API, TTFT carries queueing and transport that have nothing to do with
context length, and at this spread the 1.9x token difference is invisible. No
latency claim can rest on this; the prefill saving has to be measured on the
H200 box against KV-block counts, where the confound is absent.

**`vector_matched` did not work and must not be quoted.** It trims passages to
the graph arm's length, but prose stating a fact is inherently longer than the
triple stating it, so the trim removes the fact — the gold string was absent
from 8 of 12 contexts. Its 1/12 measures information deprivation, not
compactness, so `graph` beating it is not evidence that structure helped. Length
cannot be held constant while facts are; the replacement control renders the
same triples with the relation markup stripped, matching facts *and* length and
varying only structure (`seocho-alm`, pinned by
`test_length_matched_prose_cannot_also_be_fact_matched`).

**One case is worth more than the totals.** S5, the only item graph lost:
*"Which of Model K1, K2, L9 is not sold in Norland?"* The graph arm received the
single relevant edge, `(Model K2) -[SOLD_IN]-> (Sudmark)`, and answered NOT
STATED; the vector arm held all three sold-in statements and did the exclusion.
A triple list is a positive-assertion representation, and a negation needs the
complement — which relevance filtering is precisely what destroys. This is a
candidate answer to *when does graph lose*, which is half the thesis, but it is
n=1 on one model and one phrasing and is recorded as a hypothesis only
(`seocho-2gq`).

**`both` was not splitting the budget, it was concatenation.** The budget was
2000 characters and the longest arm used 869, so the split branch never engaged
and `both` was exactly `vector + graph` in 12 of 12 items — the doubling confound
of ADR-0105, re-entered not through a missing cap but through a budget too loose
to bind. `both` is now capped at what the largest single arm actually used, which
holds whether or not the budget binds. Re-running with the cap left `both` at
12/12, so its score was not bought with the extra context — but that could only
be known after the fix, not before.

Four harness defects were found by this run and fixed before the numbers above
were taken: questions carried no `id`, so every result row was anonymous and
could not be joined back to its question; the scorer marked a correct `Model K1`
wrong because the model emitted U+202F between the words; `both` doubled as
described; and the run captured per-call telemetry into the results file and then
never read it, which is how the character ratio survived as the headline. The
runner now prints prompt tokens, output tokens, reasoning tokens and TTFT spread
next to the accuracy table, because a number nobody prints is a number nobody
checks. All four are pinned by tests.

## Status

Layer 1 (behaviour) has now found the effect the other two layers exist to
explain, so the gate is passed:

- **stated structure is worth nothing** — `graph` and `graph_unstructured` tie
  at 16/20, identical in every stratum
- **the graph form buys 4.25x fewer prompt tokens** at parity on every positive
  stratum, including four-hop joins and five-wide aggregation
- **the graph form is categorically wrong on negation and absence** — 0 of 4
  where vector is 4 of 4, and the model answers NOT STATED every time
- **TTFT is flat** at 112-117 ms across all arms, so the prefill saving is
  invisible over an API and has to be measured on the box

Layer 3 (serving) is built and validated. Layer 2 (mechanism) is designed
(`seocho-ees`) and blocked on hardware.

## Next, on H200 x4 with MiniMax-M2.7

The behaviour layer says *what* differs. The GPU work says *why*, and turns the
routing rule above into engineering guidance:

1. **Confirm the 4x in KV blocks, not tokens.** Prompt-token count is an API
   proxy. Re-measure `graph` vs `vector` against `BlockStored` counts and
   prefill time from the KV-event stream; the ratio that matters for capacity
   is blocks, and `block_size` quantisation may eat part of a 4x.
2. **Attention over the negation failures.** The graph arm answers NOT STATED on
   all four negation/absence items. Look at where attention goes on those
   prompts: if it concentrates on the matched triples and never spreads, the
   complement really is absent rather than merely unattended — which decides
   whether the fix is retrieval (emit the complement) or serialization.
3. **Routed experts by context form** (`seocho-ees`). Both graph arms score
   identically while differing in punctuation density, which is the cleanest
   available test of whether the forms are *processed* differently or merely
   read differently. Run it with the ceiling and floor controls that
   `seocho-02t` lacked.
4. **Prefix reuse across a graph agentic loop.** The agentic path re-issues a
   stable system prefix per turn; measure what the 4x compression does to
   cache-hit rate over a multi-turn session, where it compounds.

GraphRAG-Bench remains the external-validity check and is gated on data: only
the Medical split is local and its `evidence_relations` field is prose, not
triples, so it yields no `gold_edges`. The Novel split, which carries
`evidence_triple`, has to be fetched first (`seocho-9ea.1` tracks ERB as the
second corpus).
