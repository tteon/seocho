# Architecture Health Scorecard

Per-domain quality grade with gaps tracked over time. Unlike `KNOWN_ISSUE.md`
(a risk register of specific defects), this grades each ownership domain from
`docs/MODULE_OWNERSHIP_MAP.md` on maturity + test/benchmark coverage, names the
open gaps, and records when each row was last reviewed. Re-grade on material
change to a domain; bump `Last reviewed` and adjust the grade + gaps.

Grades: **A** production-hardened, well-covered · **B** solid, known gaps ·
**C** functional but thin/compat-only · **D** prototype/at-risk. A `±`
qualifies within a band.

| Domain | Canonical owner | Grade | Coverage | Open gaps | Last reviewed |
|---|---|---|---|---|---|
| Public SDK facade | `src/seocho/client*.py`, `local_engine.py` | B− | thin facade; ~1 dedicated test file | facade-level contract tests sparse; keep helpers out of the facade (per ownership note) | 2026-06-09 |
| Agent runtime contracts | `src/seocho/agent/*`, `agents*.py`, `runtime_contract.py` | B+ | 19 modules, ~8 test files; recent cost-aware model router (#230), capability matchmaking (#229) | reflection/route-policy paths under-tested vs the new model-router axis | 2026-06-09 |
| Ontology schema & governance | `src/seocho/ontology*.py`, `fibo/*` | A− | ~22 ontology test files (highest coverage); DDD bounded-context map + boundary validation (#231) | SHACL-style governance kept offline by design — keep out of hot paths | 2026-06-09 |
| Indexing & graph shaping | `src/seocho/index/*`, `rules.py` | B | 19 modules, ~3 test files; ADR-0103 XBRL→Observation ingester, batched Neo4j write (#225) | index/* test coverage thin relative to surface; H4 dimensions newly added (#248) | 2026-06-09 |
| Query, routing, evidence, answering | `src/seocho/query/*`, `prompt_strategy.py` | B | 34 modules, ~8 test files; arbiter v2, GraphCoT, Graph-RAG handoff contract | structured text2cypher answerability still the chronic weak spot (ADR-0099/0103); LLM-judge eval is noisy (ρ≈0.18) | 2026-06-09 |
| Runtime shell & API wiring | `runtime/*` | B | 13 modules; worktree-isolated boot + policy/readiness (seocho-6q9.3) | live two-worktree concurrent boot is a documented manual check, not in CI | 2026-06-09 |
| Extraction compatibility | `extraction/*` (wrappers) | C | compat-only; legacy aliases | intentionally frozen — no new canonical logic here (per ownership rule) | 2026-06-09 |
| Benchmark harnesses & eval | `src/seocho/eval/*`, `scripts/benchmarks/*` | B | FinDER backbone/arms/bake-off harnesses, perf-budget gate; private corpora | results are local/uncommitted by rule; LLM-judged metrics confirmatory only (deterministic metrics primary) | 2026-06-09 |
| Entry docs & contributor contracts | `README.md`, `AGENTS.md`, `CLAUDE.md`, `docs/*` | A− | 109 ADRs + DECISION_LOG, read-orders, mechanical linters | AGENTS.md/.AGENTS.md duality + duplicate ADR IDs (seocho-b01.3); structure/drift linting (seocho-b01.4) | 2026-06-09 |
| GitHub automation & CI | `.github/*`, `scripts/ci/*` | B+ | doc/hierarchy/runtime-shell/module-ownership contracts, basic CI | contracts check presence, not structure/freshness/drift (seocho-b01.4) | 2026-06-09 |

## How to use this

- A domain dropping a grade band is a signal to open a hardening ticket.
- "Open gaps" should cite a `seocho-*` ticket or ADR where one exists.
- This scorecard is reviewed during architecture-affecting PRs; the reviewer
  bumps the touched domain's row.

## Related

- `docs/MODULE_OWNERSHIP_MAP.md` — the domain definitions graded here
- `docs/ARCHITECTURE.md` — the system design these domains implement
- `KNOWN_ISSUE.md` — specific-defect risk register (complementary)
