---
draws_on:
  - log2026.synthesis_validation.v1
  - log2026.composability.v1
---

# 1.5  The cross-category questions are constructed, and that is disclosed

## Why construction was the only route

Part of this study asks what happens when an answer needs facts from two
different parts of a filing. FinDER cannot answer that as it stands.

Its 5,703 cases include 386 with more than one reference, and every one
inspected is a single issuer's filing read in two places — a balance sheet
beside an income statement, a segment note beside an earnings table. The corpus
carries no issuer, company or filing column at all, so even grouping cases by
company requires inference.

So the questions were constructed: two single-reference questions from different
categories, paired when both name the same issuer. The alternative was to drop
the cross-category question entirely, and a disclosed construction is more
useful than a missing result. The limitation belongs in the abstract rather than
a footnote.

## The construction is validated, not asserted

The pairing rests on inferring an issuer from question text with a regex — the
last uppercase two-to-five letter token — and that step had no accuracy figure
attached to it anywhere. Both questions of every pair were therefore re-resolved
against a registry of 536 accepted tickers, independently of the regex that
built the pair.

| Check | Result |
|---|---|
| Both questions name a ticker in the registry | 211 / 240 (0.8792) |
| The regex agrees with the registry | 216 / 240 (0.9000) |
| **Both resolve to the same issuer** | **208 / 240 (0.8667)** |
| Fails: a question names no accepted ticker | 29 |
| Fails: the two questions name different companies | 3 |

Three pairs concern two different companies outright. No amount of labelling
rescues those and they are removed.

The constructed questions match the corpus in shape: both have a median length
of 11 words. The pairing never sees any model's output.

## Whether a pair is one question, decided without a judge

The second thing to establish is whether answering needs *both* components, and
this is usually left to human annotation. The available alternative — an LLM
panel — is not obviously better here: the judges in this project have been
measured disagreeing with each other, and a panel with no human anchor cannot
honestly be called independent labelling.

It does not need a judge. A pair is not composite when one component's gold
answer already holds everything the other contributes, and it is two adjacent
questions when the golds share nothing. Composite is the middle, and it is
decided by comparing what each gold contributes under three readings — stated
figures, named entities, content words — with a verdict of composite only when
all applicable readings agree.

Of 240 candidates, 189 are composite and 51 disputed. Combined with the issuer
check, **162 are usable, 0.6750**.

The 51 disputed all split the same way, composite by entities and content but
not by figures. That is a real disagreement about what counts as a contribution
and it is reported rather than resolved by choosing a favourite reading.

An earlier version of this counted a reading as voting *against* when the
material it needs is absent from one gold, which put 126 of 240 pairs in
"disputed" for the sole reason that one of their answers is prose with no
figures in it. That is a fact about the form of an answer, not about whether the
pair is composite. Inapplicable readings now abstain.

## What validation cannot do

It establishes that a pair concerns one company and that its two components
contribute different facts. It does not establish that anyone would ask the pair
as one question. That judgement needs an opinion, is left to a panel, and will
be reported as model-judged rather than folded in.

And none of it makes a constructed question a native one. FinDER contains no
natively multi-source question, and no validation changes that.
