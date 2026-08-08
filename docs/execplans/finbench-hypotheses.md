# Hypotheses, status, and what each one still needs

The thesis this experiment is trying to establish, in one sentence: **as a temporal
transaction graph grows it becomes heavy-tailed, that distribution — not the volume —
is what reaches an agent's workload, and an agent must therefore be aware of specific
properties in order to recognise and route around the resulting bottleneck.**

This file separates the parts we have measured from the parts we have assumed, because the
two read identically in a slide and not at all in a review. Each hypothesis carries its
status, the evidence, **what would falsify it**, and what is still required.

Status legend: **supported** (measured, held) · **refuted** (measured, did not hold) ·
**partial** (measured in one direction only) · **untested** (asserted, no measurement) ·
**not yet testable** (missing apparatus).

---

## H0 — Growth produces a power-law degree distribution

> As a transaction network grows, its degree distribution naturally becomes heavy-tailed.

**Status: untested. This is the premise, and we have not shown it.**

Our generator *imposes* skew through `--hub-skew`; scale and skew are independent
parameters. We went SF1 → SF1000 with the distribution held fixed and observed no
structural change at all. So nothing here demonstrates emergence from growth.

What is citable rather than measured: LDBC FinBench states hub vertex degrees "may scale up
to millions in large data scales" and treats this as its distinguishing property against
the social-network benchmark. That is an authority claim about real financial graphs, not
our result.

**Falsifier:** a real transaction dataset sliced by time whose degree distribution stays
binomial as volume grows.

**To test it:** take one real temporal dataset, cut it into cumulative time windows, and
measure `max/median` degree per window. If the ratio grows with window size, growth drives
the tail. This needs a real dataset — synthetic data cannot answer it, by construction.
Until then the paper must cite the premise, not claim it.

---

## H1 — Distribution, not volume, is what reaches the agent

**Status: supported.**

| varied | held fixed | effect |
|---|---|---|
| volume, SF1 → SF1000 (1000x) | distribution | plan shape and cost profile unchanged |
| degree tail, max 31 → 158,315 | volume, code, questions | aggregate 45 ms → **timeout** |

**Falsifier:** a volume increase at fixed distribution that degrades agent accuracy or
cost. Partially contradicted already and worth stating: accuracy did fall 100% → 67% from
SF1 → SF1000 with code frozen, so volume is not entirely inert. The honest form of H1 is
*distribution dominates*, not *volume is irrelevant*.

**Still needed:** re-run the SF1 → SF1000 accuracy curve at fixed distribution now that the
direction-role fix has landed, to see whether that 100% → 67% decay survives. It may have
been the direction bug interacting with scale rather than scale itself.

---

## H2 — The operation class decides the bottleneck, not the data

**Status: supported, and it is the strongest result here.**

One anchor, degree 158,315, same graph, same hop count:

| question shape | db hits | latency |
|---|---|---|
| `DISTINCT … LIMIT` | 163 | 2.8 ms |
| `count(DISTINCT …)` | — | **> 30 s, no answer** |

Flat from degree 6 to 158,315 for the terminable shape. The non-terminable shape does not
return. **The property that matters is whether the answer permits stopping early**, which
is a property of the question.

This also refuted my own prediction: I expected the indexed anchor to stop helping on a
hub. It does not — Cypher's lazy evaluation stops as soon as the row limit is satisfied.

**Falsifier:** a terminable question that degrades with degree, or a non-terminable one
that does not.

**Still needed:** the same split measured on a columnar engine. If `count(DISTINCT)` over
the same neighbourhood is cheap in DuckDB, then H2 becomes an argument for engine routing
rather than for question refusal — a materially different conclusion.

---

## H3 — Cost can be predicted offline, before the query runs

**Status: supported.**

L2 (sum of out-degrees over the anchor's out-edges), computed from Parquet with no database
round trip, predicted measured db hits at **≈2x across five orders of magnitude**:

| L2 | measured db hits | ratio |
|---|---|---|
| 5 | 51 | — |
| 3,545 | 7,317 | 2.06 |
| 81,455 | 162,997 | 2.00 |
| 190,788 | 382,596 | 2.005 |

Curating anchors on L2 rather than degree gave within-band cost variation **197x tighter**
at the medium band (cv 0.0097 vs 1.9068).

**Falsifier:** a query family where L2 and db hits decouple. Expected candidates: filtered
expansions (a selective predicate on the edge changes the effective fan-out), and hop
counts above two, where the current estimator *extrapolates* from the observed branching
factor and is marked as doing so.

**Still needed:** extend the estimator past two hops and validate it there; validate under
edge predicates; and confirm the ≈2 constant is not specific to this schema.

---

## H4 — Anchor-local properties are enough to spot supernode risk

**Status: refuted.**

Degree is non-monotonic in cost. Measured, same query, same graph:

| anchor degree | measured db hits |
|---|---|
| 6 | 158,487 |
| 73 | 3,876 |
| 336 | 429,042 |
| 158,315 | timeout |

Twelve times the degree, forty times cheaper. Preferential attachment is why: a low-degree
node's few neighbours are disproportionately likely to *be* hubs. Holding degree exactly
constant (cv 0.0) left measured cost varying with cv 1.91.

**Consequence for the design:** "detect the supernode" is the wrong framing. Cost follows
the *neighbourhood*, so the awareness an agent needs is a neighbourhood estimate, not a
node property. This is why LDBC curates parameters by intermediate result size, and we
reproduced their reason before reading their solution.

---

## H5 — An ontology's vocabulary bounds what any prompt can convey

**Status: supported.**

`TRANSFER: Account → Account` makes "which accounts did X pay" and "which accounts paid X"
identical to the schema. Query construction defaulted to anchor-as-target, so every
outgoing question was answered with an in-degree, and the direction guardrail reported
nothing to repair because it returns early when `source == target`.

Adding `sourceRole: sender` / `targetRole: beneficiary`:

| | full ontology | labels only |
|---|---|---|
| accuracy | 1/12 → **12/12** | **0/12** |
| held-out paraphrases | **3/3** | 0/3 |
| db hits | 1,102,545 → 537,477 | 64,001,516 (119x) |

The labels-only arm answers `2,000,047` — the total account count — to every fan-out
question. Retrieval without declared relationships does not degrade, it collapses to the
whole graph.

**The sub-result that matters most:** the first version of this fix resolved direction by
substring-matching a hand-authored phrase list. It scored 12/12 on questions whose
phrasings its author had written into the list, and **0 of 6** on paraphrases. Overfitting a
mechanism to its own test set looks exactly like success. Moving the general case to the
model — with the ontology supplying the role *names* — is what made it generalise.

**Falsifier:** an ontology addition that improves accuracy without the prompt changing what
it conveys, which would mean the mechanism is something other than vocabulary.

---

## H6 — Cardinality in the ontology changes agent behaviour

**Status: untested. Apparatus built, measurement not run.**

`RelDef.degree_hint` now carries measured degree facts, derived from the snapshot by
`annotate_ontology_degrees.py` (recording `measured_from`, silent for relationships whose
degree carries no information). Both prompts convey it.

**Honest expectation: limited.** The model cannot make `count(DISTINCT)` cheap on a hub. The
plausible effect is on *choice* — preferring a terminable shape when one exists, or
flagging that a bound is being applied — not on making the expensive shape cheap.

**Falsifier:** identical query shapes and identical db hits with and without the hint.

**To test it:** ablate the hint alone (roles held constant) over the hub-anchored cases,
and compare the *shapes emitted*, not just accuracy. The metric is the share of questions
answered with a terminable shape, plus db hits per answer.

---

## H7 — Routing should key on engine capability, not on operation name

**Status: partial — one direction measured, and it refuted the naive rule.**

The naive rule "traversal ⇒ graph engine" is wrong. text2SQL scored **88%** against
text2Cypher's **25%** on the same questions and the same data, and DuckDB answered traversal
questions with `WITH RECURSIVE`, including a monotonic-time condition our Cypher arm never
produced. I predicted the opposite and retracted it.

The capability direction is supported from the other side: FinBench's own mitigation —
per-hop truncation ordered by timestamp — cost **70,000x more** than no mitigation when
expressed as user-level Cypher (11,502,593 db hits against 163), because `ORDER BY` is a
pipeline breaker that destroys the laziness doing the actual protecting. A bound the engine
cannot serve natively is an amplifier.

**So the routing signal is "which engine offers this operation cheaply", and it must be
measured per engine rather than assumed from the operation's name.**

**Still needed:** a capability matrix measured rather than declared — for each engine, does
it serve ordered top-K expansion, lazy limit, set-oriented join, recursive traversal, at
what cost. `ENGINE_CAPABILITIES` in `workload_gate.py` currently encodes two engines from
this experiment's measurements and is explicitly a stub.

---

## H8 — A pre-flight gate prevents the baseline bottleneck

**Status: untested. Gate built, verdicts produced, correctness not yet validated.**

`workload_gate.py` computes a descriptor before execution — operation class × predicted db
hits × terminability × bound-safety × engine capability — and returns one of four actions:
`execute`, `execute_bounded`, `approximate`, `decline`. It is deliberately **not** a query
rewriter, because H7's 70,000x result makes "emit a cleverer bound" the failure mode.

First run on the realistic snapshot:

| case | predicted db hits | verdict |
|---|---|---|
| small / medium / large fan-out | 22 – 136 | execute |
| medium two-hop | 7,546 | execute |
| large two-hop | 159,588 | execute |
| **huge two-hop** | **385,884** | **approximate** |

**The first run of the gate was invalid and the guard now prevents it:** cases curated on
one snapshot were priced against another snapshot's cost model, so every anchor looked cheap
and every verdict was `execute`. Anchor ids do not carry across snapshots. The gate now
refuses on a provenance mismatch rather than producing confident numbers about nothing.

**Falsifier:** a case the gate clears that then times out, or a case it declines that would
have returned comfortably.

**To test it:** execute every case and compare outcome against verdict — a 2x2 of
predicted-vs-actual. Two known-hard cases exist as anchors for that: the hub-band aggregate
(>30 s) and the unanchored ring question. Also worth probing the true maximum: the realistic
snapshot's max L2 is **51,447,907**, while the `huge` band sits at the 99.9th percentile
(192,942) — the decisive case is anchored at the maximum, and has not been run.

---

## H9 — Driver-level enforcement adds what query-level cannot

**Status: untested, and the usual justification for it is wrong.**

The graph engine is **0.3–0.9%** of end-to-end agent latency (122 ms against 37,083 ms of
LLM time). A faster driver optimises ~1%, so *latency does not justify* a Rust/bolt layer.

What might justify it are enforcement points that only exist at the transport boundary:

- **Early-abort streaming.** The lazy `DISTINCT … LIMIT` behaviour is what saved the hub
  case (163 db hits at degree 158,315). At the driver level that becomes explicit and
  controllable — applicable to queries where the model did *not* emit a limit.
- **Transaction bounds.** `session.run(q, timeout=N)` silently becomes a query *parameter*
  in the Python driver; the bound has to go on the transaction. I made that mistake twice,
  the second time after documenting it, which is an argument for an API where it is hard to
  get wrong.
- **Per-query row and memory accounting**, with a kill at a predicted budget.

**What driver work cannot fix:** aggregates. `count(DISTINCT)` on a hub must walk the
neighbourhood; no transport bound makes it cheap and correct. That is a routing decision.

**To test it:** measure driver overhead as a share of server time first. If it is ~0, the
latency argument is dead by data and the work is justified by enforcement or not at all.
Then measure whether early-abort streaming bounds a query the model left unbounded, using
a case that currently runs away.

---

## H10 — Quadrant routing (OLTP / OLAP / Graph-OLTP / Graph-OLAP) beats a single engine

**Status: not yet testable. Two of four engine classes exist.**

Wired today: DuckDB (columnar OLAP) and DozerDB (graph OLTP). **Graph-OLAP is absent**, and
"OLTP" in the row-store sense is not represented either. The quadrant cannot be claimed
from a two-point comparison.

**Still needed:** at minimum a third arm. The cheapest credible one is precomputed
summaries — FinBench's factor tables, which `curate_parameters.py` and
`graph_properties.py` already approximate — because it directly serves the unanchored motif
question that neither current engine can answer within budget. A graph-algorithm arm (GDS or
equivalent) would be the fourth.

**Falsifier:** a per-question best-engine assignment that never beats always choosing one
engine.

---

## What to do next, in order of what it settles

1. **Validate H8.** Execute every gated case and build the predicted-vs-actual 2x2,
   including an anchor at max L2 (51,447,907). Cheap, and it either establishes the gate or
   kills it.
2. **Test H6 by shape, not accuracy.** Ablate the cardinality hint alone and count the share
   of terminable shapes emitted.
3. **Measure driver overhead (H9).** One number decides whether the Rust/bolt direction is
   justified by enforcement or not at all.
4. **Add a third engine arm (H10).** Precomputed summaries, aimed at the unanchored ring
   question.
5. **Re-run the volume curve at fixed distribution (H1).** The 100% → 67% decay may have
   been the direction bug, not scale.
6. **H0 needs real data.** Until then, cite it.
