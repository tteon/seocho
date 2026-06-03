# Answerability diagnosis (#3) — the real bottleneck

Corrective follow-up to the cold review. Measures REAL record-count
answerability (not the answer-text proxy, which was wrong) on the 10-case
FinDER subset, and classifies each 0-record case's root cause.

## Result

**REAL answerability (≥1 retrieved record) = 3/10 = 0.30.**
(The earlier answer-text proxy reported 0.80 — wrong, because terse/prose
answers never say "no information" even when retrieval returned nothing;
the LLM answers from passed-through chunk text or priors.)

Graph WAS populated (65 nodes: Company 6, Person 4, Subsidiary 2,
Revenue/Metric 11, + Document/DocumentVersion/Chunk/Section 10 each).

## Root cause — NOT extraction-miss, it's cypher↔structure mismatch

All 7 zero-record cases have the entities present in the graph but the
generated Cypher targets a structure the extraction didn't populate:

| case | intent | recs | cause |
|------|--------|------|-------|
| 001 Apple HQ | relationship_lookup | 0 | Company 'Apple Inc.' present; HEADQUARTERED_IN→Location not matched |
| 002 MSFT revenue | financial_metric_lookup | 0 | revenue lives in Document text, not Company-REPORTED→Metric |
| 004 Alphabet chair | relationship_lookup | 0 | governance in Document text, not LED_BY→Person |
| 005 Meta settlement | financial_metric_lookup | 0 | in Document text |
| 006 Tesla auto revenue | financial_metric_lookup | 0 | in Document text |
| 007 JPM CET1 | financial_metric_lookup | 0 | in Document text |
| 009 Amazon subsidiary | relationship_lookup | 0 | Subsidiary 'AWS' present; query missed it |
| 003 / 008 / 010 | — | 1–7 | answered |

## Implication (cold)

1. The bottleneck is **cypher↔extracted-structure mismatch**, not
   extraction coverage and not graph answerability-in-principle. The
   facts are in the graph — mostly as Document/Chunk TEXT plus partial
   entity nodes — but text2cypher queries an idealized ontology shape
   (Company-REPORTED→Metric, Company-HEADQUARTERED_IN→Location) the
   extraction only partly produced.
2. Judge score is 0.70 while structured retrieval is empty 70% of the
   time ⇒ **the answers are coming from the passed-through chunk text /
   model priors, not the graph structure.** On this workload seocho's
   graph-memory layer contributes ~nothing to answer correctness.
3. This is WHY every query-lane sophistication (GOPTS cost-ranking,
   RouteProfile, multi-plan fusion, ontology grounding) measured null:
   there is no non-empty structured result to rank, route, fuse, or
   ground 70% of the time.

## Highest-leverage next move

Close the **extraction↔query structure gap** so structured retrieval
actually fires: either (a) make extraction emit the ontology shape the
query templates expect (Company→REPORTED→Metric with value/period;
Company→HEADQUARTERED_IN→Location), or (b) make text2cypher target the
structure extraction actually produces (incl. falling back to
Chunk/Document text retrieval as a first-class path). Until then, no
query-planning work can move answer quality, and the graph layer is
dead weight on this workload.

Raw per-case data: `answerability_diagnosis_raw.txt`.
