# Agent Development Guide

Date: 2026-03-12
Status: Draft

This guide explains how coding agents should work in this repository beyond the short operational rules in `AGENTS.md`.

## 1. Read Order

Before making changes, read in this order:

1. `README.md`
2. `CLAUDE.md`
3. `docs/WORKFLOW.md`
4. `docs/ISSUE_TASK_SYSTEM.md`
5. `docs/decisions/DECISION_LOG.md`
6. `docs/PRD_MVP.md`
7. this document

If any of those documents disagree, use the stricter interpretation and call out the ambiguity in your handoff.

For document-driven development, also read:

8. `docs/DEVELOPER_INPUT_CONVENTIONS.md`

## 2. Product Direction

The near-term direction is a mem0-graph-memory-like interface on top of SEOCHO's graph-backed runtime.

Practical implication:

- public UX should feel memory-first
- internal runtime may remain graph- and agent-heavy
- new user-facing APIs should prefer stable resource-oriented naming over orchestration jargon
- do not assume the product is trying to replicate the full mem0 surface

## 3. Core Entry Points

### Backend runtime

- `runtime/agent_server.py`
- `runtime/runtime_ingest.py`
- `extraction/semantic_query_flow.py`
- `extraction/debate.py`
- `extraction/rule_api.py`
- `extraction/semantic_artifact_api.py`

Use these when changing behavior, contracts, or orchestration.

### Frontend platform

- `evaluation/server.py`
- `evaluation/static/index.html`
- `evaluation/static/app.js`
- `evaluation/static/styles.css`

Use these when changing user-visible flow, UI behavior, or API proxy expectations.

### Configuration and prompts

- `extraction/config.py`
- `extraction/conf/*`

Use these when changing environment-driven behavior, prompt text, or ingestion defaults.

### Tests

- `extraction/tests/*`
- `semantic/tests/*`

These are the main verification surfaces.

## 4. Safe Edit Surfaces

Most feature work should stay inside:

- `extraction/`
- `evaluation/`
- `scripts/`
- `docs/`

### Usually safe

- endpoint models and handlers
- orchestration logic
- config loading
- prompt/config YAML
- focused tests
- docs and scripts

### Use extra caution

- `docker-compose.yml`
- `pyproject.toml`
- `Makefile`
- `scripts/land.sh`

These affect everyone and may have repo-wide consequences.

### Avoid unless explicitly requested

- `data/`
- `logs/`
- `neo4j/`
- `opik/`
- `extraction/output/`
- bundled sample/binary folders inside `extraction/`
- `tteon.github.io/`

These are runtime artifacts, embedded workspaces, or high-noise areas.

## 5. Common Task Recipes

### Add or change a user-facing API

1. update request and response models
2. keep `workspace_id` explicit
3. enforce policy checks
4. add or update endpoint tests
5. update docs for the changed contract

### Change semantic retrieval behavior

1. inspect `extraction/semantic_query_flow.py`
2. preserve deterministic fallback behavior
3. keep provenance and override handling intact
4. add focused semantic flow tests

### Change ingest behavior

1. inspect `runtime/runtime_ingest.py`
2. preserve artifact and rule lifecycle boundaries
3. keep heavy ontology logic out of hot paths
4. verify ingest plus downstream search or chat flow

### Change UI or public interaction flow

1. inspect `evaluation/server.py` and `evaluation/static/*`
2. confirm proxy paths match backend routes
3. keep the first-run and quickstart path working
4. update docs or screenshots only if the flow changed materially

## 6. Required Invariants

Do not violate these without an explicit architecture decision:

- OpenAI Agents SDK remains the agent runtime baseline
- DozerDB remains the graph backend baseline
- vendor-neutral tracing remains the contract baseline, with Opik preferred for team observability
- `workspace_id` remains part of runtime-facing contracts
- heavy ontology reasoning stays outside request hot paths
- runtime policy checks remain enforced for new actions

## 7. API Design Rules For New Work

Because the product direction is memory-first, new public APIs should prefer:

- nouns over implementation verbs
- consistent top-level response shapes
- stable identifiers
- predictable filtering fields
- expert-mode internals hidden behind simpler defaults

Prefer:

- `POST /api/memories`
- `POST /api/memories/search`
- `GET /api/memories/{id}`

Avoid introducing more public routes that expose implementation detail in their names unless the route is clearly internal or admin-only.

## 8. Verification Matrix

### Minimum

- focused pytest suite for the changed module
- any directly affected API contract tests
- doc updates for user-visible changes

### Required when runtime API or UX changes

- `make e2e-smoke`
- quickstart or first-run path sanity check

### Required when agent docs or workflow rules change

- `scripts/pm/lint-agent-docs.sh`

## 9. Handoff Expectations

Every substantial handoff should include:

- what changed
- why it changed
- tests run
- tests not run
- open risks
- exact file paths touched

## 10. Developer Input Prefixes

When product or engineering documents are used as implementation intake, treat `DEV-*` lines as control markers.

Important markers:

- `DEV-INPUT-REQUIRED`
- `DEV-DECISION`
- `DEV-CONSTRAINT`
- `DEV-ASSUMPTION`
- `DEV-OUT-OF-SCOPE`
- `DEV-ACCEPTANCE`
- `DEV-API-CONTRACT`
- `DEV-DATA-CONTRACT`
- `DEV-TEST-REQUIRED`
- `DEV-FOLLOW-UP`

The canonical rules for these markers live in `docs/DEVELOPER_INPUT_CONVENTIONS.md`.

## 11. Current Repository Weak Spots

Agents should keep these weaknesses in mind while editing:

- `runtime/agent_server.py` is still too large
- import-time singletons increase coupling
- source code and assets are mixed inside `extraction/`
- `semantic/` responsibility is not fully clarified

When possible, make changes that reduce these weaknesses instead of adding to them.
