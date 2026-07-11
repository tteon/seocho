# AGENTS.md

Coding-agent guide for SEOCHO.

SEOCHO is ontology-aligned middleware between agents and graph databases. Keep
the public repository focused on the SDK, runtime, examples, docs, and tests.
Do not commit local agent/editor state.

## Start Here

Read these first for non-trivial work:

1. `README.md`
2. `CLAUDE.md`
3. `docs/REPOSITORY_LAYOUT.md`
4. `docs/WORKFLOW.md`
5. `docs/ISSUE_TASK_SYSTEM.md`
6. `docs/decisions/DECISION_LOG.md`

Also read `docs/GRAPH_RAG_AGENT_HANDOFF_SPEC.md` when touching semantic
retrieval, public memory answering, Graph-RAG behavior, routing policy, or
multi-agent query flow.

### AGENTS.md vs `.AGENTS.md`

This file (`AGENTS.md`) is the canonical execution contract — coding standards,
repo map, review discipline. The dotfile `.AGENTS.md` is a thin supplement that
defines only SEOCHO's **ExecPlan** shorthand (per `.PLANS.md`) for complex
features/refactors; it does not restate or override anything here. Boundary:
durable agent rules go in `AGENTS.md`; ExecPlan format/decision-style goes in
`.AGENTS.md`. The ADR index is validated by `scripts/ci/check_adr_index.py`
(no new duplicate IDs; every DECISION_LOG reference resolves).

## Repo Map

| Path | Role |
|---|---|
| `src/seocho/` | Distributable Python SDK and canonical engine code |
| `runtime/` | Deployment shell, runtime API wiring, policy checks |
| `extraction/` | Extraction service plus compatibility shims |
| `tests/seocho/` | SDK and engine regression tests |
| `extraction/tests/` | Runtime/extraction compatibility tests |
| `examples/` | Runnable examples, tutorials, small reference datasets |
| `docs/` | Product, architecture, workflow, and operator contracts |
| `.github/` | GitHub workflows and scheduled Codex prompt contracts |
| `scripts/` | CI, setup, benchmark, and maintenance helpers |
| `website/` | Tracked Astro/Starlight docs site |

Local-only directories such as `.agents/`, `.beads/`, `.claude/`, `.githooks/`,
`.jules/`, `.serena/`, `.seocho/`, `data/`, `logs/`, and `outputs/` must stay
out of Git.

## Module Playbook

Use this map before editing code. It keeps new behavior in the module that owns
the architectural decision, not just the nearest import.

| Concern | Start here | Watch for |
|---|---|---|
| Public SDK/session API | `src/seocho/client*.py`, `src/seocho/session.py`, `src/seocho/models.py`, `src/seocho/http_transport.py` | Keep the facade thin and preserve typed public contracts. |
| Agent runtime contracts | `src/seocho/agent/`, `src/seocho/agents*.py`, `src/seocho/runtime_contract.py` | Preserve OpenAI Agents SDK assumptions and trace payload shape. |
| Ontology and governance | `src/seocho/ontology*.py`, `src/seocho/fibo/`, `docs/ontology/` | Keep Owlready2/offline reasoning out of request-time code. |
| Indexing and graph shaping | `src/seocho/index/`, `src/seocho/rules.py`, `src/seocho/graph_projector.py` | Keep schema/rule changes compatible with documented examples. |
| Query, retrieval, answering | `src/seocho/query/`, `src/seocho/prompt_strategy.py`, `src/seocho/semantic_prompt_composer.py` | Validate Cypher identifiers, preserve read-safety, and document Graph-RAG behavior changes. |
| Runtime API and policy | `runtime/agent_server.py`, `runtime/memory_service.py`, `runtime/policy.py`, `runtime/models/` | Preserve `workspace_id`, policy checks, and deployment-shell boundaries. |
| Extraction compatibility | `extraction/` | Prefer wrappers and migration shims; do not add new canonical engine logic here. |
| Evaluation and benchmarks | `src/seocho/eval/`, `src/seocho/benchmarking.py`, `evaluation/`, `scripts/benchmarks/` | Keep one-off corpora/results private unless promoted to supported examples. |
| Public examples and docs | `examples/`, `docs/`, `website/` | Keep README product-first and avoid editing generated website mirrors directly. |

## Impact Map

When a change touches a concern below, include the matching validation and docs
surface in the same commit or PR.

| If you touch | Also check |
|---|---|
| `src/seocho/client*.py`, `src/seocho/session.py`, or `src/seocho/models.py` | SDK tests under `tests/seocho/`, README/QUICKSTART public API wording |
| `src/seocho/query/` or Graph-RAG prompts | `docs/GRAPH_RAG_AGENT_HANDOFF_SPEC.md`, query tests, Cypher validation coverage |
| `src/seocho/index/` or ontology shaping | examples/datasets assumptions, ontology docs, indexing tests |
| `runtime/` route, model, or policy code | runtime/extraction compatibility tests and `workspace_id` propagation |
| `.github/` or `scripts/ci/` | `docs/GITHUB_AUTOMATION.md`, root hierarchy/doc contract checks |
| `AGENTS.md`, `CLAUDE.md`, or repository layout docs | `scripts/pm/lint-agent-docs.sh` and `scripts/ci/check-root-hierarchy-contract.sh` |

## Stack Invariants

- OpenAI Agents SDK is the agent runtime baseline.
- DozerDB is the graph database backend baseline.
- `workspace_id` must be propagated in runtime-facing models, APIs, and traces.
- Tracing stays vendor-neutral; Opik is the preferred team backend.
- Owlready2 is allowed only in offline ontology governance paths, not hot
  request paths.

## How To Choose The Edit Surface

- SDK facade, ontology, indexing, query, stores: start in `src/seocho/`.
- Runtime endpoints, auth/policy, memory service: start in `runtime/`.
- Legacy import compatibility or extraction service behavior: start in
  `extraction/`, but keep new canonical behavior out of shim-only modules.
- Public docs or onboarding: update `README.md`, `QUICKSTART.md`, or `docs/*`.
- GitHub automation: update `.github/` and reusable helpers in `scripts/ci/`.
- Examples/tutorial data: keep them under `examples/`.

If a change crosses more than one of these areas, keep the PR explanation clear
about ownership and risk.

Use GitHub issues and pull requests for public work tracking. Local trackers
are private workspace aids only.

## Coding Rules

- Use type hints for new or modified Python functions.
- Keep changes scoped and testable.
- Use logging, not `print`, outside CLI/demo code.
- Do not hardcode secrets, keys, tokens, or local absolute paths.
- Prefer centralized config (`extraction/config.py`) where the existing code
  already uses it.
- Keep public API changes deliberate. If a public SDK name, return model, or
  runtime response changes, update docs and tests in the same slice.
- Preserve `workspace_id` and runtime policy checks for new endpoints/actions.
- Keep heavy ontology reasoning out of request-time code.
- Do not reintroduce root `seocho/`, root `dataset/`, root `images/`, root
  `ontology/`, root `experiments/retrieval_comparison/`, or tracked local tool
  state directories.

## Validation

### Live-evidence rule

- Mocks and in-memory runners validate contracts, deterministic failure paths,
  and no-service CI only. They are never evidence for throughput, latency,
  scalability, production readiness, or external-system compatibility.
- Performance and production claims require an actual run against every
  service named in the claim (for example PostgreSQL, DozerDB, etcd, Mara, or
  an OTel Collector). Reports must identify service versions, dataset,
  concurrency, hardware/container limits, warmup, and any skipped component.
- A failed or unavailable live gate is reported as a gap, not replaced with a
  mock number. Keep the mock test, fix the integration, and rerun the same
  workload before making the claim.

Run the narrowest relevant command first, then broaden if the touched surface is
shared.

| Change | Command |
|---|---|
| Any behavior/config/CI change | `bash scripts/ci/run_basic_ci.sh` |
| Docs contract only | `bash scripts/ci/check-doc-contracts.sh` |
| Runtime shell/import ownership | `bash scripts/ci/check-runtime-shell-contract.sh` |
| SDK/extraction ownership | `bash scripts/ci/check-module-ownership-contract.sh` |
| Root layout or public GitHub surface | `scripts/ci/check-root-hierarchy-contract.sh` |
| Python import smoke | `uv run python -c "import seocho; print(seocho.__file__)"` |

If you do not run the full basic CI, state the exact commands run and the
remaining validation gap.

## Documentation Rules

- Architecture or workflow changes must update the relevant `docs/*` contract.
- Significant architectural decisions need an ADR under `docs/decisions/` and
  an entry in `docs/decisions/DECISION_LOG.md`.
- Do not edit generated mirrored docs under `website/src/content/docs/docs/`
  directly; regenerate them from repo-root docs.
- Keep README product-first. Move operational detail to focused docs.

## Git And Landing

- Never revert user changes you did not make.
- Avoid destructive commands unless explicitly requested.
- Push target is `main`.
- Before landing, run relevant validation, `git pull --rebase`, push, and
  confirm `git status --short --branch` is clean and aligned with `origin/main`.

## Automation Boundaries

- Scheduled Codex workflows must stay draft-only, small, reviewable, and
  non-destructive.
- Comment merge is maintainer-triggered only: exact `/go`, write-or-higher
  permission, clean non-draft PR, squash merge.
- Agent/tool-specific private state belongs in local ignored directories, not in
  the public repo.
