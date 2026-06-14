# SEOCHO Documentation

SEOCHO docs are organized around one question: what do you need to do right
now?

[![Quickstart](https://img.shields.io/badge/Quickstart-First_Run-2563eb)](../QUICKSTART.md)
[![Python SDK](https://img.shields.io/badge/Python_SDK-Examples-0f766e)](PYTHON_INTERFACE_QUICKSTART.md)
[![Architecture Deep Dive](https://img.shields.io/badge/Architecture-Deep_Dive-7c3aed)](ARCHITECTURE.md)

## Start Here

| If you need to... | Start here |
|---|---|
| understand why SEOCHO exists | [WHY_SEOCHO.md](WHY_SEOCHO.md) |
| get a first local success path | [../QUICKSTART.md](../QUICKSTART.md) |
| understand local `ask()` vs runtime `semantic/react/debate` | [../README.md#choose-a-mode](../README.md#choose-a-mode) |
| bring up the full local runtime stack | [RUNTIME_DEPLOYMENT.md](RUNTIME_DEPLOYMENT.md) |
| follow a runnable notebook walkthrough | [../examples/quickstart.ipynb](../examples/quickstart.ipynb) |
| understand SEOCHO with architecture snippets | [BEGINNER_GUIDE.md](BEGINNER_GUIDE.md) |
| use the Python SDK directly | [PYTHON_INTERFACE_QUICKSTART.md](PYTHON_INTERFACE_QUICKSTART.md) |
| declare reusable agent patterns in YAML | [AGENT_DESIGN_SPECS.md](AGENT_DESIGN_SPECS.md) |
| declare graph-model-aware indexing in YAML | [INDEXING_DESIGN_SPECS.md](INDEXING_DESIGN_SPECS.md) |
| bring your own ontology and data | [APPLY_YOUR_DATA.md](APPLY_YOUR_DATA.md) |
| inspect files, artifacts, and traces | [FILES_AND_ARTIFACTS.md](FILES_AND_ARTIFACTS.md) |
| understand the system design | [ARCHITECTURE.md](ARCHITECTURE.md) |
| understand the top-level repository layout | [REPOSITORY_LAYOUT.md](REPOSITORY_LAYOUT.md) |
| understand GitHub automation | [GITHUB_AUTOMATION.md](GITHUB_AUTOMATION.md) |
| present SEOCHO to a technical audience | [presentations/SEOCHO_OVERVIEW_DEEP_DIVE.md](presentations/SEOCHO_OVERVIEW_DEEP_DIVE.md) |
| measure behavior with FinDER and benchmark tracks | [BENCHMARKS.md](BENCHMARKS.md) |

Recommended onboarding order:

1. [WHY_SEOCHO.md](WHY_SEOCHO.md)
2. [../QUICKSTART.md](../QUICKSTART.md)
3. [../examples/quickstart.ipynb](../examples/quickstart.ipynb)
4. [BEGINNER_GUIDE.md](BEGINNER_GUIDE.md)
5. [PYTHON_INTERFACE_QUICKSTART.md](PYTHON_INTERFACE_QUICKSTART.md)
6. [APPLY_YOUR_DATA.md](APPLY_YOUR_DATA.md)
7. [FILES_AND_ARTIFACTS.md](FILES_AND_ARTIFACTS.md)
8. [ARCHITECTURE.md](ARCHITECTURE.md)
9. [presentations/SEOCHO_OVERVIEW_DEEP_DIVE.md](presentations/SEOCHO_OVERVIEW_DEEP_DIVE.md)

## Product Entry Points

- [WHY_SEOCHO.md](WHY_SEOCHO.md): product framing and ontology-aligned value
  proposition
- [../QUICKSTART.md](../QUICKSTART.md): shortest local success path
- [RUNTIME_DEPLOYMENT.md](RUNTIME_DEPLOYMENT.md): full local runtime
  deployment guide for Docker stack, services, and environment setup
- [../examples/quickstart.ipynb](../examples/quickstart.ipynb): runnable
  notebook covering ontology, indexing design, agent design, indexing, query,
  `.env`-backed provider setup, safe Ladybug fallback, optional Neo4j/DozerDB,
  and provider comparison
- [BEGINNER_GUIDE.md](BEGINNER_GUIDE.md): first-run guide that connects SDK
  snippets to architecture seams
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
- [INTERNAL_CLASS_DESIGN.md](INTERNAL_CLASS_DESIGN.md): internal orchestration
  seam classes (`DomainEvent`, `IngestionFacade`, `QueryProxy`,
  `AgentFactory`, `AgentStateMachine`) used while the modular monolith is
  still being decomposed
- [presentations/SEOCHO_OVERVIEW_DEEP_DIVE.md](presentations/SEOCHO_OVERVIEW_DEEP_DIVE.md):
  20-30 minute beginner-friendly product and architecture deck
- [GRAPH_RAG_AGENT_HANDOFF_SPEC.md](GRAPH_RAG_AGENT_HANDOFF_SPEC.md):
  intent-first graph answer contract
- [ONTOLOGY_RUN_CONTEXT_STRATEGY.md](ONTOLOGY_RUN_CONTEXT_STRATEGY.md):
  ontology context contract across indexing, query, and agents
- [PROPERTY_GRAPH_LENS_STRATEGY.md](PROPERTY_GRAPH_LENS_STRATEGY.md):
  semantic overlay strategy for property graphs
- [INTERNAL_CLASS_DESIGN.md](INTERNAL_CLASS_DESIGN.md): internal orchestration
  seams for the modular monolith
- [MODULE_OWNERSHIP_MAP.md](MODULE_OWNERSHIP_MAP.md): canonical module
  ownership and compatibility boundaries
- [WORKFLOW.md](WORKFLOW.md): operational workflow
- [GITHUB_AUTOMATION.md](GITHUB_AUTOMATION.md): GitHub Actions, Codex
  automation, and `.github/` placement rules

## Contributor References

- [ISSUE_TASK_SYSTEM.md](ISSUE_TASK_SYSTEM.md): sprint and task governance
- [REPOSITORY_LAYOUT.md](REPOSITORY_LAYOUT.md): root directory intent,
  canonical edit surfaces, and legacy/local-only paths
- [OPEN_SOURCE_PLAYBOOK.md](OPEN_SOURCE_PLAYBOOK.md): contributor onboarding
- [decisions/DECISION_LOG.md](decisions/DECISION_LOG.md): architecture decision
  history
- [../CONTRIBUTING.md](../CONTRIBUTING.md): contribution flow and PR rules

## Internal & Maintainer Docs

These are working documents (planning, reviews, migrations, known issues),
collected under [`docs/internal/`](internal/). They are not part of the
getting-started path — skip them on a first read.

- [internal/AGENT_SERVER_REFACTOR_PLAN.md](internal/AGENT_SERVER_REFACTOR_PLAN.md)
- [internal/RUNTIME_PACKAGE_MIGRATION.md](internal/RUNTIME_PACKAGE_MIGRATION.md): staged `extraction/` → `runtime/` migration plan
- [internal/ARCHITECTURE_HEALTH.md](internal/ARCHITECTURE_HEALTH.md): per-domain quality scorecard
- [internal/REPOSITORY_HIERARCHY_REVIEW.md](internal/REPOSITORY_HIERARCHY_REVIEW.md): repo hierarchy cleanup priorities
- [internal/PHILOSOPHY_FEASIBILITY_REVIEW.md](internal/PHILOSOPHY_FEASIBILITY_REVIEW.md)
- [internal/PROMPT_ASSEMBLY_DISCUSSION_MEMO.md](internal/PROMPT_ASSEMBLY_DISCUSSION_MEMO.md)
- [internal/BASELINE_INSTRUCTIONS.md](internal/BASELINE_INSTRUCTIONS.md)
- [internal/KNOWN_ISSUE.md](internal/KNOWN_ISSUE.md)

## Docs Sync Integration

- GitHub `README.md` is the fastest product landing page.
- `docs/*` is the source of truth for long-form product, operator, and system
  contracts.
- `website/` is the tracked Astro/Starlight source for `https://seocho.blog`.
- `website/scripts/generate-docs.mjs` materializes selected `/docs/*` and
  `/blog/*` pages from the repo-root source docs at build time.
- Generated mirror files under `website/src/content/docs/docs/` are derived
  artifacts; edit the repo-root source docs instead.
- Validate the site with `cd website && npm ci && npm run check:docs && npm run build && bash scripts/check-built-links.sh`.
