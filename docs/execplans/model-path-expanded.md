# Expanded FinDER public execution-path experiment

## Purpose and context

Increase the two-question pilot to twelve original questions, six with each original reasoning boolean, and two independent samples per model/arm/question. Compare five MARA models and direct, explicit, adaptive single-agent designs: 360 assigned trials. Public plans and tool execution are observable; internal reasoning is not inferred.

## Progress

- [x] Specify seeded domain round-robin selection and prefer distinct companies.
- [x] Freeze uniform prompt_json contracts, fixed question-ranked passages and private evaluator labels.
- [ ] Execute bounded Recorder-only MARA suite.
- [ ] Audit all recorded calls/events and summarize repeated public paths, completion, judgments and costs.

## Decision log and review panel

2026-10-11: Use uniform prompt_json for all five models, including adaptive tool loops. This is a new cohort, separate from earlier API-schema runs. Fixed original passages remain the only knowledge source. Two samples share seed42: independent request repetition, not assurance of stochastic diversity.

professor_agent lens: Six cases per original boolean and domain round robin broaden exploratory coverage; twelve cases cannot establish population rankings. Human adjudication is pending; repeat plan structure measures descriptive stability, not correctness. Selection never uses gold to rank passages. Falsifier: missing strata, gold in inference, or mismatched sources across arms.

software_engineer_agent lens: Reuse accepted runner and schemas; no production behavior changes. Freeze manifests and unique output directory. Preserve partial results and failure source. Falsifier: any unrecorded request or invalid source receipt.

computer_systems_agent lens: Serial requests, cache off, no retries; at most3600 physical calls and7372800 reserved output tokens, ten calls per run, six successful tools. Output reservations exclude input tokens; prices unknown. Record actual usage and provider failures. Shut proxy down in finally. Falsifier: admission beyond caps, cache contamination, or live worker remaining after completion.

## Evidence and evaluation contract

Public case inputs contain question, fixed original source passages, separate agent reference. Gold answer/references/domain/reasoning boolean remain private to evaluation. Original reasoning boolean is never derived from operation type. Plan judge is gold-free; answer judge uses gold. Gemma self-judging remains a bias and no scores automatically promote a design. No graph database, ontology retrieval, private chain-of-thought or multi-agent effectiveness claim is included.

## Work and concrete steps

Prepare private artifacts under outputs/agent-observability/model-path-expanded-20261011. Copy accepted SDK/Recorder source and immutable prepared/config artifacts into the already authorized isolated server tree. Start Recorder8835, run quality_lab run, stop Recorder, then analyze results and run per-run call/event audits. Retain all failures and raw blobs. Analysis must separate model, provider, contract and evaluator failures. Repeated paths should expose plans/dependencies/tool sequences and partial failures rather than silently scoring missing plans as disagreement.

## Validation and recovery

No engine/config behavior is changed; validate suite schema and artifact hashes, use live MARA evidence, run docs contracts and diff check. Prior basic CI763 passed applies to unchanged engine only. Do not overwrite or resume a completed suite implicitly. Atomic checkpoints preserve interrupted work; report unexecuted assignments if bounded gate ends early.

## Outcomes, surprises and artifacts

Pending. Raw data/results stay ignored and private. Monetary totals and reasoning tokens remain unknown where provider does not report them.

## Amendment after initial live gate

Uniform prompt_json gate was stopped after observed fenced-JSON/validation failures. Its checkpoints and immutable Recorder blobs remain in model-path-expanded-20261011; one in-flight attempt may lack trial finalization and is explicitly not a completed assignment. SIGINT was ineffective for the stdin SSH worker; exact identified worker and proxy were terminated with SIGTERM. Do not conceal interrupted raw calls or claim complete audits for that preliminary store.

New model-path-expanded-native-20261011 freezes the same twelve questions, fixed sources, 360 assignments and budgets, but uses api_schema for four models and prompt_json for MiniMax across all its arms. This is within-model fairness; cross-model interface differences remain a confound. No code repair or posthoc output cleaning is applied to raw model outputs. Initial native gate includes completed runs, unlike the preliminary universal contract. Professor lens accepts exploratory within-model comparison; engineer lens requires original failures preserved; systems lens requires preliminary proxy closed and separate budget accounting.
