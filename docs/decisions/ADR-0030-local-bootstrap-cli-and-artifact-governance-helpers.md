# ADR-0030: Local Bootstrap CLI And Artifact Governance Helpers

Date: 2026-03-13
Status: Accepted

## Context

SEOCHO had a usable memory-first SDK and artifact lifecycle surface, but two workflow gaps remained:

- first-run local activation still depended on manual `make up` or direct `docker compose` usage
- advanced artifact governance still required developers to reason about raw payloads without first-class local validation or diff tooling

That combination left the product stronger for experienced maintainers than for developers who want a clear bootstrap path and explicit artifact review loop.

## Decision

SEOCHO will add two user-facing helper surfaces:

1. add `seocho serve` and `seocho stop` as local runtime bootstrap commands for repository-based usage
2. let `seocho serve` inject a fallback local `OPENAI_API_KEY` when the environment is missing a real key or still uses the example placeholder
3. add local artifact governance helpers in the SDK and CLI:
   - `validate`
   - `diff`
   - `apply`
4. keep artifact `apply` as an ingest-time operation built on the existing approved-artifact runtime contract, rather than introducing a new global baseline mutation API

## Consequences

Positive:

- first-run onboarding becomes one command closer to the intended memory-first product surface
- advanced developers can review semantic artifacts before approval without building custom scripts
- governance helpers remain deterministic because validation and diffing run on typed local models

Tradeoffs:

- `seocho serve` is intentionally repository-local and does not attempt a general installer/orchestrator abstraction
- fallback local bootstrap improves activation, but it does not replace a real `OPENAI_API_KEY` for production extraction quality
- artifact validation remains structural and governance-oriented, not a substitute for full downstream runtime evaluation

## Implementation Notes

- local runtime helpers live in `seocho/local.py`
- artifact governance helpers live in `seocho/governance.py`
- CLI surface lives in `seocho/cli.py`
- docs are updated in `README.md`, `docs/QUICKSTART.md`, and `docs/PYTHON_INTERFACE_QUICKSTART.md`
