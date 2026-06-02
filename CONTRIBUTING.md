# Contributing to SEOCHO

Thanks for considering a contribution. SEOCHO is ontology-aligned middleware for
agents and graph databases, so the most useful PRs keep SDK behavior, runtime
policy, examples, and docs aligned around that contract.

## Start Here

1. Read `README.md` and `QUICKSTART.md` to understand the public user path.
2. Read `docs/REPOSITORY_LAYOUT.md` before adding or moving files.
3. Read `docs/MODULE_OWNERSHIP_MAP.md` before changing SDK/runtime behavior.
4. For larger changes, check `docs/WORKFLOW.md` and the relevant ADRs under
   `docs/decisions/`.

Coding agents should also follow `AGENTS.md`.

## Local Setup

```bash
git clone git@github.com:tteon/seocho.git
cd seocho
pip install -e ".[dev]"
python -m pytest tests/seocho/ -q
```

The canonical CI command is:

```bash
bash scripts/ci/run_basic_ci.sh
```

Use focused tests while developing, then run the broader check before opening a
PR when you changed SDK, runtime, CI, packaging, or shared docs contracts.

## Where To Make Changes

| Goal | Start here |
|---|---|
| Public SDK facade or client behavior | `src/seocho/client.py`, `src/seocho/session.py`, `src/seocho/models.py` |
| Local indexing or graph shaping | `src/seocho/index/`, `src/seocho/rules.py` |
| Query, retrieval, Cypher, or answer synthesis | `src/seocho/query/`, `src/seocho/prompt_strategy.py` |
| Ontology model or offline governance | `src/seocho/ontology*.py`, `docs/ontology/` |
| Runtime API, memory service, or policy checks | `runtime/` |
| Legacy extraction compatibility | `extraction/` |
| Examples, tutorials, or sample data | `examples/` |
| Public docs | `README.md`, `QUICKSTART.md`, `docs/`, `website/` |
| GitHub Actions or repository automation | `.github/workflows/`, `scripts/ci/` |

New canonical engine logic should usually land under `src/seocho/`. Treat
`extraction/` as active compatibility and batch-service surface, not the first
home for new product behavior.

## Repository Hygiene

- Do not add root-level product folders without updating
  `docs/REPOSITORY_LAYOUT.md`.
- Do not commit generated data, benchmark results, credentials, or local tool
  state.
- Keep local overlays such as `.agents/`, `.beads/`, `.claude/`, `.jules/`,
  `.serena/`, `.seocho/`, `data/`, `logs/`, and `outputs/` out of Git.
- Put reusable automation in `scripts/`, not inline workflow-only scripts.

## Pull Requests

1. Open an issue or explain the problem clearly in the PR.
2. Keep the PR focused on one behavior change, refactor, or docs improvement.
3. Add or update tests for changed behavior.
4. Run relevant validation and include exact commands in the PR body.
5. Update user-facing docs when public behavior changes.
6. Use a conventional commit prefix such as `feat:`, `fix:`, `docs:`,
   `refactor:`, `test:`, or `chore:`.

Maintainers make final merge decisions. Automated checks and coding-agent
reviews are supporting signals, not a replacement for human review.

## Automation And AI-Assisted Work

SEOCHO uses GitHub Actions for CI, docs checks, docs deploy, and narrow
maintainer automation. The public automation map is
`docs/GITHUB_AUTOMATION.md`.

Scheduled Codex workflows may open draft maintenance PRs. They must stay
bounded, test-backed, and draft-only until a maintainer promotes them. External
AI-assisted contributions are welcome when the author understands the change,
keeps scope tight, and provides real validation evidence.

Comment-based merge is maintainer-only and intentionally narrow: the command is
exactly `/go`, the PR must be clean and non-draft, and the merge is squash.

## Architecture Summary

SEOCHO has two primary planes sharing one ontology:

- Data plane: `src/seocho/index/` ingests files, shapes graph payloads, and
  applies rule/validation logic.
- Control plane: `src/seocho/query/` turns ontology context into Cypher,
  evidence, and answers.
- Runtime shell: `runtime/` exposes policy-checked API behavior and preserves
  `workspace_id`.

For deeper context, read `docs/ARCHITECTURE.md`,
`docs/MODULE_OWNERSHIP_MAP.md`, and `docs/GRAPH_RAG_AGENT_HANDOFF_SPEC.md`.
