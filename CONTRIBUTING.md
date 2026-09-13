# Contributing to SEOCHO

Contributions should make ontology-aligned indexing, graph memory and agent
answers easier to use, inspect or maintain. Start with a concrete user problem
and an observable acceptance criterion.

## Pick a path

| Contribution | Start with | Expected evidence |
|---|---|---|
| Fix a user's failed run | issue reproduction, `docs/EXPERIMENT_PLATFORM.md` | failure regression test and an inspectable receipt |
| Improve indexing/query | owning module and Graph-RAG handoff contract | matched behavioral tests and appropriate live evaluation |
| Improve SDK/runtime | public API contract and compatibility tests | preserved response types, policy and workspace propagation |
| Improve docs/examples | the actual command a new user runs | successful example validation or a stated service gap |
| Change architecture | issue, ExecPlan and ADR | explicit alternatives, consequences and validation |

Use [Repository Layout](docs/REPOSITORY_LAYOUT.md) and
[Module Ownership](docs/MODULE_OWNERSHIP_MAP.md) to choose the edit surface.
Canonical engine changes belong under `src/seocho/`; `runtime/` owns the service
shell and `extraction/` retains compatibility/batch responsibilities.

## Set up an isolated task

```bash
git clone https://github.com/tteon/seocho.git
cd seocho
git fetch origin
make agent-doctor
make agent-start TASK=issue-123
```

Change into the printed checkout and install the committed environment:

```bash
uv sync --locked --extra dev
uv run pytest tests/seocho/test_run_spec.py -q
```

A normal branch/checkout workflow is also supported. The helper keeps ongoing
work untouched and writes a local task receipt/handoff. It does not install
packages, start services, fetch or stash changes. See
[Agent Workflow](docs/AGENT_WORKFLOW.md) for commands and recovery.

Coding agents follow [AGENTS.md](AGENTS.md). Maintainers using beads claim their
local task and update notes at meaningful steps. Public contributors need only
a GitHub issue or PR; local tracker state never belongs in a contribution.

## Validate the changed behavior

Run the narrow relevant test first, then the canonical gate:

```bash
make agent-check
# Equivalent: bash scripts/ci/run_basic_ci.sh
```

Basic CI checks curated SDK/runtime behavior, module/layout contracts, selected
lint surfaces and strict incremental typing for experiment evidence modules.
It supports Python 3.10, 3.11 and 3.12. A passing mock test establishes its tested
contract, not real service compatibility, throughput or answer correctness.

For docs-only changes:

```bash
bash scripts/ci/check-doc-contracts.sh
```

Generate website mirrors from root docs; do not edit generated pages directly.
For site changes, run the checks documented in [GitHub Automation](docs/GITHUB_AUTOMATION.md).

For experiment changes, read prior manifests/reports and local experiment memory
first. State the incremental hypothesis, fixed inputs, changed condition and
remaining telemetry gaps. Retain failed runs and compare saved reports before
spending on another live run. See [Benchmarks](docs/BENCHMARKS.md).

## Keep decisions and evidence linked

The public issue/PR owns the user problem and delivery. An ADR records a durable
architectural choice; an ExecPlan tracks a complex implementation. Local beads
and handoff notes track progress without duplicating those contracts. Run receipts
hold measurements. Link them by purpose so the next contributor can resume work.

A PR should describe:

- the trigger and resulting behavior, including failure behavior;
- the owning modules and compatibility consequences;
- exact validation commands and any skipped live/service gates;
- the relevant docs/ADR and a safe evidence summary.

Keep one coherent change per PR. Larger improvements should have reviewable
slices and a recorded rollout plan. Use conventional commit prefixes such as
`fix:`, `feat:`, `docs:`, `refactor:` or `test:`. Preserve user changes, inspect the
staged diff and run `git diff --check` before pushing.

## Repository hygiene

Keep private datasets, credentials, logs, generated results and agent/editor
state out of Git. Local state lives under `.seocho/` or the existing `data/` and
`outputs/` paths. Tool discovery directories retain their required names; only
shared `.claude/skills/` may be tracked. Put supported examples in `examples/`,
reusable automation in `scripts/`, and public contracts in `docs/`.

Do not delete compatibility modules or prior experiment receipts because they
look old. Trace imports, callers and evidence references before proposing removal.

## GitHub and review

Use the issue forms for bugs, feature requests and docs/examples. Bug reports
benefit from Python/SEOCHO versions, backend/provider, the failing stage and a
redacted run diagnostic. Do not post private corpora or unreviewed full reports.

Maintainers use the existing `area-*`, `kind-*`, `urgency-*`, `impact-*` and
`sev-*` labels. A good first issue should be solvable without private data or
knowledge of the runtime migration.

Scheduled agent workflows remain small and draft-only. Maintainers decide
merges; automatic checks and coding reviews provide evidence. Comment merge
requires the exact `/go` command, write-or-higher permission, and a clean,
non-draft PR, using squash merge.

For releases, follow [Release and Community Operations](docs/RELEASE_AND_COMMUNITY_OPERATIONS.md),
update `CHANGELOG.md`, and use the release issue template. Report vulnerabilities
through [SECURITY.md](SECURITY.md).
