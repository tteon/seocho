# Context Graph Blueprint

## Goal

Represent task intent, execution, validation, and landing as one traceable graph
that agents can consume and governance systems can audit.

## Two Clocks

- fast clock: per-task execution events
- slow clock: schema/policy/quality-gate evolution

## Minimum Schema (v0)

Node types:

- `Task`
- `Run`
- `Artifact`
- `Decision`
- `GateResult`
- `Actor`

Edge types:

- `Actor -> Task` (`owns`, `reviews`)
- `Run -> Task` (`implements`, `validates`, `lands`)
- `Run -> Artifact` (`produced`, `modified`)
- `Run -> GateResult` (`evaluated_by`)
- `Decision -> Artifact` (`approves`, `rejects`)

Required properties:

- `task_id`
- `run_id`
- `timestamp`
- `scope`
- `source_ref`
- `schema_version`

## Event Contract (JSONL)

```json
{
  "schema_version": "cg.v0",
  "event_id": "evt_001",
  "task_id": "hq-xxa",
  "run_id": "run_hq-xxa_20260214T2235Z",
  "event_type": "gate_result",
  "timestamp": "2026-02-14T22:36:02Z",
  "scope": "town",
  "payload": {
    "gate": "ops_check",
    "status": "pass"
  },
  "source_ref": "logs/ops/raw/20260214T143606Z.log"
}
```

Required event types:

- `task_claimed`
- `run_started`
- `artifact_changed`
- `gate_result`
- `landing_result`
- `run_finished`
- `task_closed`

## Rollout

1. emit JSON events from `ops-check` and `gt-land`
2. materialize graph from JSONL
3. add merge gates for missing mandatory links
4. refine schema and metrics quarterly
