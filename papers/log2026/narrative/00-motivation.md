---
draws_on:
  - log2026.fact_anchors_summary.v1
  - log2026.provenance_keying.v1
  - log2026.arm_results.v2
  - log2026.synthesis_validation.v1
cites_published:
  - 43.88
  - 0.2
  - 0.5
  - 2008
  - 6611
  - 2127
---
# Why this problem, before any of the machinery

The study has been written from the inside out — measurements first, and the
reason for wanting them assumed. That reads as a set of experiments in search of
a subject. This is the argument that should come first, built from the industry
down to the technique, with the literature carrying the parts that are not ours
to claim.

---

## 1. The industry created this problem and then named it

FIBO exists because of the 2008 crisis. It was started by people in data
governance who found that institutions could not aggregate their own exposure —
not because the numbers were missing, but because the same instrument was called
different things in different systems, and nothing declared that they were the
same instrument. The response was to standardise terminology for regulatory
reporting: a formal ontology, in OWL, maintained by the EDM Council, whose
stated purpose is "data consistency and comparability."

That history matters to a paper about whether an ontology helps. The ontology
was not proposed by researchers looking for an application. It was the
industry's own answer to a failure that cost it a great deal, and it encodes a
specific belief: **that shared meaning in finance has to be declared, because it
cannot be inferred from the text.**

Our study tests that belief in a setting it was never asked about — automated
extraction by language models — and the result is not what the belief predicts.
That is worth reporting precisely because the belief is well-founded and widely
held.

## 2. Financial text has a property that breaks the usual assumptions

Most work on retrieval and extraction assumes a sentence means what it says.
Financial statements do not work that way. A line reading `Sales 2,590,278`
means two and a half million dollars or two and a half billion depending on a
header several rows above it, and the header is not in the sentence. The same
figure printed once can be read two ways, both defensible, only one intended.

The literature has measured what this does to models. On financial numerical
reasoning, when a step needs a constant that is a matter of convention rather
than something stated in the document — that a basis point is 0.01%, that a
million is a thousand thousands — accuracy falls to **43.88%**. Benchmarks built
since have had to specify units, percentage formats, signs and decimal places
explicitly, and enforce error margins as tight as 0.2%, because at the magnitudes
involved the difference between a correct and an incorrect reading can be the
same size as the rounding unit itself.

This is the domain property that makes the problem specific rather than generic.
The failure is not that the model cannot find the number. It is that the number
does not carry its own meaning, and the meaning lives somewhere the retrieval
never looks.

Our own data shows the same thing from the other side. Of the figures we could
attribute to a place in the source text, **a quarter matched only after
rescaling** — 279 at a thousandfold difference, 213 at a millionfold. Those are
models applying, or declining to apply, a table's units. Three models reading
one printed number produced values differing by a factor of a thousand, and each
of them was locally reasonable.

## 3. What an ontology is supposed to do about it

An ontology is exactly the instrument for meaning a local passage does not
carry. It declares, ahead of any particular document, what classes of thing
exist and what may be said about them — so two readers, or two extractors, can
converge without coordinating. That is the whole proposition, and it is why the
industry chose it.

So the hypothesis under test is not arbitrary. It follows from the domain: if
financial figures fail to align because their meaning is not local, then
declaring the meaning in advance should make them align. FIBO is the declaration
the industry already agreed on. Applying it and measuring whether extraction
converges is the obvious experiment, and to our knowledge nobody has run it on
independently built graphs.

## 4. The graph is the means, not the claim

A knowledge graph enters here as the place declared meaning can be materialised
and provenance attached to a value — not as a retrieval technique competing with
embeddings. Recent work on knowledge-graph augmentation for financial QA states
the requirement in the same terms: the task "requires high provenance, numerical
fidelity, and regulatory compliance," and graph augmentation "offers verifiable
structured evidence." A graph makes it possible to say which document a served
figure came from and which other view disagrees, which no ranked passage list
can do.

The same literature names the price, and names it precisely: graph augmentation
"introduces failure modes like entity linking errors, relation noise, and
temporal misalignment." Those three are known. What is not established is how
large they are when the graph is built by a language model rather than curated,
or whether an ontology reduces them.

That is the gap this study occupies. The first of the three failure modes —
entity linking — turns out to dominate, the ontology does not reduce it, and the
reason is structural rather than incidental.

## 5. The argument the results actually support

Stated as a chain, with what is measured and what is cited kept apart:

1. Financial figures do not carry their meaning locally. *(literature; and our
   quarter of figures needing a scale correction)*
2. The industry's answer is to declare meaning in advance, which is what FIBO
   is. *(literature)*
3. So an ontology should make independently built graphs agree. *(hypothesis,
   pre-registered)*
4. It does not. Extraction under real FIBO agrees **less** than extraction under
   no schema at all, and the difference is larger than sampling noise. *(ours)*
5. Because an ontology governs class names, and what fails to align is the
   instance and its units. Adding classes adds ways to slice one sentence, so
   the names fragment further. *(ours)*
6. What does align is where a figure came from. Anchored to its source token,
   the same data yields six times the comparable pairs and forty-three times the
   detected disagreements — and every one of those disagreements was invisible
   to name matching. *(ours)*

Point 4 is the finding a reader will not expect. Points 1 through 3 are why they
should have expected the opposite, and they are why the finding is worth
publishing rather than embarrassing.

---

## On the synthesised questions, which we would rather not have needed

The cross-category part of this study asks what happens when an answer requires
facts from two different parts of a filing. FinDER cannot answer that as it
stands. Its 5,703 cases include 386 with more than one reference, and every one
we inspected is a single issuer's filing read in two places — a balance sheet
beside an income statement, a segment note beside an earnings table. The corpus
carries no issuer, company, or filing column at all, so even grouping by company
requires inference.

So the questions were constructed: two single-reference questions from different
categories, paired when both name the same issuer. We report this rather than
present the result as native, and we validated the construction rather than
assert it:

| Check | Result |
|---|---|
| Both questions name a ticker in the 536-entry accepted registry | 211 / 240 (87.9%) |
| The regex that built the pairs agrees with the registry | 216 / 240 (90.0%) |
| **Both questions resolve to the same issuer** | **208 / 240 (86.7%)** |
| Fail: a question names no accepted ticker | 29 |
| Fail: the two questions name different companies | 3 |

The synthesised questions match the corpus in length (median 11 words each), and
the pairing never sees any model's output. Three pairs concern two different
companies and no amount of labelling can rescue those; they are removed.

What validation cannot do is make a constructed question a natural one. A
separate human adjudication decides whether each surviving pair reads as one
question or as two stapled together, and both the approval rate and the
rejections are reported. The alternative was to drop the cross-category question
entirely, and we judged a disclosed construction more useful than a missing
result — but the limitation is real and belongs in the abstract, not a footnote.

---

## Sources

- [FinanceReasoning: Benchmarking Financial Numerical Reasoning](https://arxiv.org/html/2506.05828) — accuracy on convention-dependent constants; unit, sign and decimal specification; 0.2% error margin
- [Evaluating LLMs' Mathematical Reasoning in Financial Document Question Answering](https://arxiv.org/html/2402.11194v3)
- [FinVerBench: Benchmark Validity and Calibration in LLM Financial Statement Verification](https://arxiv.org/html/2605.29586) — rounding-magnitude error analysis
- [FIBO — EDM Council](https://edmcouncil.org/frameworks/industry-models/fibo/) and [the FIBO specification](https://spec.edmcouncil.org/fibo/) — origin after 2008, purpose, OWL and description-logic basis
- [Financial Industry Business Ontology: architecture, use cases and implementation challenges](https://globalfintechseries.com/featured/financial-information-business-ontology-fibo-architecture-use-cases-and-implementation-challenges/)
- [FinReflectKG — HalluBench: GraphRAG Hallucination Benchmark for Financial QA](https://www.researchgate.net/publication/403071098_FinReflectKG_--_HalluBench_GraphRAG_Hallucination_Benchmark_for_Financial_Question_Answering_Systems) — provenance and numerical fidelity requirements; entity linking, relation noise and temporal misalignment as known failure modes
- [Why Neighborhoods Matter: Traversal Context and Provenance in Agentic GraphRAG](https://arxiv.org/pdf/2605.15109)
