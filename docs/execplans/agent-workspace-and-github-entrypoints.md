# Agent workspace and public entrypoints

## Purpose / Big Picture


A contributor or coding agent should quickly find the right code and validation
without sorting through private experiment state. The public landing page should
explain SEOCHO, its actual prerequisites and how users evaluate their own data.
Local tasks: seocho-89go (workspace) and seocho-6gbq (GitHub).

## Progress


- [x] Inventory original checkout, local directories and worktrees; preserve research.
- [x] Implement read-only doctor and explicit isolated task checkout commands.
- [x] Record actual reversible cleanup and a local workspace index.
- [x] Redesign README, contributor navigation and public intake templates.
- [x] Validate, review and merge PR #676; update GitHub metadata and verify main.

## Surprises & Discoveries


The original checkout has 1778 tracked files plus substantial unfinished work.
Empty demos/notebooks and generated test/lint caches contribute avoidable root
clutter. Most bytes are actual local evidence: .seocho about 9.7 GiB and outputs
about 2.2 GiB; deleting them would destroy research continuity. Tool-specific
folders use discovery conventions and cannot all be renamed to one agent folder.
GitHub's description promises an "ultimate GraphRAG system", with no homepage or
topics; this is less precise than the SDK/runtime's current product contract.

## Decision Log


DEV-DECISION: one task, one isolated checkout; central ignored navigation, typed
commands and purpose-specific linked records. ADR-0231 owns the workspace choice.
DEV-CONSTRAINT: never overwrite unfinished research, reset the original checkout,
move original experiment receipts, or publish local agent state.
DEV-API-CONTRACT: make agent-doctor is read-only; make agent-start TASK=<id>
uses a local ref and refuses collisions; make agent-check runs existing basic CI.
DEV-ACCEPTANCE: real Git fixture tests prove preservation and isolation; local
before/after receipts account for every move; public links/config examples work.

## Outcomes & Retrospective


Local cleanup moved pytest/Ruff caches under .seocho/cache and removed only
empty demos/notebooks directories. The private receipt verifies hashes for 1777
tracked files with no content changes and unchanged Git status. All original
datasets/results/tool overlays are preserved. Ten disposable real-Git helper
tests pass. README/contribution/issue guidance is rewritten; ADR-0232 records
the public entrypoint choice. Local Basic CI tests: 1171 passed, 5 skipped. Runtime/module/import/root/docs
checks pass. The identity check rejected the synthetic `test` at the reserved invalid domain
address, so the disposable Git fixture now uses the allowed example.com domain;
no real address was exposed. Final identity and both site checks passed. PR #676 merged at 95dce428;
GitHub about metadata was updated and read back successfully.
No disk-space or developer-productivity improvement is claimed.

## Context and Orientation


AGENTS.md owns execution rules; docs/AGENT_WORKFLOW.md provides commands and
handoff navigation. .seocho holds ignored local state. README is the public
landing, CONTRIBUTING the human/agent development loop, and docs/README.md the
detailed navigation. GitHub issue/PR forms collect concrete reproduction and
validation evidence. Existing docs-site generators own website mirrors.

## Plan of Work


Separate local hygiene from public architecture. Inventory and move only reviewed
caches/empty clutter; establish a clean-main checkout pointer and task receipts.
Then rewrite README around the product loop, actual Bolt prerequisites, own-data
E2E evidence and contribution paths. Align docs index, llms.txt and issue/PR
forms. Validate local helper behavior, docs contracts and the site workflows.

## Concrete Steps


Run python3 scripts/workspace/manage.py doctor. Use disposable Git tests before
creating the maintainer's next task. Record cleanup under .seocho/workspace with
checksums; preserve original locations of data/results. Run basic CI and docs/
root/agent lint checks. Review scoped PRs before merging. Update GitHub about
metadata only to match the published README and verified documentation URL.

## Validation and Acceptance


Tests cover dirty inputs, unchanged current branch, spaces in paths, existing
tasks, invalid refs/names and no-state read-only inspection. Validate Markdown
links and YAML forms, first-run CLI examples, root contracts, package lock and
required GitHub checks. Report which checks use fixtures versus actual services.

## Idempotence and Recovery


Start refuses existing branch/path. Cleanup archives include path mappings and
hashes; restore a reviewed cache by moving it back only if the original path is
free. Do not delete new cache entries to restore an older snapshot. Worktrees are
preserved on partial helper failure and discoverable with doctor/git worktree list.

## Artifacts and Notes


Private cleanup inventories and local navigation are under .seocho/workspace.
Public PRs reference ADR/ExecPlan and validation summaries, not private datasets.

## Interfaces and Dependencies


The workspace helper uses Python's standard library and Git. It requires no
agent vendor, credentials or hosted tracker. Beads remains a maintainer-local
requirement; public contributors can use GitHub task identifiers.

## Delivery record — 2026-09-13


The clean main checkout now lives in the common repository’s ignored
.seocho/worktrees/main, with a freshly synchronized locked dev environment.
make agent-doctor reports zero local changes there. make agent-check on that
checkout reports 1171 passed, 5 skipped, 15 warnings, plus passing repository
contracts. GitHub Python 3.10/3.11/3.12 and both site checks passed for PR #676.

GitHub now describes ontology-aligned middleware, links https://seocho.blog/,
and has nine relevant technical topics. Original tracked research-file hashes
remain unchanged. A separate untracked IAM-policy file appeared after cleanup
and was left intact; the immediate before/after cleanup Git status was identical.
The local workspace receipt records that later observation separately.

No dataset, original result or tool-discovery directory was deleted. Cache moves
were location cleanup, not a disk-space-reclamation claim. Validation and PR
summaries were copied from temporary checkouts into .seocho/workspace so the
completion evidence survives temporary-directory cleanup.
