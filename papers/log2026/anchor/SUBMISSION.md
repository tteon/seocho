# OpenReview submission record — LoG 2026, second paper (the anchor study)

Register the abstract **today** (extended deadline: August 5, 2026 AoE).
The PDF follows by August 7, 2026 AoE. Same form as the first submission;
this is a separate submission, not a revision of the SDCR paper.

## Title*

```
Anchor, Don't Name: What Ontology Guidance Actually Buys in LLM-Built Knowledge Graphs
```

## Keywords*

```
knowledge graph construction, ontology-guided extraction, entity alignment, provenance grounding, retrieval-augmented generation, LLM evaluation, financial question answering, negative results, pre-registration
```

## TL;DR

```
Giving an LLM extractor an ontology does not improve the graph it builds — it makes bad extractions detectable; aligning facts by source provenance instead of entity name reveals the disagreements that matter, and evidence-conditional evaluation reverses the text-beats-graph reading downstream.
```

## Abstract*

```
Do large language models build better knowledge graphs when they are given a
domain ontology? We test this on financial filings (FinDER) with a
pre-registered design: the same documents are extracted into graphs under
five schema conditions — from no schema at all to the full FIBO financial
ontology — by three LLMs, with the two decisive conditions replicated on 420
stratified cases, so the schema is the only thing that changes. The ontology
does not improve extraction. It lowers agreement between models on entity
names, and it does not increase how many answer-relevant facts the graph
captures. Its measurable benefit is validation: schema violations become
detectable. We then ask why models disagree. Comparing facts by entity name,
the standard practice, misses most of the disagreements. Instead, we anchor
extracted values to the exact position of their source numbers in the
document. This provenance-based alignment finds 1.7-2.8 times as many
comparable facts, and shows that a quarter of anchored values carry
unit-scale errors (e.g., a figure reported in thousands recorded as ones)
that name matching cannot see. Finally, we use the graphs to answer
questions. Under standard scoring, giving the model the source text wins or
ties against giving it the graph. But standard scoring cannot tell whether
the model used the evidence or its memory of these public filings.
Separating memorized answers, honest refusals, and answers grounded in the
served evidence reverses the picture: the graph matches or beats the text on
two of three models, and adding provenance pointers — position references
only, no text — raises accuracy on the same two models. Three pre-registered
hypotheses failed and are reported as findings; exploratory results are
labelled and replicated under fresh registration.
```

## Submission Type*

**Proceedings** (9 pages, PMLR archival).

## Subject areas

- Knowledge Graphs and LLMs
- Graph ML Platforms and Systems
- Trustworthy Graph ML (evaluation methodology)

## Remaining form fields

Same values as the SDCR record (`../OPENREVIEW_SUBMISSION.md`): CC BY 4.0,
email sharing checked, data release checked, authors' real names in the form,
`Anonymous Authors` in the PDF.
