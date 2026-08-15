# ADR-0162: real-data interning validation against the MDM golden master

Date: 2026-08-15 · Status: accepted (measurement record)

## Context

ADR-0160/0161 measured interning on FinBench with *planted synthetic*
duplicates. hadry: validate on the real DozerDB where multiple models and
categories produced overlapping entities. The `mdmmaster` database provides both
the real duplicates and a ground truth: `SourceRef` nodes (raw extractions from
DeepSeek-V3.1 / gpt-oss-120b / MiniMax-M2.5 across categories risk/research/
compliance/…) each `DERIVED_FROM` a `GoldenEntity` (the MDM-consolidated
canonical). The golden clustering is the ground truth; `SourceRef.business_key`
is the MDM pipeline's own identity key.

## Method

`scripts/agentos/interning_real_mdm.py` (read-only, via the live `graphrag-neo4j`
container). Cluster the 114 SourceRefs by each policy's key and score against the
48 golden clusters (36 multi-source) with **pairwise** precision/recall/F1:
recall = of co-golden pairs, fraction sharing a key (does exact interning
collapse real duplicates?); precision = of key-sharing pairs, fraction co-golden
(does it avoid merging distinct entities?). Arms: `intern_name`
(`compute_node_identity`, name only — the real function), `intern_name_label`
(name+label), `business_key` (MDM's own key), `vector_bge` (bge single-link at
best-F1 threshold = an oracle ceiling for the semantic fallback).

## Result (114 SourceRefs, 48 golden clusters, 3 models)

| arm | precision | recall | F1 |
|---|---|---|---|
| **intern_name** (compute_node_identity) | **1.000** | 0.811 | 0.896 |
| intern_name_label | 1.000 | 0.755 | 0.860 |
| business_key (MDM's own key) | 1.000 | 0.764 | 0.866 |
| vector_bge @thr=0.82 (oracle) | 1.000 | 0.896 | 0.945 |

## What it confirms (the synthetic findings hold on real cross-model data)

1. **Exact interning = perfect precision on real duplicates.** `intern_name`
   never merges two distinct golden entities (P=1.000) across 114 real
   cross-model extractions. The collision-resistance claim (ADR-0160) holds on
   real data, not just planted homonyms.
2. **Exact interning has a real recall ceiling (~0.81), and so does the
   production MDM key (0.764).** The intern function misses ~1/5 of real
   duplicate pairs — and MDM's *own* `business_key` misses even more. Real
   missed cases (different models, same company):
   - "Delta Air Lines" vs "Delta"; "Pfizer Inc." vs "Pfizer";
     "Catalent, Inc." vs "Catalent" (legal-suffix / abbreviation)
   - "Chipotle Mexican Grill, Inc." vs "CHIPOTLE";
     "Enphase Energy, Inc." vs "ENPHASE" vs "ENPHASE ENERGY, INC."
   This is ADR-0161's suffix recall ceiling, reproduced on real surface
   variation — and independently confirmed by the fact that MDM needed a
   fuzzy/semantic layer *on top of* its exact key to reach the golden truth.
3. **The semantic fallback recovers what exact keys miss** (vector_bge R=0.896,
   F1=0.945 at P=1.000). This validates the ADR-0161 **hybrid** (intern for
   guaranteed precision, vector to lift recall) on real data — it is, in effect,
   what the production MDM pipeline already had to do.

## New real-data insight (synthetic data could not show this)

**Name-only interning out-recalls name+label across heterogeneous models**
(0.811 vs 0.755). Models disagree on the label — the same "CHIPOTLE" is a
`LegalEntity` for one model and an `Entity` for another — so adding the label to
the identity key *splits* real duplicates that the golden truth merged. Lesson
for the intern key over multi-model output: **do not over-specify the composite
key with fields models disagree on**; the label is not a reliable identity
component across heterogeneous extractors. (Precision stays 1.000 for name-only
here because the real entities have distinct names — no homonyms in this set.)

## Consequences

- Interning collapse/collision now has real-data backing (this ADR) alongside
  the scale-invariant synthetic result (ADR-0160) and the end-to-end retrieval
  effect (ADR-0161). The Tier-1 allocator claim (seocho-gzo) cites all three.
- Confirms the hybrid resolver (seocho-6l8): exact-key first (P=1.000),
  semantic fallback for the recall ceiling. The MDM golden pipeline is an
  existence proof that the hybrid is what real consolidation requires.
- Key-design guidance for `identity_keys` over multi-model output: prefer the
  stable, model-agnostic fields (name, normalized); avoid model-contested fields
  (label) — recorded for the ontology/import guidance.
