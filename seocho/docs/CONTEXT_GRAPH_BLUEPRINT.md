# Context Graph Blueprint For SEOCHO

## 1. Why This Matters

Context graphs are not just storage. They are the runtime control surface that decides:

- what an agent sees,
- what it is allowed to do,
- what memory is durable vs ephemeral,
- and how decisions are audited.

For SEOCHO, this becomes the backbone of agent-driven development rather than an optional RAG add-on.

## 2. Core Framing

Adopting the two-article framing in SEOCHO terms:

- Two clocks:
  - Fast clock: per-task interaction (prompting, tool calls, handoff, checks).
  - Slow clock: schema, ontology, policy, quality gate evolution.
- Coordinate systems:
  - Identity: who acted (human/agent/service).
  - Time: when context was valid and when it changed.
  - Intent: which issue/task/goal the action belongs to.
  - Authority: what policy allowed or blocked the action.
  - Provenance: where facts came from and whether they were verified.

## 3. SEOCHO Mapping

### 3.1 Control Plane (Slow Clock)

- `seocho/mayor/rig/`: canonical policy + orchestration context.
- `seocho/refinery/rig/`: readiness, merge, and verification orchestration.
- `seocho/docs/`: operational protocol and policy docs.

Slow-clock responsibilities:

- context schema versioning,
- policy updates (what agents can read/write/execute),
- quality gate definitions and thresholds,
- migration rules for graph and metadata.

### 3.2 Data Plane (Fast Clock)

- `seocho/crew/hardy/`: human and agent execution surface.
- `scripts/ops-check.sh`, `scripts/gt-land.sh`: operational signals and landing traces.
- issue tracking (`bd`): intent and status transitions.

Fast-clock responsibilities:

- bind each action to an issue/task ID,
- capture command, result, artifact, and decision edge,
- keep short-lived working context separate from durable memory.

## 4. Minimum Context Graph Schema (v0)

### Node Types

- `Task`: issue-level unit (`bd` issue).
- `Run`: one execution slice in a session.
- `Artifact`: files, logs, patches, test reports.
- `Decision`: accepted/rejected path with rationale.
- `Policy`: rule or gate in effect.
- `Actor`: human, agent, automation process.

### Edge Types

- `Actor -> Task`: `owns`, `assists`, `reviews`
- `Run -> Task`: `implements`, `validates`
- `Run -> Artifact`: `produced`, `modified`, `consumed`
- `Decision -> Artifact`: `approves`, `rejects`, `depends_on`
- `Policy -> Run`: `constrains`, `permits`, `blocks`
- `Artifact -> Artifact`: `derived_from`

### Required Properties

- `id` (stable)
- `ts_start`, `ts_end` (ISO 8601)
- `scope` (`crew`, `mayor`, `refinery`, `witness`, `town`)
- `confidence` (0.0-1.0 for extracted/inferred links)
- `source_ref` (command, file path, issue ID, commit SHA)

## 5. Execution Loops

### 5.1 Fast Loop (Per Task)

1. Claim task (`bd update ... in_progress`).
2. Start `Run` node with actor + environment snapshot.
3. Execute changes and checks.
4. Store produced artifacts and decision links.
5. Close with test result + landing result + push confirmation.

### 5.2 Slow Loop (Weekly/Release)

1. Review failure/reopen/hotfix clusters from graph.
2. Update policies and gates.
3. Migrate schema/version as needed.
4. Validate regressions in trace completeness and reproducibility.

## 6. Semi-Automation Design (Immediate)

### 6.1 What To Auto-Capture First

- command summaries (`gt doctor`, test/lint/build commands),
- issue transitions (`ready` -> `in_progress` -> `closed`),
- landing events (pull/rebase/sync/push),
- produced logs and changed files.

### 6.2 How To Integrate With Existing Scripts

- Extend `scripts/ops-check.sh` to emit structured JSON summary.
- Extend `scripts/gt-land.sh` to write a `Run` record on success/failure.
- Add a lightweight `scripts/context-log.sh` to append normalized events.

## 7. Quality Gates For Context Graph Adoption

Phase 1 gates:

- every merged task has linked issue ID and check artifact,
- landing status is machine-verifiable,
- no credential or machine-local path leaks in stored context.

Phase 2 gates:

- provenance coverage for key decisions >= 90%,
- reproducible run rate >= 85%,
- reopen rate trend decreasing over rolling 4 weeks.

## 8. Rollout Plan

1. Week 1: schema v0 + JSON capture in existing scripts.
2. Week 2: graph materialization job and simple query dashboard.
3. Week 3: policy checks wired to merge readiness.
4. Week 4: retrospective and schema v1 adjustments.

## 9. Immediate Next Tasks

- define canonical event JSON schema,
- patch `ops-check` and `gt-land` emitters,
- add one command to inspect latest task context trail,
- add playbook section for context graph debugging.
