# Repository Layout

This document explains which top-level directories are active product surfaces,
which ones exist for contributor tooling, and which ones are legacy or
local-only.

Use this when the repo root feels crowded and you need to know where new work
should actually go.

## Active Product Code

| Path | Role | Notes |
|---|---|---|
| `seocho/` | Canonical SDK engine | Primary owner for indexing, query, ontology, and client engine logic. |
| `runtime/` | Canonical deployment shell | Active runtime package for server composition and policy-facing runtime wiring. |
| `extraction/` | Extraction + compatibility layer | Still active, but many modules are staged shims during the `extraction/` -> `runtime/` migration. |
| `evaluation/` | Platform UI/backend | Static UI plus proxy/backend for the local platform path. |
| `scripts/` | Ops, CI, demo, and PM helpers | Preferred home for repo automation. |
| `docs/` | Product and operator contracts | Source-of-truth docs shipped with the repo. |
| `tests/` | Top-level regression anchors | Most focused tests still live nearer to the owning package. |

## Contributor Tooling Metadata

These directories are intentional and remain part of the tracked repo contract.

| Path | Role |
|---|---|
| `.agents/` | Codex skills and Gastown shared-seam registry |
| `.beads/` | Canonical task/status tracker metadata |
| `.claude/` | Shared Claude settings, hooks, and repo-managed skills |
| `.githooks/` | Repo-managed Git hooks |

## Developer-Local Tool Overlays

These paths are local tool state or personal agent overlays. They should stay
ignored and should not be treated as part of the public GitHub surface.

| Path | Role |
|---|---|
| `.jules/` | Jules local configuration/prompts |
| `.serena/` | Serena local configuration/cache |

## Learning And Reference Assets

| Path | Status | Notes |
|---|---|---|
| `examples/` | Canonical hub | Preferred home for runnable notebooks, datasets, demos, and example configs. |
| `examples/demos/` | Active reference | Demo scripts and tracing examples; useful for targeted walkthroughs, not the primary docs path. |
| `examples/labs/legacy/` | Legacy | Older exploratory notebooks retained for reference; do not add new onboarding notebooks here. |
| `examples/teaching/` | Active reference | Longer-form teaching/course material. |
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
| `neo4j/` | Local Neo4j/DozerDB plugins and state helpers |
| `build/`, `dist/`, `seocho.egg-info/` | Build artifacts |
| `dolt/` | Local Beads/Dolt runtime state |

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

- Put new runnable notebooks, datasets, demos, and sample configs under `examples/`.
- Keep exploratory or deprecated notebook material under `examples/labs/legacy/`,
  not as new repo-root directories.
- Put new contributor automation under `scripts/`, `.agents/`, or `.githooks`
  depending on purpose.
- Keep generated local state under ignored artifact paths such as `data/`,
  `logs/`, `outputs/`, `.seocho/`, and `extraction/output/`.
- Keep local AI tool overlays such as `.jules/` and `.serena/` out of Git
  tracking unless the repo explicitly adopts them as a shared contract.
- If you add a new top-level directory, update this document and the relevant
  README entry point in the same change.
