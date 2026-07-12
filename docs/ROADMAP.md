# SEOCHO Roadmap

Status: active planning baseline
Scope: 2026-08 through 2027-07

This roadmap maps SEOCHO's open-source development plan to concrete public
work. It is not a complete issue tracker. GitHub issues and milestones remain
the canonical execution surface.

## Direction

SEOCHO is ontology-aligned middleware between agents and graph databases. The
project should help users define an ontology once, ingest documents into graph
memory, ask graph-grounded questions, and inspect the evidence behind answers.

The next year of work should make that loop easier to use, easier to verify,
and easier to run in agent-coding and runtime environments.

## Product Principles

- Stabilize SEOCHO's core evidence and ontology contracts before adding many
  database integrations.
- Keep the local SDK path simple enough for a first successful run.
- Keep the runtime path deployable, observable, and explicit about
  `workspace_id` and policy checks.
- Treat performance and scalability claims as live-evidence claims. Mock or
  toy data can validate contracts, not production readiness.
- Make public docs and examples teach the smallest useful workflow first.

## 2026-2027 Milestones

| Period | Focus | Public outputs |
| --- | --- | --- |
| 2026-08 to 2026-09 | Repository and docs cleanup, roadmap alignment, first evidence-bundle and missing-slot slices, beginner guide refresh | roadmap, simplified docs map, focused issues, beginner guide draft |
| 2026-10 to 2026-12 | GraphStore adapter contract, first graph database comparison, GraphTalks and winter workshop material | adapter contract tests, comparison report, workshop slides, intermediate guide |
| 2027-01 to 2027-03 | MCP alpha for Codex and Claude Code, RDF/Oxigraph proof of concept, knowledge-graph management platform review | MCP interface draft, agent-coding examples, RDF POC notes, advanced guide draft |
| 2027-04 to 2027-07 | Agentic GraphRAG comparison, MARA/API evaluation report, selected large-scale benchmark run, annual report | product review report, benchmark artifacts, summer workshop material, annual result report |

## Development Tracks

### P0. Core Graph-RAG Contract

Goal: make graph-grounded answers inspectable.

Work:

- implement or expose `intent_id`, required slots, selected triples,
  `missing_slots`, provenance, and confidence
- keep text snippets as secondary support when a graph answer is expected
- add tests that show how unsupported or partially supported answers abstain
- keep the public behavior aligned with
  [Graph-RAG Agent Handoff Spec](GRAPH_RAG_AGENT_HANDOFF_SPEC.md)

### P1. MCP And Agent-Coding Integration

Goal: make SEOCHO usable from Codex, Claude Code, and similar coding-agent
environments.

Work:

- define the first SEOCHO MCP tool surface around run, index, query, and report
- add a small harness that can run the same task from CLI and MCP
- document Codex and Claude Code usage without making either a hard dependency
- record failure cases and tool differences as public issues or docs

### P2. Graph Backend Adapter Contract

Goal: make database integrations comparable before adding many adapters.

Work:

- formalize GraphStore contract tests for write, query, schema, source delete,
  source count, workspace filtering, and close behavior
- keep DozerDB/Neo4j and LadybugDB as the first compatibility baselines
- evaluate Oxigraph as an RDF-store proof of concept before promoting it as a
  full backend
- treat GraphScope, Neptune, Memgraph, and other systems as comparison targets
  until their adapter behavior is proven

### P3. Runtime And Kubernetes Path

Goal: provide a reproducible deployment shape for teams that need a shared API.

Work:

- keep runtime orchestration in `runtime/` and canonical SDK logic in
  `src/seocho/`
- preserve `workspace_id`, policy checks, and tracing metadata in runtime paths
- ship Docker Compose as the local baseline
- add Kubernetes or Helm examples only when the local runtime contract is stable

### P4. Benchmark And Product Review Track

Goal: compare products from a user's operational point of view.

Work:

- report installation, ingestion, query behavior, cost, backup/restore,
  observability, and licensing constraints
- keep benchmark tracks separate: private finance corpus, GraphRAG-Bench, and
  product-operation reviews are different evidence types
- run 1TB-scale experiments only for selected targets with explicit hardware,
  dataset, concurrency, warmup, and skipped-component notes
- publish readable reports for the GraphUserGroup audience

### P5. Education And Community Outputs

Goal: make SEOCHO teachable to beginners and useful to practitioners.

Work:

- beginner: install, local SDK, first ontology, first question
- intermediate: GraphRAG, ontology enforcement, graph backend, run specs
- advanced: MCP, runtime deployment, observability, product comparison
- publish docs and videos as public outputs, with GitHub links as the source of
  truth for code and reproducibility

## Product Comparison Scope

Do not compare every graph product at once. Each comparison should choose two
to four systems and state why they were selected.

Initial candidate groups:

| Group | Candidates |
| --- | --- |
| Graph databases | Neo4j/DozerDB, Amazon Neptune, Memgraph, GraphScope |
| RDF and ontology storage | Oxigraph, RDF-oriented workflows, ontology artifact stores |
| Additional candidates | JanusGraph, TigerGraph, ArangoDB, FalkorDB, TerisDB, Spanner Graph, Cosmos DB |
| Agentic GraphRAG systems | SEOCHO, graph-enabled RAG frameworks, agent memory systems |

## Near-Term GitHub Work

Create or update issues for:

- docs simplification and archive cleanup
- evidence-bundle and missing-slot implementation
- GraphStore contract tests
- MCP alpha interface
- beginner/intermediate/advanced guide structure
- first product comparison report
- large-scale benchmark plan

Each issue should include scope, acceptance criteria, validation command, and
public output expectation.

## Non-Goals For The Next Slice

- Do not claim production scalability from mocks or tutorial data.
- Do not add a public plugin surface outside documented store/backend contracts
  without an ADR.
- Do not move heavy ontology reasoning into request-time paths.
- Do not auto-publish Discord, Ghost, or release announcements without
  maintainer review.
