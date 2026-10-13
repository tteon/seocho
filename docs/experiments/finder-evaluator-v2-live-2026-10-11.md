# FinDER evaluator v2 setup and live gate — 2026-10-11

Implemented opt-in six-dimension evaluation with evaluation-only gold references,
original domain/reasoning/type annotations, deterministic quote/arithmetic receipt
checks, and stratified offline report tables. The OpenAI Agents SDK remains the
baseline; Pi remains an experimental inference harness. Neither harness has shown
an aggregate quality advantage in the preceding one-case pilot.

The local original FinDER snapshot has 5703 records, 1085 reasoning=True and 4618
False. True occurs only in Financials (595) and Company overview (490). False counts
are Financials 395, Company overview 591, Accounting 491, Footnotes 953, Governance
718, Legal 490, Risk 490 and Shareholder return 490. True/type=None occurs 204 times;
False/type=Compositional occurs twice. Preserve these independent labels and empty
cells; the official [dataset schema](https://huggingface.co/datasets/Linq-AI-Research/FinDER)
defines the reasoning boolean separately from category and type.

One new standard inference and one detailed evaluator call completed on the server
against MARA DeepSeek-V3.1 for case 7e6c28fd (Financials, reasoning=True, Subtract),
seed 42, unchanged fixed six passages. Gold references and labels were evaluator-only;
all calls went through a fresh cache-off Recorder. No retry, graph service, new
retrieval or SDK cloud tracing. A 2048-output-token cap and two-call cap bounded
the gate; this is not matched against the previous 1024-cap pilot for performance.

Final answer: $633 million increase, about 38%, between 2022 and 2023. Judge:
correct; equivalence, support, citations, reasoning and units/periods pass;
abstention not applicable. Arithmetic receipt 2294 - 1661 = 633 passed. Four quote
checks returned true, false, false, true. The false checks are compressed table
text rather than exact original whitespace. These do not prove unsupported claims;
they show why model support and exact quote presence must remain distinct.
Operand-source semantics are still not deterministically verified.

Raw call audit and decision/realization audit both passed. No human calibration
or inter-judge agreement has been measured. This gate demonstrates evaluator
integration, not its reliability across domains. Judge and answerer used the same
model; correlation/self-preference remain limitations. The supplied dataset labels
are stratification annotations, not policy inputs to an ordinary inference router.

Python 3.12.13, OpenAI Python 2.45.0, Agents SDK 0.18.2, DuckDB 1.5.6; shared remote
server with no reserved hardware limits, no warmup. Remote service revision,
hardware and prices unavailable. No cost/latency superiority claim is made.
Ignored artifacts `outputs/agent-observability/evaluator-v2-20261011/` contain
inventory, separate public/private inputs, hashes, experiment, trials, Recorder
raw store, audits and reports. The server retains its isolated copies. Writer and
paid processes were stopped. Focused tests check gold/metadata isolation, invalid
arithmetic/quotes, case-normalized legacy verdicts and unknown versus false strata.
Full final CI status is recorded in the living ExecPlan.

Next validation is calibration and balanced within-stratum design comparison:
Financials and Company overview offer both T/F labels; other domains permit only
False comparisons in this snapshot. Add reference-count/operation subdivisions,
held-out companies and multiple seeds before selecting a router or best design.
