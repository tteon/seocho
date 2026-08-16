# ADR-0187: text2cypher grounding via shared intern table + competency questions (seocho-ia4)

Date: 2026-08-16 · Status: accepted (core module) · seocho-ia4

## Context

hadry: the Cypher-generation agent's hardest moment is resolving the user request —
it can't find the entity, doesn't know the canonical id, or which query shape the
question wants — and then hallucinates or gives up. This is precisely where the shared
canonical namespace (SharedInternTable, ADR-0182/0183) and the ontology's competency
questions should shine.

## Decision

`src/seocho/query/intern_grounding.py`:
- **Mention resolution** (`resolve_mentions`): extract request mentions and resolve
  each against the shared intern table (the canonical entity address space), trying the
  full span and its capitalized sub-tokens. A resolved mention gives the Cypher a REAL
  canonical id to bind (grounded, not guessed). An **unresolved** mention is surfaced
  explicitly — the agent's "can't find entity" case becomes a routable signal (fuzzy /
  vector fallback), not a silent wrong Cypher.
- **Intent** (`rank_competency_questions`): rank the request against the ontology's
  competency questions by tf-idf cosine (dependency-free baseline; embeddings/bge is the
  richer swap, per the design principle) → the closest CQ's known query shape guides
  generation.
- `ground_request` combines both into the context a Cypher-gen prompt consumes, plus a
  `resolution_rate`.

## Consequences

- text2cypher is grounded in what ACTUALLY exists (the intern namespace) + what the
  ontology is DESIGNED to answer (competency questions) — directly attacking the
  intent-extraction/entity-resolution difficulty. The shared intern table's cross-model
  agreement (ADR-0183) means this grounding is model-agnostic.
- Honest scope: exact-name resolution (name variants miss → the same boundary-1 ceiling,
  now surfaced as `unresolved` for fuzzy routing); tf-idf intent baseline (semantic/bge
  next). +4 tests. Follow-ups: wire the grounding into the live cypher-gen prompt + the
  repair loop (now that PR #542 lets the repair agent see the plan); vector fallback for
  unresolved mentions.
