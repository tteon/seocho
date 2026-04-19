# SEOCHO Documentation

SEOCHO docs are organized around one question: what do you need to do right
now?

[![Quickstart](https://img.shields.io/badge/Quickstart-First_Run-2563eb)](QUICKSTART.md)
[![Python SDK](https://img.shields.io/badge/Python_SDK-Examples-0f766e)](PYTHON_INTERFACE_QUICKSTART.md)
[![Architecture Deep Dive](https://img.shields.io/badge/Architecture-Deep_Dive-7c3aed)](ARCHITECTURE.md)

## Start Here

| If you need to... | Start here |
|---|---|
| get a first local success path | [QUICKSTART.md](QUICKSTART.md) |
| use the Python SDK directly | [PYTHON_INTERFACE_QUICKSTART.md](PYTHON_INTERFACE_QUICKSTART.md) |
| declare reusable agent patterns in YAML | [AGENT_DESIGN_SPECS.md](AGENT_DESIGN_SPECS.md) |
| declare graph-model-aware indexing in YAML | [INDEXING_DESIGN_SPECS.md](INDEXING_DESIGN_SPECS.md) |
| bring your own ontology and data | [APPLY_YOUR_DATA.md](APPLY_YOUR_DATA.md) |
| inspect files, artifacts, and traces | [FILES_AND_ARTIFACTS.md](FILES_AND_ARTIFACTS.md) |
| understand the system design | [ARCHITECTURE.md](ARCHITECTURE.md) |
| measure behavior with FinDER and benchmark tracks | [BENCHMARKS.md](BENCHMARKS.md) |

Recommended onboarding order:

1. [WHY_SEOCHO.md](WHY_SEOCHO.md)
2. [QUICKSTART.md](QUICKSTART.md)
3. [PYTHON_INTERFACE_QUICKSTART.md](PYTHON_INTERFACE_QUICKSTART.md)
4. [APPLY_YOUR_DATA.md](APPLY_YOUR_DATA.md)
5. [FILES_AND_ARTIFACTS.md](FILES_AND_ARTIFACTS.md)
6. [ARCHITECTURE.md](ARCHITECTURE.md)

## Product Entry Points

- [WHY_SEOCHO.md](WHY_SEOCHO.md): product framing and ontology-aligned value
  proposition
- [QUICKSTART.md](QUICKSTART.md): shortest local success path
- [PYTHON_INTERFACE_QUICKSTART.md](PYTHON_INTERFACE_QUICKSTART.md): public
  Python SDK path and API examples
- [AGENT_DESIGN_SPECS.md](AGENT_DESIGN_SPECS.md): YAML-backed agent patterns
  with required ontology bindings
- [INDEXING_DESIGN_SPECS.md](INDEXING_DESIGN_SPECS.md): YAML-backed indexing
  variants for LPG, RDF, hybrid, and inquiry-cycle defaults
- [APPLY_YOUR_DATA.md](APPLY_YOUR_DATA.md): ingest your own records and query
  them safely
- [FILES_AND_ARTIFACTS.md](FILES_AND_ARTIFACTS.md): where ontology files,
  semantic artifacts, rule profiles, and traces live
- [BENCHMARKS.md](BENCHMARKS.md): FinDER and GraphRAG benchmark tracks
- [ARCHITECTURE.md](ARCHITECTURE.md): architecture deep dive and module map

## Architecture And Operations

- [ARCHITECTURE.md](ARCHITECTURE.md): system architecture and runtime/module map
- [RUNTIME_PACKAGE_MIGRATION.md](RUNTIME_PACKAGE_MIGRATION.md): staged
  `extraction/` to `runtime/` migration plan
- [GRAPH_RAG_AGENT_HANDOFF_SPEC.md](GRAPH_RAG_AGENT_HANDOFF_SPEC.md):
  intent-first graph answer contract
- [ONTOLOGY_RUN_CONTEXT_STRATEGY.md](ONTOLOGY_RUN_CONTEXT_STRATEGY.md):
  ontology context contract across indexing, query, and agents
- [PROPERTY_GRAPH_LENS_STRATEGY.md](PROPERTY_GRAPH_LENS_STRATEGY.md):
  semantic overlay strategy for property graphs
- [WORKFLOW.md](WORKFLOW.md): operational workflow

## Contributor References

- [ISSUE_TASK_SYSTEM.md](ISSUE_TASK_SYSTEM.md): sprint and task governance
- [BEADS_OPERATING_MODEL.md](BEADS_OPERATING_MODEL.md): `.beads` execution
  contract
- [OPEN_SOURCE_PLAYBOOK.md](OPEN_SOURCE_PLAYBOOK.md): contributor onboarding
- [decisions/DECISION_LOG.md](decisions/DECISION_LOG.md): architecture decision
  history
- [../CONTRIBUTING.md](../CONTRIBUTING.md): contribution flow and PR rules

## Docs Sync Integration

- GitHub `README.md` is the fastest product landing page.
- `docs/*` is the source of truth for long-form product, operator, and system
  contracts.
- `tteon.github.io/` mirrors selected pages for `https://seocho.blog`.
- If a source doc changes materially, update the mirrored website page and
  validate drift with the website repo checks.
