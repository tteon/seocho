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
Practitioners hand LLM extractors an ontology expecting better knowledge
graphs. We measure what the ontology actually buys in a pre-registered study
on financial filings (FinDER): five extraction schemas from schema-free to
full FIBO compared head-to-head, with the two decisive conditions replicated
at scale across three extractor LLMs and three stratified samples.
(1) Ontology guidance does not improve extraction: it reduces cross-model
agreement on entity names and does not raise coverage of answer-bearing
facts; its measurable contribution is detectability — schema violations
become checkable. (2) The standard alignment primitive is at fault: matching
facts by entity name hides most cross-model disagreements. Anchoring each
extracted figure to its unique source token yields 1.7–2.8x as many
comparable facts, and a quarter of anchored values turn out to be unit-scale
misreadings invisible to name matching. (3) Downstream, naive QA scoring
says gold passages beat serialized graphs. An evidence-conditional
evaluation — separating memorization, honest abstention, and grounded
answers — reverses that reading on two of three models, attributes the
residual gap to model-specific over-refusal, and shows that attaching
provenance pointers, which carry no content, raises accuracy on the same two
models. Every registered hypothesis carried its disconfirming outcome in
advance; two failed and are reported as findings. Exploratory results are
labelled as such and replicated under fresh registration.
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
