# Repository Hierarchy Review

This review captures the current architecture hygiene assessment for SEOCHO's
repository and GitHub hierarchy. It is intentionally action-oriented: use it to
pick small cleanup PRs, not as a mandate for a broad repo reshuffle.

## Reference Frame

The review uses these established software architecture references:

| Reference | Principle used here |
|---|---|
| David Parnas, "On the Criteria To Be Used in Decomposing Systems into Modules" | Hide volatile decisions behind stable module boundaries. |
| Martin Fowler, *Refactoring* | Improve structure through small behavior-preserving steps. |
| Eric Evans, *Domain-Driven Design* | Keep bounded contexts explicit so domain concepts do not leak across unrelated surfaces. |
| Robert C. Martin, *Clean Architecture* | Keep policy and core rules independent from delivery mechanisms. |
| Matthew Skelton and Manuel Pais, *Team Topologies* | Reduce cognitive load by making ownership and interaction paths obvious. |

## Current Assessment

SEOCHO has the right architectural direction, but the repository still makes
contributors pay too much orientation cost. The current Python layout is also
not the desired long-term shape: root-level import packages make local imports
too forgiving and hide packaging mistakes that a `src/` layout would expose
earlier.

The intended canonical split is now:

- `src/seocho/` owns the distributable SDK package, indexing, query,
  ontology, and engine behavior.
- `runtime/` owns deployment-shell behavior, runtime policy, and API wiring.
- `extraction/` is still visible as active extraction plus compatibility shims
  during the staged `extraction/` to `runtime/` migration.
- `examples/datasets/`, `docs/assets/`, and `docs/ontology/` own the assets
  that previously lived in root `dataset/`, `images/`, and `ontology/`.
- `.github/` owns hosted GitHub workflows and Codex workflow prompts.
- Local agent/editor/tool state such as `.beads/`, `.agents/`, `.claude/`,
  `.githooks/`, `.jules/`, and `.serena/` is not part of the public repository
  contract.

The rough edges are mostly hierarchy and source-of-truth issues rather than a
single broken abstraction.

Current packaging reality:

- `pyproject.toml` packages only `seocho` and `seocho.*` from `src/`.
- `runtime/`, `extraction/`, `semantic/`, and `evaluation/` are root-level
  Python surfaces used by tests, runtime commands, and compatibility paths.
- `tool.pytest.ini_options.pythonpath = ["src", "extraction"]` keeps tests
  compatible while the extraction shim surface remains in root.

That is an improvement over root-package imports, but still a migration
waypoint rather than the final repository shape.

## Engineering Discussion Summary

A software-engineer review agreed on four points:

- Do not flatten or rename broad packages in one pass; the staged runtime
  migration is already documented and guarded.
- The highest-value cleanup is stronger repository hygiene, not immediate code
  movement.
- README should be product-first; operational and automation details should sit
  behind focused docs.
- GitHub automation is understandable, but the daily and periodic Codex
  workflows duplicate enough logic that future workflow changes will be noisy.

The main caution was against over-cleanup. Local ignored directories and
historical artifacts can look alarming in a live workspace, but many are not
tracked repository surface. Cleanup should start with tracked contracts and
guardrails.

## Agent-Ready Repository References

Three public agent-oriented repositories informed the current AGENTS/CLAUDE
reshape:

| Repository | Pattern to copy | SEOCHO decision |
|---|---|---|
| `openai/openai-agents-python` | Root `AGENTS.md` explains mandatory validation, repo structure, public API compatibility, and PR expectations in one contributor guide. | Keep `AGENTS.md` as the primary agent guide with repo map, edit surfaces, validation table, and landing rules. |
| `langchain-ai/langgraph` | Short `AGENTS.md` maps monorepo packages, tells contributors which commands to run, and includes a dependency impact map. | Keep SEOCHO's agent guide short enough to scan; put ownership and validation before detailed product nuance. |
| `OpenHands/docs` | Agent docs start with quick orientation, key paths, local development commands, and generation/CI checks. | Treat public docs, examples, and automation as first-class surfaces, but keep private agent tool state out of Git. |

The shared lesson is that agent-facing docs should be operational, not
narrative: what this repo is, where to edit, what not to touch, and which checks
prove the work.

## Open-Source Automation Surface Comparison

Three mature open-source repositories informed the `.github/` reshape:

| Repository | Observed pattern | SEOCHO adjustment |
|---|---|---|
| `openai/openai-agents-python` | Product and contributor guidance stay in root docs; `.github` contains templates, scripts, and workflows rather than a second contributor guide. | Keep `.github/README.md` short and point contributors back to `CONTRIBUTING.md`, `AGENTS.md`, and `docs/WORKFLOW.md`. |
| `langchain-ai/langgraph` | `.github` is mostly executable configuration; contributor expectations and package orientation live in external/root contributor docs. | Keep automation implementation details out of the first contributor path; keep module orientation in `docs/MODULE_OWNERSHIP_MAP.md`. |
| `pallets/flask` / `fastapi/fastapi` | Mature Python projects use `.github` for issue/PR templates and workflows; contribution process lives in public docs. | Treat `.github/README.md` as a maintainer automation inventory, not as onboarding. Explain CI and AI-assisted contribution expectations in `CONTRIBUTING.md`. |

SEOCHO differs from these projects because it has scheduled Codex draft-PR
workflows and a comment-triggered merge path. Those are public trust surfaces,
so they deserve a short visible explanation. They should not dominate the
repository's open-source entry points.

Adopted patterns in this repository:

- Keep one primary root agent guide, `AGENTS.md`, with read order, module map,
  impact map, validation, and landing rules.
- Keep `CLAUDE.md` as SEOCHO-specific product and review context instead of a
  duplicate task tracker.
- Make module ownership explicit enough that a new agent can distinguish SDK
  behavior, runtime policy, extraction compatibility, examples, and generated
  local state before editing.
- Treat private agent/tool state as local implementation detail; only `.github/`
  remains a tracked public automation surface, and its README stays a compact
  automation map.

## Problems And Improvements

| Problem | Evidence | Improvement | Engineering plus points |
|---|---|---|---|
| Root hierarchy is noisy | `docs/REPOSITORY_LAYOUT.md` explains canonical roots, while live workspaces may also show ignored runtime state, nested worktrees, and scratch folders. | Add or extend a root-hygiene check that reports undocumented tracked top-level paths. Keep ignored local state out of the public contract. | Faster onboarding, fewer accidental edits to local artifacts, better agent context selection. |
| Python package layout was too permissive | The SDK package used to live at root `seocho/`, alongside root `runtime/`, `extraction/`, `semantic/`, and `evaluation/`. | Move the distributable SDK to `src/seocho`; later either package runtime explicitly or move runtime-only code under a separate app/service namespace. Remove broad test `pythonpath` reliance as compatibility shims shrink. | Catches packaging/import bugs earlier, clarifies what ships to users, reduces accidental dependency on repo-root execution. |
| Canonical ownership is mentally expensive | `runtime/`, `extraction/`, `semantic/`, and `evaluation/` still look product-like from the root. | Continue one seam at a time: move canonical behavior into `src/seocho/` or `runtime/`, then keep `extraction/*` as explicit shims with ownership tests. | Lower coupling, clearer review ownership, less risk of new logic entering legacy paths. |
| README mixes public and internal concerns | README had duplicated docs maps and GitHub automation notes near server-operator content. | Keep README as the fast product entry point and point CI/GitHub details to `.github/README.md`. | Better first impression, less docs drift, clearer public narrative. |
| Hidden local tool directories leaked into public root | `.beads/`, `.agents/`, `.claude/`, and `.githooks/` expose maintainer workflow state ahead of product code. | Remove them from Git tracking, ignore them locally, and keep only `.github/` as the public automation surface. | Cleaner middleware posture, less reviewer confusion, smaller clone surface, fewer accidental local-state edits. |
| One-off retrieval experiments look like product surface | `experiments/retrieval_comparison/` contains a comparison harness plus JSON data but no production dependency. | Remove the tracked experiment directory; keep future experiments private, archived, or promoted only when they become supported examples or benchmarks. | Sharper product boundary, less root noise, fewer unsupported scripts for users to debug. |
| GitHub automation lacks an in-folder map | `.github/workflows/*` and `.github/codex/prompts/*` were discoverable by filename but lacked a local ownership guide. | Add `.github/README.md` documenting workflow roles, Codex lanes, merge rules, and placement rules. | Faster workflow maintenance, safer automation edits, clearer branch/PR contracts. |
| Codex workflow implementations duplicate structure | `daily-codex-maintenance.yml` and `periodic-codex-review.yml` repeat secret checks, setup, validation, PR body construction, and PR creation. | Extract shared behavior into a reusable workflow or script while keeping separate lane files. | Smaller future diffs, fewer inconsistent fixes, easier addition/removal of automation lanes. |
| ADR index is dense and hard to scan | `docs/decisions/DECISION_LOG.md` is long and mixes chronology with narrative summaries. | Add a linted ADR index contract with unique IDs, date, status, and title metadata. | Better architectural memory, easier audits, less decision drift. |

## Recommended Order

1. Land README, `.github/README.md`, public asset relocation, and `src/seocho`
   package layout cleanup.
2. Remove tracked local tool state and one-off experiment harnesses from the
   public root.
3. Write and approve a package-layout ADR for explicit runtime/app ownership
   after the SDK package move.
4. Extract shared Codex workflow helper logic.
5. Pick one `extraction/*` compatibility seam and make its canonical owner
   enforceable in CI.
6. Normalize ADR metadata and make the decision log easier to audit.

## Non-Goals

- Do not move `website/` out of the repo. ADR-0089 intentionally made the docs
  site an in-repo surface.
- Do not commit local agent/editor/tool state as part of public architecture
  cleanup.
- Do not rename `extraction/` wholesale until compatibility paths and tests
  are ready.
- Do not move `runtime/`, `evaluation/`, `semantic/`, or `extraction/`
  wholesale in this same slice. Those need separate runtime/app ownership
  validation after the SDK package layout is stable.
- Do not merge agent, workflow, and product docs into one file; the goal is
  clearer ownership, not fewer files at any cost.
