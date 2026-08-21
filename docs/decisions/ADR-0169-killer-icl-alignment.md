# ADR-0169: in-context specification vs enforced alignment (the killer experiment)

Date: 2026-08-16 · Status: accepted (measurement record) · seocho-41a, ties ADR-0168

## Context

Schema-in-context reframed as ICL of the contract; the guardrail as hard
enforcement. Two axes on the same live-graph agent task (finbenchl1), MARA
gpt-oss-120b (strong) + gemma-4-31B-it (weak):

- COMPOSITION (ICL dose): none / labels / full / full+examples
- ENFORCEMENT: soft (validator records verdict, does not block) vs hard (rejects
  → the model must re-emit = the guardrail repair loop).

Configs: {none,labels,full,full+examples}×soft + full×hard.
`scripts/agentos/killer_icl_alignment.py`. Conformance = fraction of emitted
queries passing the ontology validator; drift = soft queries that WOULD be
rejected but ran anyway. Companion scale-up (`ablation_l1_scaleup.py`, 8 Q, 3
models) is the cross-model correctness/token backdrop.

## Result — conformance is an ICL dose-response; examples close it

Soft arms (pure ICL, no enforcement):

| composition | gpt-oss conform | gemma conform | drift (gpt/gemma) | tokens |
|---|---|---|---|---|
| none | 0% | 0% | 6 / 8 | ~3.3–3.6k |
| labels | 0% | 0% | 6 / 6 | ~3.6–5.2k |
| full | 66% | 16% | 2 / 5 | ~12.6–13k |
| **full+examples** | **100%** | **100%** | **0 / 0** | ~13.4–13.9k |

- **The ontology alone (full) is not enough** — 66% (strong) / 16% (weak)
  conformance. Describing the contract lets a strong model infer the form,
  leaves a weak model mostly off-contract.
- **Worked examples take both models to 100% conformance, 0 drift.** Showing the
  exact conformant syntax (inline-map scope `{_workspace_id: $workspace_id}` +
  `LIMIT $limit`) is the ICL move that closes the gap the weak model could not
  bridge from description. This answers "what to put in context beyond the raw
  ontology": **worked examples of the exact conformant form.**

## Result — hard enforcement without examples is the worst path

full×hard (the current OS default posture):

| model | correct | conform | emitted (retries) | tokens | turns |
|---|---|---|---|---|---|
| gpt-oss | 5/6 (↓ from 6/6 soft) | 41% | 12 (2×) | 12.1k | 2.0 |
| gemma | 6/6 | 50% | 12 (2×) | 20.5k (max) | 2.0 |

Hard enforcement on top of a *described* (not demonstrated) contract doubles the
queries/turns (the repair loop), still only reaches 41–50% conformance (the
model keeps failing to infer the exact form even after rejection), burns the
most tokens, and can make a model that soft got right get *stuck* re-emitting
(gpt-oss 6/6 → 5/6). The repair loop is expensive and, without good examples,
ineffective.

## Killer conclusion

**Good in-context specification beats hard enforcement for conformance.** Teach
the exact form with examples → the first query conforms (100%), the repair loop
never fires, at ~half the tokens and one turn. The right design is
**examples-in-context (first-try conformance) + enforcement as a rarely-fired
safety net**, not enforcement-as-repair-loop. This explains the scale-up
mystery: the OS looked bad on MiniMax (4/8) and gemma (5/8) because it ran
full×hard (described + repair loop); full+examples makes both models 100%
conformant.

## The repair loop is multi-turn → a KV-cache candidate (hadry, 2026-08-16)

The guardrail repair loop is an **append-only multi-turn loop**: each retry
re-sends the entire growing prefix (stable schema head + prior queries +
rejections). So each retry re-prefills a prefix that is identical to the prior
turn's — exactly what prefix caching / KV reuse (vLLM automatic prefix caching,
SGLang RadixAttention, LMCache/Mooncake offload) eliminates. This is a *bigger*
KV win than the static schema prefix alone (ADR-0163 note / seocho-40j), because
the measured retry cost (19–26k tokens in the scale-up) IS repeated re-prefill of
a shared prefix. Honest scope: KV reuse cuts the repeated **prefill** (compute /
TTFT / cached-input on supporting providers); it does NOT reduce the number of
**retries** or the **decode** tokens. So two complementary levers on the
repair-loop cost: (1) **fewer retries** via examples/ICL — this ADR's result,
attacks the loop at the source; (2) **cheaper prefill per retry** via KV cache —
seocho-40j. They compose, and weak models (most retries) benefit most from both.

## Consequences

- Product: ship the governed agent with **worked examples** in its instructions
  (not just the schema block); prefer first-try conformance with enforcement as
  a backstop; relax the over-strict aggregate-LIMIT rule (seocho-6md).
- Paper: the ICL & specification-alignment framing now has data — conformance is
  an ICL dose-response, examples are the lever, and enforcement-as-repair-loop is
  the expensive path. Feeds the "in-context specification + enforced alignment"
  section and the KV/inference-cost story (seocho-40j).
- Caveat: n=6 questions, single-tenant benign data (so soft drift didn't hurt
  *correctness* — but drift is exactly what the L1 governance axes, ADR-0167,
  show is unsafe under multi-tenant/adversarial load). Scale-up + a deliberate
  off-schema/adversarial class is the follow-up.
