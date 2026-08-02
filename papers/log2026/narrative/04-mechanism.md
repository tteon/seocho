---
draws_on:
  - log2026.merge_key_reality.v1
  - log2026.provenance_keying.v1
  - log2026.fact_anchors_summary.v1
  - log2026.verification_value.v1
  - log2026.routing_ceiling.v1
---

# 1.4  The alignment key, not the vocabulary, is what fails

Four results, all negative, turn out to be one result.

Identifiers are invented by the model and match the entity's own name between
0.0598 and 0.4247 of the time. Giving the extractor an ontology lowers agreement
rather than raising it, reliably. Views hold much the same figures under
different names. And cross-view verification has almost nothing to rule on.

Each is a failure of the same decision: **matching on the name**, which is the
least reliable thing an extractor produces.

## What the failure costs, at its sharpest

Verification is where the shortage becomes total. Facts that two or more views
hold *and* that bear on the answer:

| Condition | Facts only one view holds | Answer-relevant and verifiable |
|---|---:|---:|
| no ontology | 309 | 6 |
| real FIBO | 611 | 4 |
| FIBO + synonyms | 602 | 3 |
| FIBO + hierarchy | 412 | 5 |

Three to six, against hundreds. Nothing can be concluded about whether refusing
to serve a disputed fact is worthwhile, because there are almost no disputed
facts to refuse. Section 1.3's ceiling of about 0.20 is generous; restricted to
facts that matter, it is under one percent.

Selection fares no better. Given the graphs as they are, a perfect router — one
that sees the answer before choosing — reaches 0.4113 under real FIBO where
simply always querying the same view reaches 0.4040. Under no ontology the two
are identical at 0.3787. Querying every view reaches 0.4241 and 0.3863
respectively, so one view already holds nearly all of what three hold. There is
no room for a method.

## What does not fragment

The figure does not. That is why one view has most of the numbers.

Neither does the source. Two models reading one sentence read one sentence,
whatever they then call what they found.

So facts are keyed by their anchor in the source text instead. Of 2,500
extracted figures, 1,975 could be located at a unique numeric token in their
case's own reference passages — an anchor rate of 0.79. The 525 that could not
are counted, not dropped: attribution recovered after the fact does not reach
everything.

| Condition | Pairs by name | Disagreements | Pairs by anchor | Disagreements | Invisible to name matching |
|---|---:|---:|---:|---:|---:|
| no ontology | 94 | 55 | 121 | 50 | 26 |
| **real FIBO** | **30** | **2** | **183** | **87** | **87** |
| FIBO + synonyms | 29 | 2 | 177 | 79 | 79 |
| FIBO + hierarchy | 101 | 61 | 187 | 97 | 52 |

Under real FIBO, six times the comparable pairs and forty-three times the
detected disagreements — and every one of those 87 disagreements was invisible
to name matching, because the two views had given the fact different names.

## What the invisible disagreements are

A quarter of anchored figures matched their source only after rescaling: 279 at
a thousandfold difference and 213 at a millionfold. Those are models applying,
or declining to apply, a table's units.

Two models read the table as printed and one applied its "in thousands" header.
The values differ by a factor of a thousand. One called the fact `sales` and the
other `sales 2022`, so no name key ever placed them side by side.

Which of the two readings is right depends on the header, and that is the point.
This is the conflict a verifier exists to surface, and the structure as it
stands cannot see it.

## The reading

An ontology governs class names. What fails to align is the instance and its
units. Adding classes adds ways to slice one sentence, so the names fragment
further — which is why more vocabulary made agreement worse rather than better.

That last sentence is interpretation and not measurement. The control that would
confirm it — seventy classes that are not FIBO's, separating "more classes" from
"FIBO's classes" — has not been run.

## What this does not establish

An anchor is a unique numeric coincidence, not a provenance record written
during extraction. Two unrelated facts sharing one figure would be attributed to
the same token where only one occurrence exists, which is why ambiguous matches
are dropped and why the anchor rate is reported beside every result. Only
figures can be anchored at all.
