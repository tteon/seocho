# FinDER fixed-context five-design pilot — 2026-10-09

All five designs completed actual MARA runs on one FinDER question, using the same
original-source evidence, model, shared resource caps and final Answer schema.
A separately budgeted same-model evaluator marked all five correct and supported.
This is one case, not evidence of generalized quality, cost or latency superiority.

## Question, evidence and answer

Case `7e6c28fd` (Financials, Subtract):

> Change in operating earnings for MSI in 2022-23.

The official FinDER parquet and original 10-k.zip archive supplied the question
and source filing. Six original MSI passages were chosen once using question
terms. Gold text and expected answer were not used for ranking or agent context.
The evidence hash is `68034578b348d22933a041fc5bb5643a9aa0dcab0358ae6c226d441d8587b711`.

Every arm answered: operating earnings increased from **$1,661 million in 2022**
to **$2,294 million in 2023**, an increase of **$633 million** (about **38%**).
The source table in passage p69 independently supports those operands and the
reported $633 million/38% increase. Post-inference arithmetic gives
2294 - 1661 = 633 and 633 / 1661 × 100 = 38.1096%.

## Designs and observed calls

| Design | Planned subtasks | Model dispatches, including retries | Tool calls | Reported input + output tokens | Evaluator |
| --- | ---: | ---: | ---: | --- | --- |
| A. Standard | 0 | 1 | 0 | 6088 | correct |
| B. CoT prompt | 0 | 1 | 0 | 6090 | correct |
| C. Decomposition only | 2 | 4 | 3 | 27556 | correct |
| D. Single delegation | 3 | 8 | 4 | 52974 | correct |
| E. Multi delegation | 2 | 6 | 3 | 32613 + unknown failed-request usage | correct |

A performs direct generation. B adds private stepwise checking; no private chain
of thought is exposed or scored. C uses one Solver for planning, all subtask
work, and final generation; its local result-recording tools do not invoke agents.
D reuses one Specialist identity sequentially, with fresh task contexts, then
Supervisor generates the final answer. E uses separate Specialist identities
and Supervisor aggregation. Both receive typed findings, references and missing
fields from their Specialist agent tools. All agents see the same evidence.

D planned three tasks; C and E planned two. Therefore this is an end-to-end
comparison including decomposition, not an isolated test of scheduling. D's
delegated calls did not overlap. E's delegated calls overlapped by about four
seconds, demonstrating actual concurrent execution. This is not a speedup claim.
One E request needed an explicit 429 retry, with unknown usage retained. The
complete ledger and per-agent receipts are saved, including failure attempts.

All arms produced the same principal answer. On this simple, explicitly stated
source fact, additional decomposition/delegation introduced more model calls
without changing the answer. Larger, multi-reference/compositional samples are
needed before drawing conclusions about which design is preferable.

## Conditions and evaluation limits

MARA model: DeepSeek-V3.1. OpenAI Agents SDK: 0.18.2. OpenAI Python SDK: 2.45.0.
No database or runtime retrieval was used. Inference caps per arm: 24 model
requests, 24 tool invocations, 2048 output tokens per request, 100000 context
bytes, 1000000 estimated admission units and 120 seconds. Admission units are
request-size estimates, not provider-token ceilings. Each explicit 429 retry
reserves another common call; at most three attempts per turn with five/ten-second
waits are permitted. Transport retries and SDK cloud tracing are disabled.

Mode order was shuffled with seed 42: D, B, C, E, A. There was no warmup and one
completed trial per arm under the final protocol. Remote service version, model
revision and hardware/container limits are not exposed. Latencies in the JSON
are observations from this run, affected by overload and different task plans;
no throughput, scalability or production-readiness claim is made.

All candidates froze before the evaluator received the expected answer. The
same model evaluated correctness, units, periods, arithmetic and source support
at temperature zero, with its own three-request/ninety-second budget. All five
judgments completed; seven evaluator dispatches included two 429 retries.
Same-model self-preference and one-case evaluation remain limitations.
`answer_correctness` is kept null in deterministic metrics; judgments remain
separately labeled model judgments. A manual check confirms the main numerical
answer, not every possible semantic claim.

Exact gold-span coverage is **zero for all five arms**: the mapped gold reference
is in passages p91/p92, while the selected p69/p140 tables independently contain
the same needed values. Gold-source location matching does not credit alternative
support. This is retained explicitly and is not interpreted as an unsupported
answer. Input/citation ID validity and evaluator support checks are separate.
The evidence was not expanded after examining gold mapping.

## Preserved failures and reproducibility

First attempt: CoT completed; decomposition arms failed because ordered task-ID
rules were insufficiently stated. Standard received 429. The ID description and
instructions were corrected. Second attempt: A/C/E completed; D and B suffered
provider 429, and one evaluator request also failed. The final protocol added
uniform, budgeted 429 retries and reran all five arms, preserving prior artifacts.
These prototype attempts cannot be treated as repeated trials of one unchanged
protocol or silently omitted from reliability analysis.

Local ignored artifacts:

- outputs/collaboration-mara/finder-five-designs-live-20261009.json
- outputs/collaboration-mara/finder-five-designs-live-r2-20261009.json
- outputs/collaboration-mara/finder-five-designs-live-r3-20261009.json

No credential was written to source or reports. Source/dataset hashes, actual
answers, plans, partial results, events, instructions, judgments, limits and
usage receipts are in the final artifact. To repeat with a locally configured key:

```bash
uv run python examples/graphrag-collaboration/run_fixed_finder.py \
  --model DeepSeek-V3.1 --case 7e6c28fd \
  --output outputs/collaboration-mara/finder-five-designs-repeat.json
```

The graph retrieval experiments remain separate. This pilot does not add a
production query route or establish a universal coordination strategy.
