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
- Preserve `workspace_id` and runtime policy checks for new endpoints/actions.
- Keep heavy ontology reasoning out of request-time code.
- Do not reintroduce root `seocho/`, root `dataset/`, root `images/`, root
  `ontology/`, root `experiments/retrieval_comparison/`, or tracked local tool
  state directories.

## Validation

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
