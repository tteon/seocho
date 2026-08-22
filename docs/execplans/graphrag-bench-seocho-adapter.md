# GraphRAG-Bench adapter and governed Text2Cypher extension

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds. It follows `.PLANS.md` from the repository root.

## Purpose / Big Picture

SEOCHO needs an external reasoning corpus that is large enough to test whether ontology-guided context helps an agent, rather than relying on the small quickstart workload. This plan adopts the official `jeremycp3/GraphRAG-Bench` question files as an external answer-and-rationale track, without falsely treating them as a gold graph, a Text2Cypher corpus, or governance evidence.

After the first milestone, an operator can transform a local upstream `questions/` directory into a content-free, content-addressed reference ledger and manifest. Every output row makes missing corpus-span, Cypher, and governance labels explicit. This prevents the later experiment from silently converting missing labels into favourable scores.

## Progress

- [x] 2026-08-22: Inspected upstream commit `8a5c821038ea31a8b474b030fb452b9b7fcf43d8`; confirmed its five-file `Question/` layout and its separate official evaluator.
- [x] 2026-08-22: Added a strict five-file adapter, upstream file digests, local manifest, and bounded preparation metrics; changed its output to a content-free reference ledger after verifying the upstream no-distribution/no-modification license.
- [x] 2026-08-22: Downloaded the ignored academic-only snapshot, prepared a balanced 50-case (10/type) smoke ledger, and verified every source file digest. The full snapshot contains missing rationales in MC (2), OE (4), and TF (12); the smoke selection happened not to include one.
- [x] 2026-08-22: Passed the focused adapter tests, docs contracts, Ruff checks, and repository basic CI (1107 passed, 1 skipped).
- [ ] Download the upstream dataset into ignored `.seocho/datasets/graphrag-bench/`, record the revision/license review, and create a balanced smoke manifest.
- [ ] Bind each selected question to retrieved textbook spans and source document identifiers using a reproducible corpus-indexing manifest.
- [ ] Add independently reviewed SEOCHO extension labels: ambiguity class, intent slots, allowed ontology profile, gold Cypher or executable query constraints, and valid/invalid/stale governance variants.
- [ ] Run matched direct, profile/slice, and governed Text2Cypher arms through DozerDB and the actual Agents SDK loop; score answers separately with the official evaluator-compatible output.

## Surprises & Discoveries

- The repository contains a `scripts/serve_track/annotate_graphrag_bench.py`, but it targets another benchmark family with Novel/Medical subsets and must not be used to parse this textbook benchmark.
- The upstream question rows include question, answer, rationale, and two topic levels, but no question-to-source-span mapping, gold RDF triples, or gold Cypher. These are missing labels, not zero-valued labels.
- The Hugging Face dataset card explicitly permits academic research only and prohibits commercial use, distribution, and modification. Adapter artifacts must contain references and hashes, not copied benchmark content.
- The verified snapshot has at least one empty official rationale (`MC.jsonl` row 136). Such rows remain answer-evaluable but are labelled `missing_upstream` and excluded from any rationale/AR denominator unless an upstream correction is pinned.
- Upstream `evaluator.py` uses a GPT-4o judge for rationale and open-ended answer scores. SEOCHO must record any replacement judge model and not describe it as the official score.

## Decision Log

- Decision: Preserve upstream answer/rationale evaluation as Track A and publish a separately named `GraphRAG-Bench × SEOCHO` extension as Track B.
  Rationale: changing the label universe would invalidate comparability with the upstream benchmark.
  Date/Author: 2026-08-22 / Codex.
- Decision: Keep upstream text and generated adapter data local-only.
  Rationale: the corpus derives from textbooks; repository availability does not by itself grant SEOCHO redistribution rights.
  Date/Author: 2026-08-22 / Codex.
- Decision: Measure dataset preparation as an operational event but store content, paths, case IDs, workspace IDs, and digests in JSONL manifests/traces rather than metric labels.
  Rationale: metrics must stay low-cardinality and privacy-safe.
  Date/Author: 2026-08-22 / Codex.

## Outcomes & Retrospective

The implemented adapter establishes a reproducibility boundary, not a semantic-lift result. A claim about ontology value remains blocked until source bindings, extension labels, and a matched live workload exist.

## Context and Orientation

`src/seocho/eval/graphrag_bench.py` owns the upstream parser contract. `scripts/benchmarks/prepare_graphrag_bench.py` turns a local `Question/` directory into JSONL plus manifest. `src/seocho/metrics.py` defines the bounded telemetry instruments. `docs/BENCHMARKS.md` documents the public benchmark-track policy.

An ontology signal is an inspectable indication that an active ontology profile helped, mismatched, or was insufficient. A typed evidence bundle contains selected evidence, relation paths, required slots, provenance, and named insufficiency. A governance variant is a deliberately labelled valid, invalid, stale, or receipt-missing candidate used to test admission; it is not a normal QA case.

## SEOCHO Evidence Contract

Every selected Track B case must preserve: upstream source-file hash and row index; corpus document IDs and spans; ontology bundle/profile digests; candidate Cypher and validation result; executed graph result; selected triples/relation path; required and missing slots; projection receipt/lease/fence; final answer; and evaluation result. Raw source text and prompt content remain optional local trace capture only.

## SEOCHO Review Panel

The professor lens rejects treating a rationale as a proof of graph truth. The software-engineer lens requires a frozen manifest, schema validation, and explicit missing labels. The computer-systems lens requires local-only artifacts, bounded telemetry labels, sampling before full indexing, and separate cold/warm cost measurements. The plan advances only if all three conditions remain true.

## Plan of Work

First prepare the upstream questions without data invention. Next index a fixed textbook corpus snapshot and produce document/span bindings. Then add a reviewed extension annotation layer instead of overwriting upstream rows. Finally run the following separately reported conditions: bare context, static ontology profile, JIT ontology slice, and governed projection/receipt. The Text2Cypher condition adds intent resolution, read-only and workspace validation, execution, evidence check, and bounded repair/abstention.

## Concrete Steps

1. Download the upstream release to `.seocho/datasets/graphrag-bench/` after licence review. Do not add this directory to Git. The snapshot verified on 2026-08-22 is commit `ed2f6c2e80ddbfe4f2886ac89d520ee22b1623c4`.
2. Run:

       uv run python scripts/benchmarks/prepare_graphrag_bench.py \
         --question-dir .seocho/datasets/graphrag-bench/questions \
         --out .seocho/benchmarks/graphrag-bench/cases.jsonl

3. For a balanced smoke run, add `--limit-per-type 10`; preserve the full-file digest in the output manifest.
4. Write reviewed extension records keyed by `case_id`, rather than editing the source adapter rows.
5. Run the matched workload with `SEOCHO_TRACE_BACKEND=jsonl`, a unique trace path, and an experiment manifest naming source snapshot, model, ontology bundle, DozerDB version, and concurrency.

## Validation and Acceptance

Run:

    uv run pytest tests/seocho/test_graphrag_bench_adapter.py tests/seocho/test_production_metrics.py -q
    uv run python scripts/benchmarks/prepare_graphrag_bench.py --help
    bash scripts/ci/run_basic_ci.sh

Acceptance is a five-file manifest whose case count and per-file hashes are inspectable, with all unavailable labels explicitly marked `unbound` or `unannotated`. A live answer-quality claim requires a real indexed corpus, DozerDB, Mara/Agents SDK run, trace artifact, and official-compatible evaluator outputs.

## Idempotence and Recovery

Preparation overwrites only the explicitly selected local `--out` and manifest paths. If a source file digest changes, retain the old manifest and write a new output directory; do not mix outputs from two snapshots. A malformed source row fails with its file and line number; fix or replace the upstream snapshot rather than skipping it silently.

## Artifacts and Notes

Generated files belong below `.seocho/benchmarks/graphrag-bench/` during iteration and `outputs/evaluation/graphrag_bench/` for explicitly retained local evidence. Only aggregate, reproducible findings belong in a public dated report.

## Interfaces and Dependencies

The adapter expects `FB.jsonl`, `MC.jsonl`, `MS.jsonl`, `OE.jsonl`, and `TF.jsonl`, each with `Question`, `Answer`, `Rationale`, `Level-1 Topic`, and `Level-2 Topic`. It has no Hugging Face client dependency and does not contact an LLM. Its ledger contains only replay references, file hashes, and labelled gaps; a future runner reads source content directly from the local snapshot. The official evaluator remains an external dependency; its judge model and parser assumptions must be versioned in every comparable result.

## Cost, Latency, and Provider Policy

Dataset preparation makes no paid model call. Full indexing and answer evaluation are paid runs and must begin with a topic-balanced smoke subset. Mara is the preferred judge path for SEOCHO-specific extension results, while any score substituted for the upstream GPT-4o evaluator is labelled non-official. Record provider, model, token usage when available, request latency, failures, retries, and judge decision in content-free traces.
