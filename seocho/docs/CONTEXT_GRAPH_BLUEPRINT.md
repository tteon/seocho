# Context Graph Blueprint For SEOCHO

This document maps context graph concepts to SEOCHO's actual operating model.
The goal is not "graph for graph's sake", but reliable agent execution at scale.

## 1. Problem Statement

SEOCHO already has:

- issue state (`bd`),
- execution surfaces (`crew`, `mayor`, `refinery`, `witness`),
- operational scripts (`ops-check`, `gt-land`).

What is missing is a single machine-readable trace that links:

- intent (why action happened),
- execution (what changed),
- validation (what passed/failed),
- authority (what policy allowed it),
- delivery (what was landed).

That trace is the context graph.

## 2. Operating Model: Two Clocks

### 2.1 Fast Clock (Per Task / Per Run)

Handles day-to-day work:

- claim issue,
- run commands,
- edit files,
- validate,
- land.

Primary concern: latency and reliability of execution.

### 2.2 Slow Clock (Weekly / Release Cadence)

Handles model/policy evolution:

- schema version changes,
- gate threshold changes,
- policy and permissions updates,
- retrospective metrics.

Primary concern: correctness and governance over time.

## 3. Coordinate System For Context

Every event or edge should be explainable through:

- `identity`: who acted (`human`, `agent`, `service`),
- `time`: when it started/ended and when it became invalid,
- `intent`: linked issue/task and expected outcome,
- `authority`: policy/check that allowed or blocked it,
- `provenance`: source command/file/commit/log.

If one axis is missing, the event is incomplete.

## 4. SEOCHO Surface Mapping

### 4.1 Control Plane (Slow Clock)

- `seocho/mayor/rig/`: canonical orchestration and policy anchor.
- `seocho/refinery/rig/`: readiness and merge policy enforcement.
- `seocho/docs/`: policy + protocol definitions.

### 4.2 Data Plane (Fast Clock)

- `seocho/crew/hardy/`: execution workspace.
- `scripts/ops-check.sh`: operational snapshot signal.
- `scripts/gt-land.sh`: landing event signal.
- `.beads/issues.jsonl`: issue state timeline.

## 5. Graph Schema v0

### 5.1 Node Types

- `Task`: `bd` issue.
- `Run`: one bounded execution attempt.
- `Artifact`: file/log/test report/patch.
- `Decision`: accepted/rejected choice.
- `GateResult`: pass/fail for a named gate.
- `Policy`: rule in force.
- `Actor`: human/agent/automation identity.

### 5.2 Edge Types

- `Actor -> Task`: `owns`, `assists`, `reviews`
- `Run -> Task`: `implements`, `validates`, `lands`
- `Run -> Artifact`: `produced`, `modified`, `consumed`
- `Run -> GateResult`: `evaluated_by`
- `Policy -> GateResult`: `defines`
- `Decision -> Artifact`: `approves`, `rejects`, `depends_on`
- `Artifact -> Artifact`: `derived_from`

### 5.3 Required Properties

- `id`: stable UUID/string
- `kind`: node/edge subtype
- `ts_start`, `ts_end`: ISO8601 UTC
- `scope`: `crew|mayor|refinery|witness|town`
- `task_id`: issue ID when applicable (example: `hq-xxa`)
- `run_id`: execution correlation ID
- `source_ref`: command, file path, commit SHA, or issue URL
- `confidence`: `0.0-1.0` for inferred links
- `schema_version`: current schema tag (example: `cg.v0`)

## 6. Canonical Event Contract (JSON)

Event stream should be append-only and normalized.

Example event:

```json
{
  "schema_version": "cg.v0",
  "event_id": "evt_20260214_223602_001",
  "run_id": "run_hq-xxa_20260214T2235Z",
  "task_id": "hq-xxa",
  "event_type": "gate_result",
  "actor": {
    "type": "agent",
    "id": "codex"
  },
  "scope": "town",
  "timestamp": "2026-02-14T22:36:02Z",
  "payload": {
    "gate": "ops_check",
    "status": "failed",
    "command": "scripts/ops-check.sh --rig seocho",
    "exit_code": 1
  },
  "source_ref": "logs/ops/raw/20260214T143606Z.log"
}
```

Required `event_type` set (v0):

- `task_claimed`
- `task_closed`
- `run_started`
- `run_finished`
- `artifact_changed`
- `gate_result`
- `landing_result`
- `decision_recorded`

## 7. Execution Loops

### 7.1 Fast Loop (Task-Level)

1. Claim task (`bd update ... in_progress`)
2. Emit `run_started`
3. Execute and emit `artifact_changed`
4. Run gates and emit `gate_result`
5. Land and emit `landing_result`
6. Emit `run_finished`

### 7.2 Slow Loop (Weekly Governance)

1. Analyze reopen/hotfix clusters by task and subsystem
2. Tune gate and policy thresholds
3. Migrate schema if event fields are insufficient
4. Publish policy updates in docs and enforcement scripts

## 8. Script Integration Plan

### 8.1 `scripts/ops-check.sh`

Add:

- machine-readable summary output (JSON line),
- optional `--task-id` and `--run-id`,
- stable location for exported records.

### 8.2 `scripts/gt-land.sh`

Add:

- structured landing result (`pull`, `rebase`, `sync`, `push`),
- explicit failure reason classification,
- final event containing branch sync status.

### 8.3 New `scripts/context-log.sh`

Responsibilities:

- validate required event fields,
- append JSONL record,
- reject secrets/token patterns,
- fail closed on malformed payload.

## 9. Query Patterns (What We Should Ask)

Operational queries:

- "Which tasks closed without any successful gate?"
- "Which runs touched policy-sensitive paths?"
- "Which reopened issues had failed ops gate before close?"
- "Which actor identities produce highest rework rate?"

Release queries:

- "Coverage of landing_result over all closed tasks"
- "Mean lead time by subsystem"
- "Top failure reasons for push/rebase"

## 10. Quality Gates For Adoption

Phase 1:

- 100% of closed tasks have `task_id`-linked validation artifact
- 100% of merged tasks have landing result record
- 0 leaked credentials in context logs

Phase 2:

- provenance coverage for decisions >= 90%
- reproducible run rate >= 85%
- reopen rate trending down over rolling 4 weeks

## 11. Security And Data Hygiene

- Never store raw tokens/secrets in event payload.
- Mask machine-local absolute paths when not needed.
- Keep context logs append-only and auditable.
- Separate runtime cache from durable context records.

## 12. Rollout Plan (4 Weeks)

1. Week 1
- freeze schema v0
- add JSON emission to `ops-check` and `gt-land`
- start collecting baseline events

2. Week 2
- materialize graph from JSONL
- add minimal dashboard or query script
- validate event completeness

3. Week 3
- wire policy checks into merge/readiness path
- fail build for missing mandatory event links

4. Week 4
- retrospective on data quality
- adjust schema to `cg.v1`
- update playbook and scripts

## 13. Immediate Backlog

- define canonical event schema issue
- implement script emitters
- add context trail inspection command
- add context graph debugging section in playbook
