# Reproducible platform foundation

## Purpose

Make the reviewed main checkout installable and testable using a committed
dependency resolution, without replacing a contributor's existing virtualenv.
This lands the independent environment/CI portion of the 2026-09-13 platform
audit. The wider local experiment implementation remains separately reviewable.

## Progress

- [x] Preserve the original source snapshot, experiment artifacts and environment.
- [x] Isolate landing from current main and the PR backlog consolidation.
- [x] Track `uv.lock`, select Python 3.11 locally, preserve the CI version matrix.
- [x] Provide `platform-setup`, `platform-check`, and `platform-ci`.
- [x] Correct stale compose and tracing guidance relevant to setup.
- [x] Validate setup in a fresh environment; final main-based basic CI:
  1,133 passed, 3 skipped.
- [x] Check offline lock validity, SDK import path and docs contracts.
- [ ] Pass required GitHub checks and merge into main.

## Decisions

Semantic validity: retain original experiment receipts and their source/config
identity. This change makes no answer-quality or live service compatibility claim.

API and maintenance: exclude runner wrappers that depend on the uncommitted
specialist harness. A main-branch command must execute against main-branch code.
Use the standard extras rather than build a second dependency manager.

Systems: a separate environment avoids disturbing running experiments. Pin the
resolved dependency graph with the lock, while keeping project compatibility
ranges deliberate. Explicit CI `UV_PYTHON` prevents `.python-version` from
silently collapsing the Python 3.10/3.11/3.12 matrix.

## Validation and acceptance

Run `make platform-setup`, `make platform-check`, `make platform-ci`,
`bash scripts/ci/check-doc-contracts.sh`, and the required GitHub checks. Confirm
the import path is the fresh landing checkout and the original environment is
unchanged. Check lock validity offline, root hierarchy, agent docs, and Git diff.

## Outcomes

Pending validation and landing. No datasets, credentials, local tracker state,
generated traces, or service containers are part of this PR.
