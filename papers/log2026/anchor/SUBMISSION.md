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
Enterprises increasingly build knowledge graphs from their documents with
LLM extractors, and in regulated domains such as finance the answers drawn
from those graphs must be verifiable, not merely plausible. The standard
prescription is to guide extraction with a domain ontology such as FIBO.
Yet whether the ontology improves the graph — and whether the graph then
improves answering — has rarely been measured with the schema as the only
variable and memorization controlled. We measure both, in a pre-registered
study on the FinDER financial-QA corpus: the same filings are extracted
under five schema conditions, from no schema to full FIBO, by three LLMs,
with the two decisive conditions replicated on 420 stratified cases. Three
results follow. First, the ontology does not improve extraction: it lowers
cross-model agreement on entity names and does not raise coverage of
answer-bearing facts; its measurable benefit is that schema violations
become detectable. Second, comparing facts by entity name — standard
alignment practice — hides most cross-model disagreements: anchoring each
extracted value to its source position instead finds 1.7-2.8 times as many
comparable facts and exposes that a quarter of anchored values carry
unit-scale errors. Third, standard QA scoring favors source text over the
serialized graph, but cannot tell evidence use from memory: separating
memorized answers, honest refusals, and grounded answers reverses the
comparison on two of three models, and adding provenance pointers alone
raises accuracy. Alignment should key on provenance rather than names;
ontologies earn their keep as validators, not extraction guides; and
evaluations of retrieval structure must be evidence-conditional. Three
pre-registered hypotheses failed and are reported as findings.
```

## Submission Type*

**Extended Abstract** (4 pages + unlimited references/appendix, non-archival).

Chosen 2026-08-06: the track's own call describes this paper — insightful
negative results, new ways of thinking, novel resources (the registration
ledger and artifacts). Non-archival means the full version remains free for
a better-fit venue. If the abstract was registered with Proceedings
selected, edit the Submission Type dropdown before the PDF deadline.

## Subject areas

- Knowledge Graphs and LLMs
- Graph ML Platforms and Systems
- Trustworthy Graph ML (evaluation methodology)

## Remaining form fields

Same values as the SDCR record (`../OPENREVIEW_SUBMISSION.md`): CC BY 4.0,
email sharing checked, data release checked, authors' real names in the form,
`Anonymous Authors` in the PDF.
