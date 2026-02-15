# Decision Log

This file is the lightweight index of architecture/product decisions.
Each entry must link to a full ADR when impact is non-trivial.

## 2026-02-14

- Accepted `ADR-0001-aip-platform-baseline.md`
  - OpenAI Agents SDK as agent runtime
  - Opik for tracing/evaluation
  - DozerDB fixed as backend graph DB
  - Single-tenant MVP, multi-tenant-ready data model
  - `owlready2` allowed only for offline policy validation/compilation

- Accepted `ADR-0002-runtime-guardrails-phase1.md`
  - add `workspace_id` to API/context
  - add runtime policy engine hook for endpoint authorization
  - introduce DozerDB-first config aliases
  - keep ontology reasoning out of request hot path

- Accepted docs restructure (no ADR)
  - separate active docs and archive docs
  - add explicit workflow doc for control plane/data plane visibility
  - make README the primary entry with workflow and doc map links

## 2026-02-15

- Accepted `ADR-0003-rule-api-phase1.md`
  - add `/rules/infer` and `/rules/validate`
  - enforce runtime permission actions for rules APIs
  - keep workspace-aware request contract

- Accepted `ADR-0004-rule-profile-lifecycle-and-cypher-export.md`
  - add rule profile save/list/get APIs
  - add DozerDB Cypher export API for rule profiles
  - return unsupported mapping kinds explicitly in export response

- Accepted `ADR-0005-graph-model-selection-for-upload-flow.md`
  - adopt layered graph representation for upload flow
  - keep Owlready2 in offline ontology governance path
  - align retrieval strategy to local/global/query-structured patterns

- Accepted `ADR-0006-issue-task-governance-for-sprints.md`
  - enforce required collaboration labels on active work items
  - standardize issue/task capture scripts
  - add sprint board and lint tooling for roadmap execution

- Accepted `ADR-0007-agent-docs-baseline-refresh.md`
  - refresh `CLAUDE.md` as primary execution contract
  - refresh `AGENTS.md` as concise operational rules
  - align docs with current stack and workflow guardrails

## Template

Use this block for new entries:

```md
## YYYY-MM-DD

- [Status] ADR-XXXX short-title
  - key decision 1
  - key decision 2
  - risk/tradeoff note
```
