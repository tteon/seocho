# SEOCHO Documentation

SEOCHO docs are organized around jobs, not around the repository tree. Start
with the path that matches what you are trying to do today, then move deeper
only when you need the extra detail.

## Choose Your Path

| I want to... | Start here | What you should get |
|---|---|---|
| understand the product idea | [Why SEOCHO](WHY_SEOCHO.md) | why ontology-first graph memory is different from generic AI memory |
| get a first local success | [Quickstart](../QUICKSTART.md) | install, define a tiny ontology, add text, ask a question |
| use SEOCHO from Python | [Python SDK](PYTHON_INTERFACE_QUICKSTART.md) | local SDK, HTTP client, semantic query, and artifact examples |
| bring my own records or files | [Bring Your Data](APPLY_YOUR_DATA.md) | ingestion paths, graph targets, query order, and inspection points |
| run the local platform | [Runtime Deployment](RUNTIME_DEPLOYMENT.md) | UI, API, DozerDB, environment, and troubleshooting |
| contribute to the project | [Open Source Playbook](OPEN_SOURCE_PLAYBOOK.md) | issue/PR workflow, labels, examples, and review expectations |

If you are new, use this order:

1. [Why SEOCHO](WHY_SEOCHO.md)
2. [Quickstart](../QUICKSTART.md)
3. [Python SDK](PYTHON_INTERFACE_QUICKSTART.md)
4. [Bring Your Data](APPLY_YOUR_DATA.md)
5. [Files and Artifacts](FILES_AND_ARTIFACTS.md)
6. [Architecture](ARCHITECTURE.md)

## The Mental Model

SEOCHO keeps one ontology contract aligned across four surfaces:

| Surface | What the ontology controls |
|---|---|
| Ingestion | which entities, relationships, and properties should be extracted |
| Graph writes | constraints, provenance, and schema-shaped payloads |
| Querying | schema-aware retrieval, Cypher generation, and bounded repair |
| Runtime | HTTP-facing semantic artifacts, traces, policy, and `workspace_id` scope |

The fastest first run is `Seocho.local(...)`. The runtime path is for teams that
want a shared API, UI, and DozerDB-backed deployment.

## Common Questions

| Question | Short answer | Read next |
|---|---|---|
| Do I need Neo4j or DozerDB for hello world? | No. `Seocho.local(...)` uses the embedded local path by default. | [Quickstart](../QUICKSTART.md) |
| When should I use the runtime? | When another process or agent needs to consume the same graph contract over HTTP. | [Runtime Deployment](RUNTIME_DEPLOYMENT.md) |
| Where do generated artifacts go? | Local graph data, semantic artifacts, rule profiles, and traces are filesystem-visible. | [Files and Artifacts](FILES_AND_ARTIFACTS.md) |
| Is debate the default mode? | No. Start with semantic graph QA and use debate only for explicit comparison work. | [Python SDK](PYTHON_INTERFACE_QUICKSTART.md) |
| Where are release and Discord rules? | GitHub releases and docs are canonical; Discord is for curated community updates. | [Release And Community Operations](RELEASE_AND_COMMUNITY_OPERATIONS.md) |

## Builder References

- [Run Specs](RUN_SPECS.md): declare ontology, documents, questions, and sweeps in YAML.
- [Tutorial First Run](TUTORIAL_FIRST_RUN.md): end-to-end local runtime tutorial.
- [Agent Design Specs](AGENT_DESIGN_SPECS.md): YAML-backed agent patterns with ontology bindings.
- [Indexing Design Specs](INDEXING_DESIGN_SPECS.md): graph-model-aware indexing variants.
- [Benchmarks](BENCHMARKS.md): FinDER and GraphRAG benchmark tracks.

## Architecture And Operations

- [Architecture](ARCHITECTURE.md): system architecture and module map.
- [Workflow](WORKFLOW.md): canonical development and operations workflow.
- [Graph-RAG Agent Handoff Spec](GRAPH_RAG_AGENT_HANDOFF_SPEC.md): intent-first graph answer contract.
- [Repository Layout](REPOSITORY_LAYOUT.md): root directory intent and canonical edit surfaces.
- [GitHub Automation](GITHUB_AUTOMATION.md): CI, docs deploy, labels, Discord, and maintainer automation.
- [Release And Community Operations](RELEASE_AND_COMMUNITY_OPERATIONS.md): release gates and `#seocho` community rules.

## Contributor References

- [Open Source Playbook](OPEN_SOURCE_PLAYBOOK.md): contributor onboarding.
- [Issue Task System](ISSUE_TASK_SYSTEM.md): public issue and task metadata.
- [Decision Log](decisions/DECISION_LOG.md): architecture decision history.
- [Contributing](../CONTRIBUTING.md): PR and contribution flow.

## Internal And Maintainer Docs

These are useful after you know the product path. They are not part of the
first-read sequence.

- [Architecture Health](ARCHITECTURE_HEALTH.md)
- [Internal Class Design](INTERNAL_CLASS_DESIGN.md)
- [Runtime Package Migration](RUNTIME_PACKAGE_MIGRATION.md)
- [Repository Hierarchy Review](REPOSITORY_HIERARCHY_REVIEW.md)
- [Philosophy Feasibility Review](PHILOSOPHY_FEASIBILITY_REVIEW.md)
- [Known Issue](KNOWN_ISSUE.md)

## Docs Site Integration

- GitHub `README.md` is the fastest product landing page.
- `docs/*` is the source of truth for long-form product, operator, and system
  contracts.
- `website/` is the tracked Astro/Starlight source app in this repository.
- Current live deployment for `https://seocho.blog` is still owned by
  `tteon/tteon.github.io` GitHub Pages until Pages is enabled on `tteon/seocho`.
- `website/scripts/generate-docs.mjs` materializes selected `/docs/*` and
  `/blog/*` pages from repo-root source docs for the in-repo site app.
- the `scripts/sync.mjs` helper in `tteon/tteon.github.io` mirrors selected
  source docs into the live GitHub Pages repository.
- Generated mirror files under `website/src/content/docs/docs/` are derived
  artifacts; edit the repo-root source docs instead.
- Validate the site with `cd website && npm ci && npm run check:docs && npm run build && bash scripts/check-built-links.sh`.
