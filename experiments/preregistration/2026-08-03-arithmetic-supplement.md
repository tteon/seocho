# Pre-registration — the arithmetic supplement (tag s3, answering tag an2)

Written and committed before any extraction under tag s3. Prefix **AR-**.

## Why this run exists

The 280-case sample behind s1/s2 contains **zero** type≠None cases. The
sweep's preference for multi-reference cases excluded them — 881 of the 883
arithmetic-type cases carry exactly one reference. The consequence, stated in
the answering registration's addendum, is that Part 2 has been testing the
graph only on the terrain where it was expected to lose (prose, single
lookup), and the original motivation's central slice — questions that need a
figure *computed*, where CLAUDE.md §19 hypothesised the graph wins — has
never been tested in this study. This run closes that hole and does nothing
else.

## Sample — drawn before this file was committed, by fixed rule

`experiments/select_arithmetic.py`, output
`dataset/arithmetic_supplement_cases.txt` (140 ids, committed with this
file). Rule: pool = every case with type≠None and ≥1 reference (883);
allocation proportional by type with largest-remainder rounding; within a
type, `random.Random("42-<type>")`, the s1/s2 seeding idiom.

    Compositional 70 · Division 20 · Multiplication 19 · Subtract 18 ·
    Addition 12 · Subtraction 1        (Financials 91, Company overview 49)

Subtraction's single case is reported folded into Subtract, declared here
rather than decided after results. Per-type strata other than Compositional
are underpowered singly and are reported with that label.

## Extraction (tag s3)

Conditions **A and C only** — the two Part 2 reads; B/D/E settled in Part 1.
Same extractor models, prompts, chunking, store, and property floor as s1/s2;
the sample is the only change. 140 × 2 arms × 3 models = 840 extractions,
mostly single-chunk (~2 calls each). DeepSeek's share (~560 calls) runs on
its own quota day; gptoss and minimax start first — same staging discipline
as s2.

## Answering (tag an2)

The five conditions, prompts, and scoring stack of the an1 registration,
unchanged, with graph_a and graph_c both served from s3's snapshots (union
of three views, plumbing labels dropped, anchors as pointers). 140 × 5 × 3 =
2,100 calls, staged under the same quotas.

## Hypotheses

**AR-H1 — the gate holds on arithmetic questions.** Passages beat closed
book on number overlap. *Disconfirmed if* not separated — in which case the
downstream rows are void and reported as void.

**AR-H2 — the original claim, finally given its chance to fail.** On
type≠None questions, at least one graph condition beats passages on number
overlap. This is CLAUDE.md §19's pre-registered direction (graph wins
compositional/multi-step slices), carried forward verbatim as the hypothesis
under test. The honest prior is against it: this repository has measured
vector ≥ graph three times on other corpora, and an1's non-arithmetic sample
shows passages ≥ graph everywhere or ties. *Disconfirmed if* passages ≥ every
graph condition — which would close the original motivation's question in
the negative, on its own chosen ground.

**AR-H3 — structure helps grounding even where it does not help accuracy.**
The evidence-grounding rate (share of answer figures present in the served
evidence, years excluded) is higher under the graph conditions than under
passages, replicating the an1 pattern on this stratum. *Disconfirmed if*
the ordering reverses or flattens.

**AR-H4 — the anchor trust signal replicates.** graph_c_anchors ≥ graph_c on
number overlap for at least the models where an1 separated them (minimax),
with the same pointer-only payload. *Disconfirmed if* the an1 separation
does not reproduce — which would demote that result to a one-model,
one-sample observation.

## What will not be claimed

Arithmetic cases are single-reference, so this stratum cannot speak to
multi-document synthesis (S2/S4/S5 of the original table); only the
computation slices (S1/S3 analogues) are covered. Passages remain a ceiling
for retrieval, not a simulation of it. The judge panel's 60-case subsample
is an1's; an2 adds a fresh seed-42 draw of 30 from these 140, fixed in the
run config before any call.

## Analysis fixed in advance

    python3 experiments/export_snapshots.py     --tag s3 --arms A,C
    python3 experiments/materialize_anchors.py  --tag s3
    python3 experiments/answering.py            --tag an2 ...
    paired bootstrap over cases, 5,000 draws, per model — the an1 code path

Primary outcome: AR-H2's paired passages-versus-graph differences per model.
Secondary: AR-H3's grounding ordering, AR-H4's replication, and per-type
means for Compositional/Division/Multiplication/Subtract(+Subtraction),
reported with their n.

---

## Addendum (2026-08-05) — evidence-conditional grading, registered for an2
## before any an2 graph condition has run

Exploratory analysis of an1 (after its results) decomposed each answer by
whether the served evidence contained the gold figures: grounded-correct /
utilization-failure / over-refusal / honest-abstention / contaminated /
hallucination. On an1 it reversed the naive reading: stripped of
contamination, graph_a's grounded-correct count beat passages on gptoss
(111 v 107) and minimax (138 v 124), passages carried 2-4x the
contamination of graphs, and deepseek's graph deficit was over-refusal
(55 v 31), not misunderstanding. Found in the data, so on an1 it is
exploratory and is labelled as such wherever reported.

The an2 graph conditions have not run (s3 deepseek extraction in flight at
the time of this commit), so the following are registered predictions:

- **AR-EC1** · grounded-correct under at least one graph condition >=
  passages, on at least two of three models.
- **AR-EC2** · contaminated count: passages > each graph condition, per
  model. (On the arithmetic stratum contamination should be rarer overall —
  closed book collapsed to 0.12-0.14 — so this may be a near-zero
  comparison; if both sides are ~0 it is reported as uninformative, not as
  a win.)
- **AR-EC3** · deepseek's over-refusal on graph conditions exceeds its
  over-refusal on passages, reproducing the mechanism behind AN-H3.

Boundary, stated now: "evidence contains the answer" is a numeric-token
set approximation; whether a contained figure is attached to the right
entity is the judge panel's question, not this decomposition's. The
grounded boundary is overlap > 0, so partial answers count as grounded.
