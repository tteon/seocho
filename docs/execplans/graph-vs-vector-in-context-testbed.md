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

## Status

Layer 3 is built and validated. Layer 1 has one prior result (`ADR-0154`) and no
three-arm run. Layer 2 is designed (`seocho-ees`) and blocked on hardware. The
question set with the fact-distance stratifier does not exist yet and is the
first thing to write.
