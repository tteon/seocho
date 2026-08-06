# Hypothesis ledger — what was registered, what was found, what was explained

This file is an index, not a registration. The registrations are the dated
files under `experiments/preregistration/`, each committed before the run it
governs, and nothing here adds to them. The ledger exists because three
documents now carry hypotheses numbered H1–H4 that are different hypotheses,
and because an earlier version of this file blurred the line this project
exists to keep sharp: it described an exploratory finding as pre-registered.
That version is replaced by this one.

Every hypothesis below carries one of three labels, and the label is the point:

- **registered** — direction and disconfirming outcome committed before the
  evidence. The file and its commit are the proof.
- **exploratory** — found in the data, not predicted. Real, but it has not
  survived a test it could have failed. Its registered replication is named.
- **post-hoc** — an explanation written after a result, offered as
  interpretation and never as a prediction.

Prefixes replace the colliding H-numbers: **SW-** (second sweep), **SC-**
(scale-up), **S2-** (condition-C scale), **AN-** (answering).

---

## Registrations on file

| File | Governs | Committed |
|---|---|---|
| `experiments/preregistration/2026-08-02-second-sweep.md` | v2 sweep, conditions A/C/D/E, 16 cases | before results, at 93/192 extractions |
| `experiments/preregistration/2026-08-02-scale-up.md` | s1 sweep, condition A, 280 cases | before the run; addendum at 115/840 |
| `experiments/preregistration/2026-08-02-condition-c-scale.md` | s2 sweep, condition C, 280 cases | before the run |
| `experiments/preregistration/2026-08-02-answering.md` | Part 2, answering under five evidence conditions | before the run |

A fifth registration predates all of these and governs a **different study**:
CLAUDE.md §19 pre-registers the Goldilocks direction for the FIBO
*module-size* sweep (non-ontology / small / medium / large). It is not a
registration of the A/C/D/E condition study, and this ledger's earlier
version was wrong to cite it as one.

---

## Second sweep (v2) — scored

Artifacts: `log2026.arm_results.v2`, `log2026.validity.v2`.

- **SW-H1** · equalising the property floor removes the period effect, not the
  agreement effect — **registered**, held.
- **SW-H2** · the subsumption hierarchy (E) raises agreement above plain FIBO
  (C) — **registered**, not separated; the registration itself named this the
  likelier outcome.
- **SW-H3** · FIBO's content advantage survives equal property slots —
  **registered**, held.
- **SW-H4** · no ontology condition escapes name fragmentation — **registered**,
  held. Every ontology condition agrees less on names than the one-class floor.

The floor's win is structural: one declared class gives two extractors nothing
to disagree about. Seventy classes give them seventy choices.

## The alignment key — exploratory, replication registered

Keying facts by their anchor in the source instead of by name multiplied
comparable pairs and revealed disagreements name-matching could not see
(`log2026.provenance_keying.v1`, 16 cases, condition C).

This was **not predicted**. It was found after the sweep and it is the study's
centre precisely because it could not have been retrofitted into the sweep's
registration. Its tests that can still fail are registered as **SC-H4** (does
the advantage survive 280 cases on condition A?) and **S2-H1** (does it
survive 280 cases on condition C, where it was found?).

## Scale-up (s1) — scored

Condition A, 280 cases, three models, 840/840 extractions, zero failures.
Artifacts: `log2026.verification_value.s1`, `log2026.routing_ceiling.s1`,
`log2026.provenance_keying.s1`, `log2026.arm_results.s1`, `log2026.validity.s1`.

- **SC-H1** · the verifiable-fact shortage is a sample-size problem —
  **registered, held.** Answer-relevant verifiable facts went 6 → 123 while
  cases went 16 → 280 (12 scored → 280): a 20.5× count on a 17.5× sample,
  at or above linear. SC-H2 became answerable, which is what SC-H1 gated.
- **SC-H2** · agreement-gated serving beats serve-always — **registered,
  disconfirmed** on its own criterion. Gating removed all six wrong figures
  (precision 0.951 → 1.000) but withheld 29 facts of which 23 were right
  (recall 0.951 → 0.764). The registered clause — recall falling more than
  precision rises — triggers on every weighting tried, including
  precision-weighted F0.5 (0.802 vs 0.951). Four right answers lost per wrong
  answer prevented.
- **SC-H3** · the single-view ceiling does not move — **registered, held.**
  The share of facts held by exactly one view *rose* with scale, 77.1%
  (309/401) → 87.1% (6,246/7,171). More cases do not make views land on the
  same facts; the ceiling is structural. (The registration's "around 95%"
  described condition C at n=16; on A the baseline was 77%.)
- **SC-H4** · the shortage was a keying problem — **registered, held.** At the
  same 280 cases, anchor keying yields 1,656 comparable pairs to name keying's
  992 (1.67×, up from 1.29× at n=16) and 606 disagreements to 324, of which
  485 are invisible to any name key (26 at n=16 — 18.7× growth on a 17.5×
  sample). The advantage did not shrink with scale.

SC-H1 and SC-H4 were framed as competitors and both held: the counts do grow
with data, *and* the anchor key multiplies them at any fixed sample. Together
with SC-H3 they say the method paper needs the anchor key first and more data
second — name keying leaves most of what scale buys on the table, and no key
reaches the 87% of facts only one view holds.

## Condition-C scale (s2) — scored

840/840 extractions, zero failures. Artifacts: `log2026.arm_results.s2`,
`log2026.provenance_keying.s2`, `log2026.validity.s2`, plus the paired
A-versus-C computation over the same 280 cases.

- **S2-H1** · the alignment key replicates on its discovery condition —
  **registered, held.** At 280 cases anchor keying yields 1,983 comparable
  pairs to name keying's 717 (2.8×; comparable rate 0.445 vs 0.079, 5.6×) and
  874 disagreements to 246, of which 734 are invisible to any name key. One
  honest caveat: the n=16 raw multipliers (6.1× pairs, 43× disagreements) were
  inflated by tiny denominators and do not carry to scale; the rate ratio does.
- **S2-H2** · the name-agreement gap persists as a paired comparison —
  **registered, held.** A − C on per-case name comparable rate:
  +0.069 [+0.046, +0.091], zero excluded. Part 1's headline stands at scale.
- **S2-H3** · the scale-error rate is stable — **registered, held.** 26.7% of
  anchored figures (1,872/7,019) matched their source only after rescaling,
  against "roughly a quarter" registered from v2.
- **S2-H4** · FIBO's content advantage survives scale — **registered,
  DISCONFIRMED, direction reversed.** C − A on per-case gold-figure coverage:
  −0.036 [−0.067, −0.008] — A captures *more* of the gold figures at 280,
  with zero excluded on the wrong side. The v2 reading (C 0.292 vs A 0.253)
  was a small-sample artefact. As the registration itself states, this
  removes the "ontology buys content at the price of names" reading and
  leaves the ontology with **no measured extraction benefit at all** on this
  corpus: fewer agreed names (S2-H2) and less of the answer's content, at
  once. The ontology's remaining measured contribution is detectability
  (SHACL violations, schema legibility), not extraction quality.

## Answering (Part 2, an1) — scored on all three models

4,200 calls (280 cases × 5 conditions × 3 models), zero failures. Artifacts:
`log2026.answering.an1` run directories and partials; paired bootstrap over
cases, 5,000 draws, per model.

- **AN-H1** · the models cannot already answer — **registered, held 3/3.**
  Passages − closed book: gptoss +0.092 [+0.006,+0.173], minimax +0.299
  [+0.232,+0.366], deepseek +0.238 [+0.195,+0.281]. The gate is open, though
  gptoss's closed book of 0.306 keeps contamination on the record.
- **AN-H2** · the graph does not beat the text it was built from —
  **registered, held.** No graph condition beats passages anywhere: deepseek
  separates in passages' favour on both graphs (+0.075, +0.062), gptoss on
  graph_a (+0.043), minimax ties both. The registered direction survives its
  third corpus.
- **AN-H3** · the passages−graph gap is a model property — **registered,
  held on one of three pairs.** Case-paired difference-of-gaps: deepseek −
  minimax +0.081 [+0.019,+0.144] separated; the other two pairs cross zero.
  One separated pair is enough for what the hypothesis protects: averaging
  models would erase a real difference (minimax ties passages with a graph
  the other two models lose with).
- **AN-H4** · anchors change attribution, not accuracy — **registered,
  DISCONFIRMED on two of three models.** With a pointer-only payload
  (never window text), accuracy *rose* under anchors on minimax +0.055
  [+0.012,+0.097] and deepseek +0.050 [+0.022,+0.080]; gptoss unchanged.
  The registered reading of that outcome ("the anchor text carries answer
  content") cannot apply — no text was carried. Post-hoc, clearly labelled:
  the pointer decorates only figures that anchored, so it acts as a trust
  signal steering the model toward source-verified figures — consistent with
  the evidence-grounding rate being highest under anchors (73%/70%) and with
  citation verification landing at 42–58%.

The registered direction was *against* the graph: the graph is a
lossy compression of the same passages, and this repository has measured
vector ≈ hybrid ≫ graph three times on other corpora. A graph win would be the
surprising result.

## Post-hoc explanations — never predictions

- **SHACL's role** (`log2026.shacl_check.v1`) — the ontology's measurable
  contribution is making a class of error *detectable*, not making names
  converge; a one-class condition has almost nothing it can violate. Written
  after the counts existed.
- **Verification's value** (`log2026.verification_value.v1`) — cross-view
  agreement converts precision into a serving decision. Written after.
- **Routing's ceiling** (`log2026.routing_ceiling.v1`) — an oracle router
  barely beats the best fixed view, so retrieval's problem is not routing.
  Written after.
- **Why synonyms did nothing** (`log2026.question_axes.v1`) — almost no
  question needs a FIBO synonym to be answerable, and the ones that do are two
  abbreviations in one category. An explanation of SW-era nulls, found later.

---

## Sample-size boundary, stated once

Everything scored under the second sweep rests on sixteen cases per condition.
Differences there separated by bootstrap interval are claims; everything else
is "not separated at this size", never "no effect". The 280-case sweeps exist
to move the claims that matter off that footing, and the ledger will record
which ones survive.

## Arithmetic supplement (s3/an2) — gate scored, rest in flight

Registration `experiments/preregistration/2026-08-03-arithmetic-supplement.md`
(commit 6280e9f, before any s3 extraction). Extraction: gptoss and minimax
560/560, deepseek in flight. Gate:

- **AR-H1** · passages beat closed book on arithmetic questions —
  **registered, held emphatically** on both models run: gptoss +0.411
  [+0.349,+0.472], minimax +0.410 [+0.355,+0.464], n=140 each. Closed book
  collapses to 0.12–0.14 on this stratum — computed figures cannot be
  recalled, which is exactly why this sample can test what the 280-case
  sample could not. AR-H2 through AR-H4 await the graph conditions.

## Arithmetic supplement (s3/an2) — fully scored

2,100/2,100 answering calls (429-contention failures re-attempted once,
documented, final failures zero). Artifact: `log2026.answering_analysis.an2`.

- **AR-H1** · gate — **held** (+0.41 both models first run; deepseek +0.254,
  separated).
- **AR-H2** · the original §19 claim: a graph condition beats passages on
  arithmetic — **registered, DISCONFIRMED decisively.** Passages beat every
  graph condition on every model, all intervals separated (+0.079 to
  +0.184). The original motivation's question closes in the negative on its
  own chosen ground.
- **AR-H4** · anchors ≥ graph_c where an1 separated (minimax) —
  **disconfirmed as registered**: minimax's an1 separation did not reproduce
  (−0.004, tie). The effect appeared on deepseek instead (+0.045, separated)
  — anchors cut deepseek's graph over-refusal 41→26 and raised its grounded
  count 64→82. The anchor trust signal is real but model-and-stratum
  dependent, which is itself the AN-H3 lesson repeated.
- **AR-EC1** · grounded-correct reversal replicates — **registered,
  DISCONFIRMED.** On arithmetic, passages' grounded-correct beats every
  graph on all three models (109/121/96 vs 80–106). The an1 reversal is
  stratum-dependent: where answers must be computed from several figures,
  the serialized graph genuinely under-serves even after stripping
  memorization.
- **AR-EC2** · contamination asymmetry — **uninformative as pre-declared**:
  counts are small on this stratum (3–11 per cell) because computed figures
  cannot be memorized; direction weakly consistent.
- **AR-EC3** · deepseek over-refusal concentrated on graphs — **held**
  (50/41 vs 24 on passages; anchors repair it to 26).

Reading, labelled interpretation: the two strata answer the user-facing
question differently — on prose/lookup questions the graph's grounded yield
matches text once contamination is separated (an1); on computation questions
the graph loses outright (an2), plausibly because arithmetic needs several
co-located figures and extraction fragments them. "When is ontology/graph
guidance appropriate" is therefore a per-stratum, per-model question — the
discriminator the profile work (PR-) exists to answer.

## Schema dose-response at scale (s5) — scored

1,680/1,680 extractions, zero failures. Artifacts: `log2026.arm_results.s5`,
`log2026.validity.s5`. The five-point curve on the same 280 cases (name
comparable rate, case bootstrap):

    A 0.293 [0.269,0.319] > C 0.211 [0.191,0.232] > D 0.140 [0.120,0.163]
    ~ E 0.126 [0.106,0.146] ~ B 0.118 [0.097,0.142]

- **S5-H1** · agreement monotone in declared-class count (A > B > C/D/E) —
  **registered, DISCONFIRMED.** B's twenty hand-written classes fragment
  naming below full FIBO's seventy; the curve is not a function of size.
- **S5-H2** · synonyms do not separate from C — **registered, DISCONFIRMED
  in the unexpected direction**: D separates BELOW C. At 16 cases the two
  were indistinguishable; at 280 the synonym lists actively fragment naming.
- **S5-H3** · hierarchy does not separate from C — **registered,
  DISCONFIRMED, same direction** (E below C).

Post-hoc, labelled: what predicts fragmentation is not how many classes are
declared but how much non-canonical lexical material the prompt carries —
bespoke class names (B), synonym lists (D), and hierarchy text (E) all
fragment naming below the bare, standard FIBO list (C), which itself sits
below the one-class floor (A). The schema's dose is measured in competing
vocabulary, not in classes.

## Reasoning census (s4/an3) — scored on the two MARA models
## (deepseek graph lanes complete on its quota day)

2,448/2,448 answering calls, zero failures after refills. Artifact:
`log2026.answering_analysis.an3`.

- Gate — **held emphatically**: passages − closed book +0.512/+0.691/+0.441,
  all separated; closed book collapses to 0.07–0.15 and contamination to
  0–3 answers per cell. The reasoning stratum is barely memorized.
- "Passages ≥ both graphs" (the thrice-measured prior, registered) —
  **DISCONFIRMED in the direction its registration called the result most
  worth having.** Each MARA model has at least one graph condition
  separated ABOVE passages (gptoss: FIBO graph −0.060 [−0.117,−0.004];
  minimax: schema-free graph −0.055 [−0.110,−0.002]) — the first graph win
  on the naive metric anywhere in this study, on the stratum the profile
  flagged in advance.
- **The prospective profile prediction — DISCONFIRMED as registered.** The
  addendum committed "each model's grounded-correct lift exceeds its an1
  lift". gptoss: +4 → +10, hit. minimax: +14 → +6, miss — the registered
  disconfirm clause fires. Post-hoc, labelled: at V = 0.796 minimax grounds
  148/169 from passages alone, so the lift is ceiling-compressed — a
  mechanism the registered monotone link did not anticipate. Verdict as it
  will appear in the paper: the profile's directional signal coincided with
  the first naive-metric reversal, but its registered predictive form
  failed its first prospective test (one hit, one miss, one pending).

### Reasoning census — deepseek graph lanes landed (3-model final)

All three models now measured; artifact refrozen. deepseek: passages 0.467
vs graph_a 0.548 (−0.085 [−0.153,−0.016]) and graph_c 0.574 (−0.131
[−0.195,−0.068]) — both graph conditions separated ABOVE passages, on the
model that lost with graphs on every other stratum. Final stratum verdict:
3/3 models have at least one graph condition separated above passages;
deepseek has both, plus its best condition is pointers (0.634; grounded
127 vs 106; over-refusals 21 vs 27).

Prospective profile prediction, final tally: grounded-correct lifts
an1 → an3 were gpt-oss +4 → +10 (hit), MiniMax +14 → +6 (miss,
ceiling-compressed at 148/169), DeepSeek −15 → +2 (hit). Two of three in
the predicted direction; the registered all-models clause still fires on
MiniMax, so the hypothesis stays DISCONFIRMED as registered and is
reported as two hits, one ceiling-compressed miss.
