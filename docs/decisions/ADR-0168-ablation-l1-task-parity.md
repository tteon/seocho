# ADR-0168: ablation L1 task axis — does governance cost answer quality?

Date: 2026-08-16 · Status: accepted (measurement record) · seocho-41a

## Context

L1 (ADR-0167) showed the OS dominates the governance axes (0 leaks, full
disclosure, bounded concurrency). This is the companion axis hadry asked for:
does routing an LLM agent through the governed path DEGRADE its answers? Both
arms use the openai-agents SDK with a MARA-served model (gpt-oss-120b) — the SDK
is the substrate, SEOCHO fills its guardrail/session sockets. BARE = a raw
`run_cypher` tool, no ontology guardrail. OS = the governed session agent
(`Session.agent()`): ontology guardrail on the tool + row-cap + truncation
disclosure. Same live graph (finbenchl1), same 5 questions, deterministic
numeric gold (no LLM judge). `scripts/agentos/ablation_l1_task_parity.py`.

## Result (n=1 per question, temperature 0)

| question | gold | BARE | OS |
|---|---|---|---|
| company count | 100 | ok | ok |
| transfer count | 10,895 | ok | ok |
| flagged accounts | 1 | ok | ok |
| person count | 1,000 | ok | ok |
| accounts with owner_id=1019 | 5 | ok | **X** |
| **correct** | | **5/5** | **4/5** |
| **tokens** | | **3,389** | **18,138 (5.4×)** |

## Reading it (the honest trade, not "free")

- **Near-parity on correctness (4/5 vs 5/5).** On questions answerable within
  the ontology schema, the governed agent is as correct as the bare one —
  governance does not break the task.
- **The one miss is diagnostic, not flaky.** Transcript capture: on the
  owner_id question the guardrail first rejected the agent's query
  (`missing_parameterized_limit`), forcing a re-emit; the agent then produced a
  schema-conformant query that returned 0 (gold 5) — it was steered toward the
  ontology's modeled relationships and away from the raw `owner_id` property the
  answer needed. BARE, unconstrained, queried `WHERE n.owner_id = 1019` directly
  and got 5. This is the governance trade made concrete: **conformance (no
  off-schema queries — a safety feature) costs the answer when the answer lives
  in an off-schema property.**
- **Token cost 5.4×.** Two causes, both honest: the governed agent carries the
  full ontology schema in-context (a feature — it is why it stays conformant),
  and guardrail rejections trigger re-emit retries (observed). The bare agent
  runs a one-line instruction and a free tool.
- **Guardrail over-strictness surfaced:** it demanded a parameterized `LIMIT` on
  an aggregate `count(...)` that needs none — a real tuning item (feeds the
  guardrail/governance-preset work, seocho-6md).

## Consequences

- L1 is complete on both axes: OS **dominates governance** (ADR-0167) at
  **near-parity on task** (this ADR), with a stated cost — 5.4× tokens and a
  conformance-vs-raw-property trade that can miss an off-schema answer. The
  honest headline is "governance is near-free on correctness, at a token/latency
  cost and a schema-conformance trade," not "free."
- Follow-ups: relax the guardrail's LIMIT rule for aggregates (seocho-6md);
  fix the OS database routing so the session's database reaches the layer
  (seocho-933, worked around here); scale to more questions / models / repeats
  and add the harder off-schema class deliberately.
