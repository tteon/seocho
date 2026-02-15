# Workflow

This document is the canonical workflow reference for SEOCHO operations.

## Stack Baseline

- Agent runtime: OpenAI Agents SDK
- Tracing/evaluation: Opik
- Graph backend: DozerDB
- MVP tenancy: single-tenant with `workspace_id` propagated end-to-end

## Planes

## Control Plane

Responsibilities:

- agent definitions and routing policy
- runtime authorization policy (app-level RBAC/ABAC)
- deployment, versioning, and quality gates
- decision records (ADRs) and change governance

Primary surfaces:

- `extraction/agent_server.py`
- `extraction/policy.py`
- `docs/decisions/`
- `docs/ADD_PLAYBOOK.md`

## Data Plane

Responsibilities:

- data ingestion from CSV/JSON/API
- extraction, linking, deduplication
- rule inference and validation annotations
- graph load/query execution against DozerDB

Primary surfaces:

- `extraction/pipeline.py`
- `extraction/rule_constraints.py`
- `extraction/data_source.py`
- `extraction/graph_loader.py`

## End-to-End Workflow

1. Intake
- define issue scope and acceptance criteria
- assign `workspace_id`
- capture work item using standardized scripts:
  - `scripts/pm/new-issue.sh`
  - `scripts/pm/new-task.sh`

2. Ingestion and graph build
- run extraction pipeline
- apply SHACL-like rule inference/validation
- save reusable rule profiles (`/rules/profiles`)
- load graph into DozerDB

3. Agent execution
- run `/run_agent` or `/run_debate`
- enforce runtime policy checks
- capture traces in Opik

4. Validation and landing
- run code and ops gates
- run sprint label lint (`scripts/pm/lint-items.sh --sprint <id>`)
- close issue, rebase, sync, push
- verify branch is up to date with origin

5. Governance loop
- log architecture decisions as ADRs
- track context graph events and quality metrics
- schedule follow-up issues for unresolved risks
