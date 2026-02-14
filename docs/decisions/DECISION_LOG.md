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

## Template

Use this block for new entries:

```md
## YYYY-MM-DD

- [Status] ADR-XXXX short-title
  - key decision 1
  - key decision 2
  - risk/tradeoff note
```
