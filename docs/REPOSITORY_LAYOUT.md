# Repository Layout

This document explains which top-level directories are active product surfaces,
which ones exist for contributor tooling, and which ones are legacy or
local-only.

Use this when the repo root feels crowded and you need to know where new work
should actually go.

## Active Product Code

| Path | Role | Notes |
|---|---|---|
| `src/seocho/` | Canonical SDK engine | Distributable Python package; primary owner for indexing, query, ontology, and client engine logic. |
| `runtime/` | Canonical deployment shell | Active runtime package for server composition and policy-facing runtime wiring. |
| `extraction/` | Extraction + compatibility layer | Still active, but many modules are staged shims during the `extraction/` -> `runtime/` migration. |
| `evaluation/` | Platform UI/backend | Static UI plus proxy/backend for the local platform path. |
| `scripts/` | Ops, CI, demo, and PM helpers | Preferred home for repo automation. |
| `docs/` | Product and operator contracts | Source-of-truth docs shipped with the repo. |
| `tests/` | Top-level regression anchors | Most focused tests still live nearer to the owning package. |

## Root Files

Root files should be limited to standard repository entry points, package/build
metadata, and one-command local stack entry points.

| Path | Keep in root? | Role |
|---|---:|---|
| `README.md`, `QUICKSTART.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`, `LICENSE` | yes | Standard public project entry points |
| `AGENTS.md`, `CLAUDE.md` | yes | Coding-agent orientation and SEOCHO-specific guardrails |
| `pyproject.toml`, `Makefile`, `.env.example`, `.gitignore`, `.dockerignore` | yes | Python packaging, common commands, and repo/tool defaults |
| `docker-compose.yml`, `docker-compose.dev.yml`, `docker-compose.tutorials.yml` | yes | Local stack entry points; kept at root for familiar `docker compose -f ...` usage |
| `.gitattributes` | only with active rules | Do not keep an empty placeholder |
| `setup_*.sh` | no | Put setup helpers under `scripts/setup/` |
| generated data, logs, exports, scratch PR bodies | no | Keep ignored and outside the public repository surface |

## Contributor Tooling Metadata

These directories are intentional and remain part of the tracked public repo
contract.

| Path | Role |
|---|---|
| `.github/` | GitHub Actions workflows and Codex automation prompt contracts |

## Developer-Local Tool Overlays

These paths are local tool state or personal agent overlays. They stay ignored
and must not be tracked as part of the public GitHub surface.

| Path | Role |
|---|---|
| `.agents/` | Local agent skills, coordination state, or seam reservations |
| `.beads/` | Local Beads task/status database and runtime state |
| `.claude/` | Local Claude settings, hooks, and skills |
| `.githooks/` | Local Git hook experiments |
| `.jules/` | Jules local configuration/prompts |
| `.serena/` | Serena local configuration/cache |

## Learning And Reference Assets

| Path | Status | Notes |
|---|---|---|
| `examples/` | Canonical hub | Preferred home for runnable notebooks, datasets, demos, and example configs. |
| `examples/datasets/` | Active reference | Small tracked datasets used by tutorials, smoke tests, and documented benchmark samples. |
| `examples/demos/` | Active reference | Demo scripts and tracing examples; useful for targeted walkthroughs, not the primary docs path. |
| `examples/labs/legacy/` | Legacy | Older exploratory notebooks retained for reference; do not add new onboarding notebooks here. |
| `examples/teaching/` | Active reference | Longer-form teaching/course material. |
| `docs/assets/` | Active reference | README/docs images and other public documentation assets. |
| `docs/ontology/` | Active reference | Ontology guidance documents that are not executable examples. |
| `docs/archive/` | Historical | Kept for reference only, not current contract. |

## Secondary Or Compatibility Surfaces

| Path | Status | Notes |
|---|---|---|
| `semantic/` | Secondary/legacy service surface | Keep changes narrow; active runtime paths now center on `runtime/` and canonical query modules. |
| `seocho-core/` | Optional accelerator workspace | Rust/Python hybrid support code, not the first stop for normal app changes. |

## Local Runtime State And Generated Artifacts

These paths are usually not where feature work should land.

| Path | Role |
|---|---|
| `data/` | Local graph/runtime state |
| `logs/` | Local logs |
| `outputs/` | Generated evaluation or export output |
| `data/neo4j/` | Local Neo4j/DozerDB data, logs, import files, and plugins |
| `build/`, `dist/`, `seocho.egg-info/` | Build artifacts |
| `dolt/` | Local Dolt runtime state when a developer tool creates it |

## Compose Files

Only two compose files are part of the tracked repo contract:

| Path | Role |
|---|---|
| `docker-compose.yml` | Default image-backed local stack |
| `docker-compose.dev.yml` | Live-mount overlay used with `make up-live` / `make dev-up` |

There is no tracked `docker-compose.prod.yml` in this repository. Production
overrides should be deployment-specific instead of implied by the default repo
layout.

## Placement Rules

- Put distributable SDK code under `src/seocho/`; do not reintroduce a root
  `seocho/` package.
- Put new runnable notebooks, datasets, demos, and sample configs under `examples/`.
- Put documentation images under `docs/assets/`, not root `images/`.
- Put ontology guidance under `docs/ontology/`, not root `ontology/`.
- Keep exploratory or deprecated notebook material under `examples/labs/legacy/`,
  not as new repo-root directories.
- Put shared contributor automation under `scripts/`.
- Put GitHub-hosted workflows and Codex workflow prompts under `.github/`; put
  reusable workflow helper scripts under `scripts/`.
- Keep generated local state under ignored artifact paths such as `data/`,
  `logs/`, `outputs/`, `.seocho/`, and `extraction/output/`.
- Keep local AI/tool overlays such as `.agents/`, `.beads/`, `.claude/`,
  `.githooks/`, `.jules/`, and `.serena/` out of Git tracking.
- If you add a new top-level directory, update this document and the relevant
  README entry point in the same change.
