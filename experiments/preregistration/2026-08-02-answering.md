# Pre-registration — answering under five evidence conditions (Part 2)

Written and committed before any answering call is made. This file supersedes
the condition table sketched in `papers/log2026/narrative/07-part2-search.md`
and keeps that document's gating ladder; where the two disagree, this file
wins, and the disagreement is recorded here rather than papered over: the
sketch had no anchor condition because it predates the alignment-key result,
and this design adds one.

    cases       the same 280 as tags s1/s2 (seed 42, 35 per category)
    models      DeepSeek-V3.1, gpt-oss-120b, MiniMax-M2.7 — each answers
                every condition, because AN-H3 is about the model × format
                interaction and an average across models would erase it
    depends on  s1 loaded (graph A) and s2 loaded (graph C) with anchors
                materialized; runs only after both
    volume      280 × 5 conditions × 3 models = 4,200 answering calls,
                staged under the same provider quotas as the sweeps

## Conditions — one factor, five levels

| Level | The model sees | Why it is here |
|---|---|---|
| closed book | the question only | contamination control: FinDER is built from public filings the models may have memorised |
| passages | the case's own reference text | the vector-retrieval proxy, and the comparison every reviewer will ask for |
| graph A | the case's subgraph from the no-ontology condition, serialized | isolates graph structure from vocabulary |
| graph C | the case's subgraph from the FIBO condition, serialized | the question Part 1 leaves open |
| graph C + anchors | graph C with each figure's source passage offset attached | does provenance change the answer, or only what the answer can be held to? |

Anchors attach to C and not to A because C is where the alignment key was
found and C is the graph the paper's centre concerns; running both would
double the paid calls to decorate a secondary contrast. This choice is made
here, before any result, so it cannot be made after one.

The serializer is the same for graph A and graph C — same code path, same
ordering, same property selection — so the A/C contrast is the graph's
content and never its formatting. Prompt templates are identical across
conditions except for the evidence block, and all are frozen in the run
config before the first call.

## The gating ladder, kept from the sketch

1. If closed book is not clearly worse than passages, the models answer from
   memory and nothing downstream measures retrieval. Report and stop.
2. Only if passages beats closed book do the graph conditions mean anything.
3. Only then is graph-versus-passages a retrieval comparison, and
   anchors-versus-not an attribution comparison.

## Hypotheses

**AN-H1 — the models cannot already answer.** Closed book scores materially
below passages on the primary metric.
*Disconfirmed if* closed book ≈ passages, in which case the experiment's
remaining rows are void and are reported as void — not as weak evidence.

**AN-H2 — the graph does not beat the text it was built from.** Passages ≥
both graph conditions. The graph is a lossy compression of the same passages;
this repository has measured vector ≈ hybrid ≫ graph three times on other
corpora, and registering the same direction again is the honest expectation.
*Disconfirmed if* either graph condition beats passages, which would mean the
compression removed distraction rather than information — the surprising
result, and the one worth a section if it happens.

**AN-H3 — the graph-versus-text gap is a model property.** The ordering and
size of the passages−graph gap differs across the three models by more than
each model's own resampling interval.
*Disconfirmed if* the per-model gaps are indistinguishable — which would make
the condition effect a property of the representation, and would license
averaging models after all.

**AN-H4 — anchors change attribution, not accuracy.** Graph C + anchors is
within interval of graph C on the primary metric, and strictly better on the
attribution check (the share of answers naming a source passage that actually
contains the served figure).
*Disconfirmed if* anchors move accuracy, which would mean the anchor text
carries answer content rather than provenance, and the condition is
mislabelled.

## Scoring, fixed now

- **Primary — number overlap**, deterministic and free: the gold answer's
  numeric tokens against the produced answer's, scale-normalised the same way
  `provenance.parse_amount` normalises extraction. Scored on every case.
- **Attribution check** (graph C + anchors only): a served figure counts as
  attributed when the cited offset's window contains that figure. Mechanical.
- **Secondary — judge panel** on a fixed 60-case subsample drawn now by seed
  42, never re-drawn: three MARA-hosted judges, majority verdict, judged
  blind to condition labels. A single judge is not used anywhere; this
  project has measured single-judge agreement at κ 0.20–0.67 and DeepSeek as
  a lenient outlier.
- Cases where a model call fails are failures, reported as N attempted vs
  N scored per condition. No imputation, no silent retry beyond the client's
  registered policy.

Prose-only questions score zero numeric tokens under the primary metric; they
are reported as a separate stratum by the dataset's own `type` column rather
than silently deflating the average.

## What will not be claimed

Passages here are the case's own gold references handed over in full — a
*ceiling* for vector retrieval, not a simulation of it. If passages win, real
vector retrieval may still do worse; if a graph condition wins, it beat
retrieval at its best, which strengthens rather than weakens that finding.
Nothing here measures multi-agent orchestration; that line is deferred. And
nothing here re-opens the ontology-for-extraction question — graph C's
content was fixed by s2 before this experiment reads it.

---

## Addendum — decisions fixed at harness build, before the first an1 call

Recorded when the runner was built and smoke-tested (4 paid calls under tag
`smoke`), before any call under `an1` was scored.

- **Which view a graph condition serves:** the union of the three extractor
  views, each fact tagged with its view, disagreements left in. Passages are
  retrieval at its best, so the graph gets extraction at its best (union
  coverage 0.476 vs 0.389 for the best single view). Any dedup rule would
  import Part 1's alignment machinery into Part 2's evidence.
- **Serializer exclusions:** Document, Chunk, DocumentVersion, Section nodes
  are dropped — Document and Chunk carry the reference passages verbatim, and
  serializing them would hand the graph conditions the passages condition's
  evidence. Applied identically to graph A and graph C.
- **Scoring tokenizer fixes, from the smoke:** "SP 800-171" read −171 as a
  negative figure (the hyphen-adjacency defect check_narrative had), and
  "(1)…(6)" enumeration markers counted as figures. Both fixed in the
  answering scorer only — provenance.tokenize is untouched because the
  anchors were located with it — and both rules apply to gold and produced
  text symmetrically.
- **Prompt freeze:** system prompts frozen at hash d8a368a27144407e after the
  smoke showed the first draft's refusal bar made the model ask for
  clarification instead of answering terse FinDER queries. Softened once,
  before any an1 call; identical across conditions except the evidence block
  and the anchors condition's citation instruction.
- **Empty arithmetic stratum, discovered at first scoring:** the 280-case
  sample contains zero type≠None cases — the sweep's own preference for
  multi-reference cases excluded them, since arithmetic questions almost
  always carry one reference. The type-column stratum split is therefore
  degenerate on this sample; the primary metric stands on the 199 cases whose
  gold answers carry figures, and nothing here can speak to arithmetic-type
  questions. This is a sample property fixed by s1/s2, not a choice made
  after results.
