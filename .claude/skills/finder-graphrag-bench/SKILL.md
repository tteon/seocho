---
name: finder-graphrag-bench
description: Run SEOCHO's vector-vs-graph(-vs-hybrid) RAG comparison on FinDER-style financial QA, end to end — extract per-ontology graphs into DozerDB, embed evidence into LanceDB, then compare retrieval modes across one or more LLMs with token_f1 + LLM-judge and Opik traces. Invoke when asked to run/extend the FinDER or GraphRAG vector-vs-graph benchmark, reproduce the content-vs-context study, or measure where graph retrieval beats vector.
---

# finder-graphrag-bench

Purpose: a single runbook for the multi-LLM vector/graph/hybrid comparison so a fresh clone can reproduce it without rediscovering the env contract, the per-ontology database setup, the rate-limit discipline, and the gotchas that silently corrupt results. This is the operational companion to CLAUDE.md §19–§20 (the vector-vs-graph study and its experimenter ethics).

Pairs with: `dozerdb-up` (graph backend), `mara-gateway` (LLM provider), `opik-trace-meta` (trace tagging). Invoke those for their specifics.

## Pipeline (4 stages)

```
extract → load(graph already in DozerDB) + embed(LanceDB) → compare → score
```

1. **Extract** per-ontology graphs into DozerDB — `scripts/benchmarks/finder_phase_experiment.py` (phase cases) or `finder_4arm_sample.py` (ontology-arm sweep). Writes typed nodes to an ontology-derived DB.
2. **Embed** evidence into LanceDB — `scripts/benchmarks/finder_load_to_lancedb.py` (`--all` for all 910 cases, keyed by slice). Needed for the `vector` and `hybrid` lanes.
3. **Compare** retrieval modes × LLMs — `scripts/benchmarks/finder_compare_vector_graph.py`.
4. **Score** — token_f1 + LLM-judge (json), emitted to `outputs/evaluation/finder_compare/<run>/` + Opik.

## Preconditions (check ALL before launching a long run)

- **`.env`** (repo root) has: `MARA_API_KEY`, `OPENAI_API_KEY` (embeddings), `NEO4J_URI/USER/PASSWORD`, and Opik vars. `bench_common.bootstrap()` loads `.env` then `/home/hadry/openup/.env`; the repo-root `.env` wins per key.
- **DozerDB up + reachable** → see `dozerdb-up`.
- **Per-ontology databases exist.** Each ontology composition maps to its own DB (`sanitize(ontology.name + "lpg")`, e.g. `be+ind`→`fibobeindlpg`, `be+fbc`→`fibobefbclpg`). DozerDB does NOT auto-create on write. `finder_phase_experiment.run_one` now calls `graph_store.ensure_database(default_database)`, but if you bypass it, pre-create every DB the run needs — a missing DB silently stores 0 nodes and the compare's graph lane then 429s on `Graph not found`.
- **LanceDB embedded for the cases you'll compare.** If a case isn't embedded, its vector/hybrid lane retrieves the wrong evidence. Run `finder_load_to_lancedb.py --all` once (cheap) to cover everything.
- **LLM choice + rate limits** → see `mara-gateway`. Use `--judge mara/gpt-oss-120b` to keep the judge off the heaviest answer model; set `FINDER_LLM_TIMEOUT=300`.
- **Opik tagging** → see `opik-trace-meta`.

## Run recipes

Smoke (1 case × all LLMs × 3 modes — validate the loop + Opik tags first):
```bash
python3 -u scripts/benchmarks/finder_compare_vector_graph.py \
  --smoke --case 4af93b03 \
  --llms "mara/DeepSeek-V3.1,mara/MiniMax-M2.5,mara/MiniMax-M2.7,mara/gpt-oss-120b" \
  --judge mara/gpt-oss-120b --modes vector,graph,hybrid \
  --graph-workspace-prefix <extraction-workspace-prefix>
```

Stratified slice-% (scale up after smoke passes):
```bash
# 1) extract (writes graphs; FINDER_LLM_TIMEOUT guards big cases)
FINDER_LLM_TIMEOUT=300 python3 -u scripts/benchmarks/finder_phase_experiment.py \
  --stratified 0.10 --llm mara/DeepSeek-V3.1 --variants treatment \
  --workspace-prefix mara-strat10-<ts>
# 2) embed all cases once
python3 -u scripts/benchmarks/finder_load_to_lancedb.py --all --overwrite
# 3) compare
python3 -u scripts/benchmarks/finder_compare_vector_graph.py \
  --stratified 0.10 --llms "mara/DeepSeek-V3.1,..." --judge mara/gpt-oss-120b \
  --modes vector,graph,hybrid --graph-workspace-prefix mara-strat10-<ts>
```

`--graph-workspace-prefix` MUST match the extraction `--workspace-prefix` — the compare's graph lane reads nodes from the ontology DB filtered by `_workspace_id` built from this prefix. Mismatch → empty graph context.

## Background + monitoring (long runs)

- Launch detached (`python3 -u ... > log 2>&1 &`) — sweeps run hours; never as a fragile foreground child.
- Resume-safe: per-(case,mode,llm) partials land in `<run>/partial/`; the final `aggregate.json` is rebuilt from in-memory records. Re-running the same command skips matching partials.
- Watch milestones, not every line; aggregate partials mid-run by `mode` and by `slice`.

## Gotchas that silently corrupt results (each cost a wasted run this is built from)

- **Entity fallback.** A weak extractor labels everything generic `Entity` instead of the ontology's domain classes, gutting the graph. MARA DeepSeek-V3.1 ≈ 0% fallback; some models ~67%. Check the per-label distribution in the target DB after extraction (`MATCH (n) WHERE NOT n:_infra RETURN labels(n)[0], count(*)`); near-100% `Entity` = re-extract with a better model.
- **Graph serializer dropping raw text (`keep_raw`).** The typed-only graph view drops the raw Chunk/Section text the graph already stores, handicapping the graph lane vs vector/Graphiti. `_graph_context(..., keep_raw=True)` re-adds it — the False/True delta is the *serialization* effect, not recall. Don't attribute a graph-lane loss to "ontology/recall" until you've A/B'd `keep_raw`.
- **judge 0 → -1 bug.** `int(d.get("score", -1) or -1)` turns a real `0` into `-1`; reserve `-1` for parse failures only.
- **Per-case vector scoping.** With many cases embedded, vector retrieval must filter to the case's own evidence (`where case_id = ...`), else top-k returns other cases' chunks.
- **MARA daily quota / 429** → `mara-gateway`. Separate the judge model; never re-run completed partials.

## Experimenter ethics (binding — CLAUDE.md §20)

Report all slices every run; pair point estimates with n / spread; small-sample wins are noise until reproduced at scale (a 9-case "graph beats vector on S1" reversed at 28 cases). Keep ontology the only moving part across graph arms. State measured result before mechanism. Disconfirming evidence is the finding, not noise to filter.

## Verify done

Smoke: all runs OK, judge parse-errors 0, the 4 Opik axis-tags present (`opik-trace-meta`). Full run: `aggregate.json` written, per-slice vector/graph/hybrid table produced with n per cell, error rate reported (not hidden).
