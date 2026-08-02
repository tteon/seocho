# Paper spec — problem, experiments, conclusions

Working contract for the LoG 2026 submission. Every row traces to a frozen
artifact under `outputs/evaluation/mdm_fedcat/`. Numbers here are the ones the
paper may claim; anything not here is not claimable.

Direction (decided 2026-07-30): the value of federating isolated knowledge
graphs is **verification and provenance**, not answer quality. The routing
result becomes the evidence for why answer quality is the wrong axis to sell on.

---

## 1. The problem

An organization does not keep one knowledge graph. Accounting, risk, and
governance teams each build their own, because each owns its extraction rules,
its provenance, and the authorization that decides who may read it. Those
boundaries are why the graphs are trusted.

Two questions follow, and only the second is ours:

- **Execution:** how does an authorized application read across databases?
  Already solved by engines with cross-database and multi-tenant support.
- **Semantics:** which views does a question need, and what happens when two
  views disagree about the same fact? Ours.

The failure this targets is not a worse sentence. It is a **silently wrong
number**: a single view reports "$59.4" where an independent extraction of the
same filing reports "$59.4 million", and nothing in a single-view pipeline can
notice.

## 2. What we claim, and what we do not

| # | Claim | Status |
|---|---|---|
| C1 | Independent extractions of one document disagree on values at a material rate | **measured** |
| C2 | Most of that disagreement is scale, not content | **measured** |
| C3 | A deterministic cross-view verifier surfaces those disagreements with no model call | **measured** |
| C4 | Cross-view verification is bounded by whether views name a fact the same way, not by routing | **measured** |
| C5 | Answer quality is the wrong axis: federation does not beat centralizing retrieval | **measured** |
| C6 | Selection failure, not answer synthesis, is what breaks the answer path | **measured** |
| N1 | Federation improves natural-query answer accuracy | **not claimed** |
| N2 | The conflict rate reflects ground-truth error rates | **not claimed** — no gold values |
| N3 | Results generalize past FinDER 10-K text | **not claimed** |

## 3. Experiments

Cost column: `$0` means no model or embedding calls.

| Purpose | Method | Cost | Result | Artifact |
|---|---|---|---|---|
| Does extraction diverge at all? | Four providers extract the same 5,703 cases; compare `(case, fact slug)` keys | $0 | **8.0%** of 187,450 keys exist in ≥2 providers; 172,482 in exactly one | `log2026-natural-conflict-v1` |
| Do comparable views disagree? | Numeric comparison on 18,281 comparable pairs, no injection | $0 | **23.5%** disagree (4,302 of 18,281) | same |
| What kind of disagreement? | Classify each by ratio and sign | $0 | scale 1e6 **1,866**; scale 1e3 **1,027**; different value 909; rounding 230; sign flip 209 | same |
| Does the shipped verifier catch them? | Feed real disagreeing pairs to `seocho.query.sdcr.verify_conflicts` | $0 | **60/60** surfaced | same |
| Is the contract safe under attack? | 31 fact-matched injections, protected-field probe | paid | 31/31 conflicts detected, 0/31 poison accepted, leakage 26/31 → **0/31** | `log2026-adversarial-answer-v3` |
| Are the views structurally different? | Identifier-first resolution, then PPR divergence vs matched cross-model null | $0 | AUROC **.746** vs .648 phrase control; improvement CI [.045,.146] | `log2026-entity-cleaning-ablation-v1` |
| Can divergence trigger collaboration? | Upper-tail rule over the matched null | $0 | No. Null saturates; every threshold is 1.000 | `log2026-clean-entity-network-v1` |
| Does the router pick the right action? | 80-query mixed suite, six baselines | $0 | Macro F1 **.981**; network tie-break moves 1 of 80, CI [0,.041] | `log2026-sdcr-selector-eval-v1` |
| Does it pick the right views? | 13 blind-validated cross-view questions | $0 | Both views covered **4/13**; coverage .538 | `log2026-full-finder-cross-view-v1` |
| Is that a retrieval or a routing failure? | Fixed 2,048-token budget across seven arms | $0 | Routed **.045** vs random-equivalent .134 at the same coverage; oracle .197 | same |
| Which, conditioned on outcome? | Split by whether the router covered the views | $0 | Hits **.147** vs baseline .083; misses **.000** | `log2026-capability-fallback-v1` |
| Does a fallback repair it? | Serve the capability team on a miss | $0 | .045 → **.152**, +.106 CI [.052,.164] | same |
| Does the repair reach answers? | Re-answer with three models, same harness | paid | Slot F1 up for all three; GPT-OSS +.067 CI [.008,.124] | same |
| Does federation beat centralizing? | Same 13 cases, three models | paid | **No.** All six intervals against centralization contain zero | `log2026-full-finder-cross-view-v1` |
| Does graph construction reach the answer? | 16-case, 256-cell prompt × ontology × model | paid | Serial indirect ≈ 0 for both treatments | `log2026-factorial-mediation-v1` |

## 4. Conclusions

1. **Cross-view disagreement is a real, large, and specific phenomenon.** 4,302
   natural conflicts, 67% of them scale errors. This is the work federation
   exists to do, and it needs no model to do it.
2. **The binding constraint on that work is naming, not routing.** At an 8.0%
   comparable-key rate, 92% of extracted facts cannot be checked against any
   other view no matter how good the router is. Improving the router cannot
   raise this ceiling; aligning extraction keys can.
3. **Answer quality is the wrong axis to sell federation on.** Centralizing
   retrieval is at least as good on every prose metric across three answer
   models, and graph-construction changes do not measurably reach the answer.
4. **Where the answer path does break, it breaks at selection.** A miss serves
   no evidence, so the router scored below random selection until a fallback was
   added. Expected quality is governed by miss rate and miss behavior.

## 5. Threats to validity

- **No gold values.** Disagreement is measured between extractions, not against
  truth. We cannot say which side is right, only that they conflict. C1–C3 are
  claims about consistency, never about accuracy.
- **Slug matching is exact.** The 8.0% comparable-key rate is a lower bound; a
  fuzzy matcher would find more pairs and possibly a different disagreement
  rate. Report it as a lower bound and say so.
- **One corpus, one document type.** FinDER 10-K text, four models, one
  extraction pipeline.
- **n=13 on the answer path.** Model-constructed and model-validated, four
  routing successes. Not a confirmatory answer comparison.
- **Adversarial set is synthetic and easy by construction.** It validates a
  contract, not a natural rate; the natural rate is the census above.
- **Issuer labels in the candidate pool are heuristic.** Three of 240 candidates
  provably merge two companies; the 13 evaluated cases were hand-checked.

## 6. Literature the argument needs, and does not yet cite

The current bibliography is 21 entries about routing, debate, adaptive
retrieval, and judge calibration. It positions the method. It does **not**
establish the problem. To support Section 1 and claims C1–C4 we need work on:

1. **Extraction consistency in knowledge-graph construction** — that independent
   runs or independent models over the same source produce divergent graphs.
   Without this, the 8.0% comparable-key rate reads as our pipeline's bug rather
   than a property of LLM extraction.
2. **Numeric and unit errors in financial NLP** — that scale errors are a known,
   consequential failure mode. This is what makes the 67% scale finding land.
3. **Provenance and conflict handling as an enterprise knowledge-graph
   requirement** — why isolation and attribution are kept deliberately, rather
   than being a limitation we invented.
4. **How multi-agent retrieval has been evaluated** — to support C5, that the
   field measures answer text and therefore has not reported this axis.

Until these are cited, the "why" rests on assertion. This is the next action.
