# ADR-0170: shipping worked examples eliminates the guardrail repair loop

Date: 2026-08-16 · Status: accepted (measurement record) · seocho-41a/6md, validates #524

## Context

ADR-0169 (killer) showed, in a bespoke harness, that worked examples take
conformance to 100% and remove the guardrail repair loop. #524 put ontology-
derived examples into the SHIPPED governed agent (`SeochoOS.build_agent`). This
validates that on the production path — the real `Session.agent()` — reading the
guardrail's own ledger (rejections = repair-loop turns) after each run.
`scripts/agentos/validate_os_examples.py`, live finbenchl1, MARA gpt-oss-120b
(strong) + gemma-4-31B-it (weak), 6 deterministic questions.

## Result

| model | OS (examples) correct | BARE correct | guardrail rejections | tokens OS / BARE |
|---|---|---|---|---|
| gpt-oss-120b | **6/6** | 5/6 | **0** | 14.8k / 4.2k |
| gemma-4-31B-it | **6/6** | 6/6 | **0** | 15.2k / 4.0k |

- **Zero guardrail rejections on both models** — the repair loop never fires. The
  shipped examples make the first query contract-conformant every time, on a weak
  model as well as a strong one. This is the killer's 100%-conformance result,
  now on the real `Session.agent()` path.
- **Correctness matches or beats BARE** (OS 6/6 both; BARE 5/6 on gpt-oss). The
  earlier OS underperformance — scale-up MiniMax 4/8, gemma 5/8 (ADR-0169) — was
  the *missing-examples* config (described-not-demonstrated + hard enforcement =
  repair loop), not a governance cost. With examples, it is gone.
- **The token cost is now purely the schema prefix.** OS ~15k vs BARE ~4k (~3.6×),
  but the retry component is 0 — so the residual is the stable ~756-token schema-
  in-context prefix × turns, not repair-loop churn. This cleanly separates the two
  cost causes (ADR-0169): examples killed cause #2 (retries); cause #1 (the stable
  prefix) is exactly the KV-offload candidate (seocho-40j), and it is a *cacheable
  prefix*, not recomputed work the model does.

## Consequences

- Closes the (c) loop: worked-examples-by-default (#524) is validated end-to-end
  — governance at parity-or-better correctness with **no repair loop**.
- The "governance is expensive" reading is now precisely bounded: the only OS
  overhead left is a stable, shared, cacheable schema prefix (seocho-40j), not
  the retry churn that sank weak models before.
- Feeds the paper's in-context-specification + enforced-alignment section
  (ADR-0169): examples for first-try conformance + enforcement as a
  rarely-fired safety net — here it fired zero times.
- Caveat: n=6, single-tenant benign data; scale-up (more Q/models/repeats +
  adversarial + off-schema class) remains the follow-up.
