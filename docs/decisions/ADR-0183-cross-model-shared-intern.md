# ADR-0183: cross-model + cross-session shared intern table — the OS memory test

Date: 2026-08-16 · Status: accepted (measured) · seocho-ia4

## Context

hadry's OS test: give the SAME ontology to DIFFERENT models and check whether they
populate the SAME canonical addresses in ONE shared intern table — "이게 바로 정말
OS니깐요". An OS memory manager IS many heterogeneous clients over one governed heap;
here the clients are different LLM families. Builds on the SharedInternTable (ADR-0182).

## Decision

- **Cross-session**: `SharedInternTable.persist()/load()` — the canonical namespace
  is a shared file that survives the process, so later sessions / different model
  runs intern INTO the same address space (the allocator's heap outlives one process).
- **Cross-model experiment** (`scripts/agentos/e2e_cross_model_intern.py`): the same
  ontology (finance-compliance, identity_keys=["name"]) + same corpus (FinDER subset,
  10 docs, real companies), one shared intern table; three model families extract with
  the same ontology-typed prompt and intern into the shared namespace.

## Result — 3 models, one ontology, one shared namespace

MiniMax-M2.7 · gpt-oss-120b · gemma-4-31B-it (via MARA):

| metric | value |
|---|---|
| shared canonical namespace | 23 entities |
| **agreed by ALL 3 models** | **15 (65%)** |
| agreed by ≥2 models | 17 (74%) |
| unique to one model | 6 (26%) |
| **interning collapse (table hits)** | **35** cross-model+cross-doc folds |
| per-model canonical entities | 19 / 18 / 18 |

Three different model families, given the same ontology, **independently interned to
the same canonical addresses for 65% of entities** (74% for ≥2) — `company|apple inc.`,
`company|alphabet inc.`, `company|bank of america`, … The ontology functions as a
genuine **shared type system + canonical address space across models**: the OS claim
embodied — heterogeneous clients (models/agents), one governed canonical heap.

## Honest limits

- The 26% divergence + within-agreed name variants (`berkshire hathaway` vs
  `berkshire hathaway inc.` both present) are the SAME boundary-1 recall ceiling
  documented for single-model interning (ADR-0160/0161) — surface variation →
  distinct canonical address — now visible ACROSS models. Exact interning is
  guaranteed on the normalized name; the residual needs the vector/fuzzy fallback.
- The corpus is small and finance-typed; agreement on a larger, more diverse corpus
  (and with the cold-start upper-ontology instead of a hand ontology) is the follow-up.

## Consequences

- New paper measurement (Resource/Execution + Foundations): a shared, persisted,
  workspace-scoped intern table gives cross-model canonical agreement — the concrete
  "OS memory for agents" evidence. Composes with the concurrent extraction (ADR-0182)
  and the RCU/EBR reclamation discipline (ia4.3/.4).
- Follow-ups: larger/diverse corpus; cross-model agreement under the cold-start upper
  ontology; a fuzzy-fallback pass to fold name variants (raise the 65%).
