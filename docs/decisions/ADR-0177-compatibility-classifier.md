# ADR-0177: typed compatibility classifier → live freshness signals (seocho-ia4.2 + ia4.6)

Date: 2026-08-16 · Status: accepted · seocho-ia4.2, ia4.6

## Context

ia4.6 (ADR-0176) proved a bounded-staleness freshness policy dominates always-warn
and always-block on the refusal ROC — but on SYNTHETIC, hand-assigned relevance and
distance. To promote it to a live measurement the signals must be DERIVED from real
ontology version diffs. The blocker: `diff_ontologies` marks any changed node/rel as
`breaking` (a false-major — adding an optional property is flagged breaking), so it
cannot separate reconcilable drift from answer-invalidating drift.

## Decision

`src/seocho/ontology/compatibility.py::classify_ontology_change` — schema-registry
typed classification of every change atom into BACKWARD / FORWARD / BREAKING, purely
structural (no DL reasoner; DL stays offline per ia4.7):
- add optional prop / node / rel, loosen cardinality, alias → BACKWARD
- add required/unique prop, retype, tighten cardinality, narrow domain/range,
  remove node/rel/prop → BREAKING/FORWARD (answer-invalidating)
Exposes `breaking_labels` (label granularity) and `breaking_properties` (label,prop
granularity). `semver_distance` (ia4.3) supplies the staleness magnitude.

## Result — live refusal-ROC (signals from the real classifier)

`scripts/agentos/ablation_freshness_live.py`: a real v1→v2 ontology diff (add
required prop, retype, remove prop, tighten cardinality, + compatible add-optional /
add-node), a property-level ground truth (a query is invalid-if-served iff it READS
an invalidating property or touches a structurally-broken label), and five policies:

| policy | under-refusal | over-refusal |
|---|---|---|
| always_warn | 100% | 0% |
| always_block (ia4.1 binary) | 0% | 100% |
| fresh_OLD (diff_ontologies false-major, label) | 0% | **83%** |
| fresh_label (ia4.2 breaking_labels) | 0% | **50%** |
| fresh_prop (ia4.2 breaking_properties) | 0% | **0%** |

The classifier's real judgment sets the frontier: the OLD signal marks a
compatible-only-changed label (`Team`, +optional prop) as breaking → 83% over-refusal;
ia4.2 correctly classes it compatible → 50%; property-level relevance → 0/0. Non-
tautological: ground truth is at property/value granularity, the label-level signals
are coarser, so over-refusal shrinks strictly with signal fidelity, all at 0
under-refusal. Freshness dominates both fixed corners.

## Consequences

- ia4.6's freshness policy now runs on REAL ontology-diff signals; ia4.2 (the
  classifier) is the signal source and independently fixes the `diff_ontologies`
  false-major for the publish-time compatibility gate.
- **Honest scope:** "live signals" = signals derived from real ontology version
  diffs. FULLY live (real materialized data + real re-answered queries on DozerDB)
  is the pending e2e run — this ablation's ground truth is a property-level model of
  answer-invalidation, not measured answers.
- 8 + prior tests; Long-Horizon + Trust/Safety tracks. Follow-ups: wire the classifier
  into the publish gate (refuse incompatible bump vs certified) and into the ia4.1
  query barrier (relevance-scoped block); version chain / RCU (ia4.3 full).
