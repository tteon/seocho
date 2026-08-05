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
