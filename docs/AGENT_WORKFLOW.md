# Coding with agents

Use this page to start, hand off and land a coding task. `AGENTS.md` is the
canonical execution contract; this guide is the practical entrypoint. Keep the
public repository useful for people and coding agents through the same commands.

## Start a task

```bash
make agent-doctor
# Fetch explicitly when you need a newer baseline.
git fetch origin
make agent-start TASK=seocho-example
```

`agent-doctor` is read-only. `agent-start` creates branch `codex/<task>` and a
separate checkout under the common repository's `.seocho/worktrees/<task>/`.
It never switches/stashes the current checkout, installs packages, fetches,
launches a service, or removes an existing task. An existing branch/directory
is an error, not something to overwrite. You can use a GitHub issue identifier
as the task name. Select another existing local ref with `BASE=<ref>`.

Change into the printed checkout, then:

```bash
uv sync --locked --extra dev
uv run pytest tests/seocho/test_run_spec.py -q
```

Maintainers with the local beads tracker run `bd ready`, inspect active tasks,
and claim their task before coding. Public contributions use GitHub issues/PRs;
`.beads` stays private. A checkout receipt and `.seocho/HANDOFF.md` keep local
context close to the task. Edit the handoff as work proceeds; never commit it.

## Read by responsibility

Start with the read order in `AGENTS.md`. Then select the relevant contract:

| Task | Contract / owner | First useful validation |
|---|---|---|
| User-data experiment | `docs/EXPERIMENT_PLATFORM.md`, `src/seocho/e2e.py` | `uv run pytest tests/seocho/test_run_evidence.py -q` |
| SDK/API | `docs/SDK_CONTRACT.md`, `src/seocho/` | focused SDK tests |
| Index/query behavior | `docs/MODULE_OWNERSHIP_MAP.md`, Graph-RAG handoff spec | indexing/query contract tests |
| Runtime/policy | `runtime/`, `docs/RUNTIME_ARCHITECTURE.md` | runtime/extraction compatibility tests |
| Repository/GitHub docs | layout/workflow/automation docs | `bash scripts/ci/check-doc-contracts.sh` |

Search the owning module before the entire tree. Use `rg --files src/seocho/query`
or `rg 'pattern' src/seocho/index tests/seocho`; local corpora and generated
results are not useful default code-search inputs.

## Keep a review trail

Use each record for one purpose:

| Record | Purpose | Update when |
|---|---|---|
| Public issue / PR | user problem, acceptance, review and delivery | scope or public behavior changes |
| Local beads task | claim, progress, commands, artifacts and follow-ups | a meaningful step finishes |
| ExecPlan | a complex change's implementation and validation plan | design, discovery or milestone changes |
| ADR + decision log | an architectural choice and its consequences | a durable architectural decision is made |
| Local handoff | exact checkout, active files, next action and remaining gaps | handing off or pausing a task |
| Run receipts | immutable measurements, failures and execution conditions | each experiment run |

Link records instead of copying the same narrative into every file. An ADR does
not turn a proposal into measured evidence. Close a local delivery task after
merge and validation; preserve failed/aborted experiment receipts.

## Local state has a home

The tracked root contains product/package entrypoints. Use these ignored paths:

| Local path | Contents |
|---|---|
| `.seocho/worktrees/` | isolated task and clean-main checkouts |
| `.seocho/cache/` | pytest, Ruff and mypy caches |
| `.seocho/workspace/` | inventory, cleanup receipts and local navigation |
| `.seocho/archive/` | reversible backups of reviewed local clutter |
| `.seocho/benchmarks/`, `.seocho/platform/` | existing experiment/setup evidence |
| `.seocho/venvs/` or `.venv/` | Python environments |
| `data/`, `outputs/` | existing data/results; preserve original experiment paths |

Tool-specific paths (`.agents`, `.claude`, `.codex`, `.beads`, `.serena`) keep
names required by their tools. Moving them blindly breaks tool discovery.
Only the shared `.claude/skills/` exception may be tracked. Existing experiment
artifacts are not cache: inventory them before proposing relocation or deletion.

## Validate and land

Run the focused tests while editing, then `make agent-check` for the complete
basic gate. Include exact commands and gaps in the PR. A mock or offline dry-run
is not proof of real backend compatibility or improved answer quality. Before a
new benchmark, read local experiment memory and prior receipts as directed in
`docs/BENCHMARKS.md` and the experiment platform guide.

Review the staged diff, run `git diff --check`, rebase against the current main,
and land only the scoped change. Verify a clean checkout aligned with
`origin/main`. Keep ongoing research changes in their own checkout. Do not force
reset the original folder to make a status display look clean.
