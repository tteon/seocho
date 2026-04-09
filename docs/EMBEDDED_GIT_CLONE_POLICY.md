# Embedded Git Clone Policy

Rig-local clone policy is **ignored paths only**.

## Policy Decision

- Do not use submodules for rig-local helper clones.
- Nested git repositories inside this repo must be ignored by git.
- `gt-doctor` enforces this via `CLEANUP->embedded-git-clones`.

## Why

- avoids accidental `embedded git repository` warnings during normal commits
- keeps parent repository history clean and deterministic
- prevents submodule pointer drift in this MVP workflow

## Allowed Pattern

- local helper clone path is present in `.gitignore` (for example `tteon.github.io/`)

## Disallowed Pattern

- nested repo path tracked by parent repo
- path configured as submodule (`gitlink` mode `160000`)

## Validation

```bash
scripts/gt-doctor.sh
```
