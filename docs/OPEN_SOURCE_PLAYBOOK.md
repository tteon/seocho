# SEOCHO Open Source Playbook

This playbook defines a structured onboarding plan for open-source contributors,
with explicit licensing, documentation, and delivery gates.

## 1. Onboarding Outcomes

By the end of onboarding, a contributor should be able to:

1. run SEOCHO locally and verify ingest -> semantic/debate chat
2. deliver one scoped change with tests and docs
3. pass licensing and release checklists without maintainer rework

## 2. 14-Day Onboarding Plan

| Phase | Timebox | Goal | Required Output |
|---|---|---|---|
| Phase 0 | Day 0 | Environment and policy baseline | local run success (`make up`) and docs read complete |
| Phase 1 | Day 1-3 | First reproducible data flow | successful raw ingest + semantic query against sample DB |
| Phase 2 | Day 4-7 | First contribution | one PR with focused tests + docs updates |
| Phase 3 | Day 8-14 | Ownership readiness | one follow-up item created and one review completed for another PR |

## 3. Phase Checklists

### 3.1 Phase 0: Baseline Setup

- read in order: `README.md` -> `CLAUDE.md` -> `docs/WORKFLOW.md` -> `docs/ISSUE_TASK_SYSTEM.md` -> `docs/decisions/DECISION_LOG.md`
- validate stack assumptions:
  - OpenAI Agents SDK
  - vendor-neutral tracing/evaluation with Opik as the preferred team backend
  - DozerDB backend
  - single-tenant with `workspace_id` propagation
  - Owlready2 only in offline ontology path
- run local runtime:

```bash
cp .env.example .env
make up
```

### 3.2 Phase 1: First End-to-End Flow

- ingest raw records using `/platform/ingest/raw`
- ensure fulltext index with `/indexes/fulltext/ensure`
- run semantic mode chat and confirm route/answer payload
- capture command outputs in PR/issue notes for reproducibility

### 3.3 Phase 2: First PR

- select one scoped item (bug fix, docs improvement, or small feature)
- keep change set atomic and testable
- add/adjust tests for changed behavior
- update user-facing docs when behavior changes
- describe test evidence and known gaps in PR

### 3.4 Phase 3: Ownership Readiness

- create one follow-up work item for deferred improvements
- review one peer PR against runtime guardrails and docs contract
- verify you can run the landing checklist without maintainer assistance

## 4. License and Compliance Baseline

Repository license model:

- project license: MIT (`LICENSE`)
- contribution model: inbound = outbound (all accepted contributions stay MIT)

Contributor compliance checklist:

1. confirm new code is original or from MIT-compatible sources
2. avoid copying code with incompatible license terms
3. when adding dependencies, record package/version/license in PR description
4. do not add secrets, keys, or credentials to code or docs
5. keep security-sensitive reports in `SECURITY.md` process (not public issue first)

## 5. Work Intake and Tracking

Use GitHub issues and pull requests for public work state. Local maintainer
tools are fine for private coordination, but their state directories are not
part of the public repository.

Use the issue templates for public intake:

- bug reports: reproducible defect, failing command, expected vs actual result
- feature requests: user workflow, proposed contract, acceptance criteria
- docs/examples: confusing page, missing example, validation command

Active work items must include collaboration labels:

- `sev-*`, `impact-*`, `urgency-*`, `sprint-*`, `roadmap-*`, `area-*`, `kind-*`

The repository syncs its public label vocabulary from `.github/labels.json`.
The `Triage Metadata` workflow applies first-pass labels from issue form fields,
PR titles, and PR changed-file paths. Maintainers still own final severity,
priority, roadmap, and sprint decisions.

Use `good first issue` only when the change can be completed without deep
runtime/query migration context. Good candidates are docs wording, run-spec
examples, small tests, validation messages, and onboarding inconsistencies.

Validation commands:

```bash
bash scripts/ci/run_basic_ci.sh
scripts/pm/lint-agent-docs.sh
```

## 6. Runtime and Architecture Guardrails

When extending runtime behavior:

- preserve `workspace_id` in request/model contracts
- enforce runtime policy checks (`runtime/policy.py`)
- keep heavy ontology reasoning out of hot path
- preserve trace topology contract (`node_id`, `parent_id`, `parent_ids`)

Public plugin surfaces:

- `GraphStore` for graph database backends
- `VectorStore` for vector similarity backends
- `LLMBackend` for chat-completion providers
- `EmbeddingBackend` for embedding providers

Everything else is an internal contract unless a new ADR explicitly promotes
it. Query flow, indexing internals, runtime bundles, and agent factories should
not be treated as open subclass/plugin surfaces.

## 7. Minimum Reproducible Workflow

### 7.1 Ingest your own records

```bash
curl -sS -X POST http://localhost:8001/platform/ingest/raw \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id":"default",
    "target_database":"mydomain",
    "records":[
      {"id":"r1","content":"Entity A acquired Entity B."},
      {"id":"r2","content":"Entity B supplies analytics to Entity C."}
    ]
  }' | jq .
```

### 7.2 Ensure semantic index

```bash
curl -sS -X POST http://localhost:8001/indexes/fulltext/ensure \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id":"default",
    "databases":["mydomain"],
    "create_if_missing":true
  }' | jq .
```

### 7.3 Query with semantic mode

```bash
curl -sS -X POST http://localhost:8001/platform/chat/send \
  -H "Content-Type: application/json" \
  -d '{
    "session_id":"oss_semantic_1",
    "message":"What entities are linked in mydomain?",
    "mode":"semantic",
    "workspace_id":"default",
    "databases":["mydomain"]
  }' | jq '{assistant_message, route: .runtime_payload.route}'
```

## 8. Quality Gates Before PR

Run relevant gates before opening a PR:

```bash
bash scripts/ci/run_basic_ci.sh
```

Use narrower commands while developing, then run the relevant broader gate
before review. Examples:

```bash
uv run pytest tests/seocho/test_run_spec.py -q
bash scripts/ci/check-doc-contracts.sh
make e2e-smoke
scripts/pm/lint-agent-docs.sh
```

If a relevant suite is not run, state the exact gap and reason in the PR.

## 9. Documentation Contract

For architecture or workflow changes, update all applicable:

- `README.md`
- relevant `docs/*` pages
- `docs/decisions/ADR-*.md`
- `docs/decisions/DECISION_LOG.md`

Docs sync critical set for seocho.blog:

- `docs/README.md`
- `docs/RUNTIME_DEPLOYMENT.md`
- `docs/ARCHITECTURE.md`
- `docs/WORKFLOW.md`

The tracked site source lives in `website/`, and generated mirror files under
`website/src/content/docs/docs/` are derived from the repo-root docs.

## 10. Open Source Workflow Backlog

Use this as the maintainer TODO list for the next operational improvements:

- backfill existing unlabeled issues and PRs with the new label vocabulary before
  relying on dashboard or roadmap views
- add CODEOWNERS after stable maintainer ownership is clear
- add release-note drafting once merged PR volume is large enough to need it
- add a weekly stale-review report before enabling any stale-closing bot
- add contributor welcome/comment automation only after the label workflow has
  enough signal to avoid noisy or misleading comments
- add a security workflow review after `SECURITY.md` claims are reconciled with
  the workflows that actually exist
