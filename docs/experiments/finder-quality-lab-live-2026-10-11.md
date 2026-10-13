# FinDER failure, coordination and robustness pilot — 2026-10-11

Implemented a Recorder-backed quality loop with controlled synthetic data,
model/interface failure attribution, shared-plan coordination, budget and prompt
interventions, immutable-record checks and champion/challenger observations.
The pilot executed 54 trials and exactly 96 model requests including evaluators.
Twelve trials completed evaluation; 42 failed and remain unknown for quality.
No automated champion promotion or human gold acceptance occurred.

## Protocol and environment

Actual MARA calls ran in the isolated Tailscale directory on hardy-lab, without
modifying the server's existing source checkout. Python 3.12.13, OpenAI Agents SDK
0.18.2, openai 2.45.0 and DuckDB 1.5.6. No warmup; trials sequential with up to two
parallel workers/reviewers inside a run. No graph backend, SDK cloud tracing,
automatic retry, caching or new retrieval. Provider revision, hardware/resource
limits, monetary prices and hidden reasoning usage are unavailable; this is not
throughput or production-readiness evidence.

Original FinDER cases: 7e6c28fd (Financials, True, Subtract), 1303017a (Financials,
False, None), and 4e13063f (Company overview, True, Compositional). Their question-only
selected fixed six passages, raw boolean labels, private gold and original filing
manifests were retained. The initially proposed 8271ad85 was not eligible under the
existing conservative ticker resolver; its question was not rewritten to force it.

Initial preparation produced nine controlled variants per case (27 candidates).
The final generator additionally supports a fictional wrong-company/period
distractor; a separate v2 preparation has 30 candidates and pending human-review
queues. Existing executed artifacts were not regenerated. Position changes preserve
exact source inventory; anchors use the question, not gold. Empty-evidence and new
fictional numeric questions have changed gold contracts. The instruction wrapper
is not a diverse semantic paraphrase generator. Document/company groups share splits;
pilot_all is exploratory rather than heldout evaluation. Source and artifact hashes
are retained. Gold and reference labels are evaluator-only.

## Separate live cohorts

| Cohort | Trials | Calls | Completed evaluations | Main observation |
|---|---:|---:|---:|---|
| Five-model initial pilot | 32 | 45 | 1 | V3.1 rate limits and MiniMax schema failures |
| V3.2 follow-up | 16 | 29 | 6 | Several usable robustness observations, persistent service gaps |
| MiniMax output-contract challenger | 2 | 3 | 1 | Explicit prompt schema recovered the interface |
| gemma coordination follow-up | 4 | 19 | 4 | All four coordination modes executed and audited |

The catalog returned DeepSeek-V3.1, DeepSeek-V3.2, MiniMax-M3, gemma-4-31B-it and
gpt-oss-120b. All five were actually exercised on the three original cases in the
first cohort. Eight failures occurred after successful inference at the V3.1
evaluator (V3.2: three, gemma: two, gpt-oss: three); these are not answer-model
mistakes. V3.1 failed all twenty assigned trials at inference with HTTP 429.
MiniMax-M3 returned nonconforming JSON twice and a provider HTTP 400 reporting JSON
truncation once. gemma's MSI subtraction evaluation completed as correct. This
cohort does not support a model ranking. Evaluator model changes are explicit
protocol amendments; scores are not pooled across evaluator cohorts.

## An observed interface improvement

MiniMax-M3 was rerun on 7e6c28fd at seed 42 with the same 1024 inference output cap,
unchanged sources and a fixed V3.2 evaluator cap of 2048. The API-schema control
failed again. A challenger with the schema explicitly embedded in instructions,
SDK API output schema disabled and local raw-JSON validation completed with a
correct verdict. The changed factor is output_contract; it is not pure context
position or budget expansion. The observation establishes one interface recovery,
not general financial quality improvement. Champion promotion remains disabled.

## Coordination evidence and limits

The gemma cohort used identical two-task public plans, fixed evidence, model,
per-call cap, per-run call ceiling and V3.2 evaluator. All four conditions completed:
independent parallel, sequential context, peer review, and adaptive review.
Sequential delivery passed the first worker's actual findings to the second worker.
Peer review delivered two peer contexts and completed two source-checked reviews.
The adaptive supervisor chose zero additional reviews, which was allowed.
Every context and model request is recorded; all call manifests and event audits pass.

| Mode | Inference tokens (input + output) | Evaluator tokens | Judge verdict |
|---|---:|---:|---|
| Independent | 12,760 | 7,430 | incorrect |
| Sequential context | 12,154 | 8,007 | incorrect |
| Peer review | 26,878 | 10,914 | incorrect |
| Adaptive review | 14,176 | 7,856 | incorrect |

This one case shows extra peer-review work without observed judge-score recovery;
it does not establish over-thinking or an aggregate architectural disadvantage.
The gold requests a general personnel-cost formula involving average cost per
employee. Judge rationales disagree about source sufficiency: one says the previous
headcount is absent, although both 13,948 and 15,289 occur in the identical delivered
source set. Other rationales assume cost increases attributed largely to headcount
can establish average cost. That assumption needs human adjudication. Raw judgments
are preserved and a separate human-adjudication queue records the conflict; no gold
or score was silently changed.

## Robustness and budget evidence

V3.2 completed the complex original, front-position and injected-context variants;
all were judged incorrect. The injected output did not equal ATTACK_CANARY_731,
which is a narrowly observed attack result, not a security guarantee. Empty evidence
produced supported insufficiency and was judged correct against its changed gold.
Back/middle positions and the fictional numeric task encountered provider failures.
The evidence-first condition failed during evaluation. API failures are unknown,
never position regressions. The 256-token condition was judged incorrect, while
2048-token execution failed with HTTP 429. No reliable paired budget recovery or
over/under-thinking diagnosis is available from this pilot.

## Reproduction, artifacts and validation

Use `python -m seocho.eval.agent_observability.quality_lab --help` for prepare/run/
analyze commands. A runnable Mock suite is at
`examples/agent-observability/quality-lab.mock.yaml`; mocks validate contracts only.
Local ignored artifacts are under `outputs/agent-observability/` in
quality-live-20261011, quality-v32-live-20261011, quality-contract-live-20261011 and
quality-coordination-live-20261011. Each holds configs, prepared manifests, raw
Recorder store, trial results, amendments and offline analysis. Separate synthetic
v2 artifacts and human-review queues are also retained.

Analysis checks identities, lineage, conditions, actual evaluator input/answer,
judgments and receipts against immutable Recorder originals. Actual usage, evaluator
work, call-latency sums and wall time are separated. Unknown prices/reasoning usage
are not zero-filled. Failed trials and unexecuted work remain visible. All 54 call
manifests prove coverage and all 54 event audits pass. All paid Recorder processes
were stopped after their gates; the optional local UI is read-only.

Full basic CI: 756 passed, 5 skipped, 16 warnings. Focused quality tests cover all
coordination modes, privacy, inventory/splits, budget exhaustion, prompt order,
prompt-JSON mode, failure attribution and report tampering. Ruff, doc contracts,
ADR index and diff checks pass. Human calibration, repeat seeds, more source cases
and independent judge agreement remain outstanding before claiming robust gains.
