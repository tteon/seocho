# Pre-registration — multi-agent adjudication over anchored disagreements
# (MA-), utilization-pruned FIBO (F-), external corroboration (EX-, design only)

Committed before any adjudication call. Prefix **MA-**.

## The setting no benchmark can give us

Cross-view disagreements located by provenance anchors carry their own
ground truth: the source token's value. s1 (schema-free) holds ~606
disagreement groups and s2 (FIBO) ~874, each a real conflict two extractors
produced on the same source number. Multi-agent adjudication strategies can
therefore be scored mechanically, with no judge and no gold annotation.

## Arms (per disagreement group; the adjudicator may return any value or abstain)

- **M0 · anchor rule, no LLM**: trust whichever view's value matches the
  source token; abstain if none does. $0. Its accuracy equals the share of
  groups where some view matches the source — the pick-only ceiling.
- **M1 · blind adjudication**: an LLM sees the disputed fact's names and the
  competing values with view labels, and decides. The multi-agent-debate
  proxy: deliberation without evidence.
- **M2 · provenance adjudication**: M1 plus the source window (±120 chars
  around the anchor). Reading, not deliberating.
- **M3 · schema adjudication**: M1 plus the declared class/property
  information of the disputed nodes (the ontology-guardrail-at-verification
  arm). No source window.

Adjudicator: Kimi K2.5 — the one model that contributed no extraction and no
answer anywhere in this study. Same prompt scaffold across arms; the
evidence block is the only difference. Accuracy: close() at 0.1% against
the source value, scale words applied.

## Hypotheses

- **MA-H1** · M2 beats M1 with separated intervals: provenance, not
  deliberation, does the work. *Disconfirmed if* blind deliberation matches
  reading the source.
- **MA-H2** · M3 does not separate from M1: type information does not fix
  value conflicts (types cannot see scales). *Disconfirmed if* the schema
  arm separates upward — an ontology-guardrail value extraction never showed.
- **MA-H3** · M2 exceeds M0's pick-only ceiling, because a quarter of
  disagreements are scale errors correctable by reading the window even when
  neither view matches the source. *Disconfirmed if* M2 ≤ M0 — in which case
  the honest engineering advice is "apply the anchor rule and skip the LLM".

## F — utilization-pruned FIBO (registered now, runs after s5)

Condition F: condition C's class list minus classes never instantiated in
the s2 extraction (dead classes), same 280 cases, three models, tag s6.
**F-H1**: pruning recovers name agreement toward B's level while keeping C's
detectability surface on the classes that remain. *Disconfirmed if*
agreement stays at C's level — fragmentation would then be caused by FIBO's
class GRAIN, not its size.

## EX — external corroboration (design registered, execution deferred)

Cross-SOURCE anchoring: the same fact anchored in a filing and in an
external document (news, later-quarter filing) gives cross-source
verification with the same mechanics as cross-model. Deferred past the LoG
deadline because external documents for FinDER's filing vintage need
collection and have no gold; recorded here so the design predates any data.

---

## Addendum (2026-08-06) — the ground truth was the discovery

Scoring M1 against the registered ground truth produced 0.005 accuracy, an
implausible number that triggered an audit BEFORE any verdict was recorded.
The audit found the registration's ground-truth definition defective and the
defect more informative than the experiment:

1. **Every one of the 1,482 anchored cross-view disagreements is a unit-scale
   split** (candidates differing by exactly 10^3/10^6/10^9within 1%); zero
   are genuine digit disagreements. Independent extractors never disagree
   about the printed digits — only about whether the table header's scale
   applies.
2. The registered ground truth (the source token's value) inherits the same
   ambiguity: the scale word usually lives in the table header, outside the
   tokenizer's 24-char trailing window, so `source_value` is the RAW printed
   number. M0's "perfect" score was circular and raw-biased; M1's 0.005
   means Kimi systematically applies domain priors and picks the scaled
   candidate — which the registered truth cannot arbitrate.
3. Consequently **MA-H1 and MA-H3 are VOID as registered** (not
   disconfirmed): the mechanical token truth cannot decide scale. What
   remains scoreable, registered now before scoring: the subset of
   disagreements whose case's GOLD ANSWER states the disputed figure — the
   answer's form arbitrates the scale. Arms are re-scored on that subset
   only, with its size reported. MA-H2 (schema arm ≈ blind arm) remains
   scoreable as a same-truth comparison since both arms face the same truth
   definition.
4. The G2 guardrail (explicit unit-scale slot) is hereby promoted: if 100%
   of cross-extractor value conflict is scale, the scale slot is not one
   guardrail among three — it is the whole ballgame for value agreement.
