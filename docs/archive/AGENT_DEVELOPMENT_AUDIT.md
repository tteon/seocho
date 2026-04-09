# Agent Development Audit

Archived: this is a point-in-time repository audit, not an active operating document.

Date: 2026-03-12

This document summarizes what this repository is trying to build, which directories matter for agent work, how agent-friendly the repo currently is, and which documents are still missing for reliable agent-driven development.

## 1. Repository Purpose

SEOCHO is building a self-hosted, agent-driven knowledge graph platform.

Current product intent:

- ingest raw internal text, CSV, JSON, and PDF material
- extract ontology candidates, entities, relationships, and SHACL-like rules
- load the resulting graph into DozerDB
- answer questions over that graph through either:
  - debate mode across graph-specific agents
  - semantic mode with entity resolution plus router/LPG/RDF/answer agents
- trace runtime behavior with Opik

The strongest product signals are in:

- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/GRAPH_MODEL_STRATEGY.md`
- `extraction/agent_server.py`

In practice, this repo is closer to "GraphRAG platform plus governance and operations tooling" than to "generic agent SDK example code".

## 2. Core Directory Responsibility Map

The table below focuses on directories that matter for implementation decisions, not every folder in the tree.

| Path | Primary Responsibility | Agent Relevance | Notes |
|---|---|---|---|
| `extraction/` | Main runtime and data-plane code | Highest | Core backend. Contains API server, debate/semantic orchestration, ingestion, rules, graph loading, config, tests. |
| `extraction/conf/` | Prompt, schema, and ingestion config | High | Safe surface for controlled config changes. Needs clear prompt/config governance. |
| `extraction/ontology/` | Ontology support code | Medium | Offline ontology path is part of product boundary, but not the hot runtime path. |
| `extraction/tests/` | Main test suite | Highest | Current source of truth for behavior verification. |
| `evaluation/` | Thin frontend/backend proxy and static UI | High | User activation path depends on this, but most business logic lives in `extraction/`. |
| `semantic/` | Separate semantic service surface | Medium | Exists, but current docs do not clearly define whether it is active production path, secondary service, or legacy/prototype. |
| `scripts/` | Operational automation, demos, PM tooling, landing flow | High | Very important for agent workflows, validation, and release discipline. |
| `docs/` | Source-of-truth design, workflow, ADRs, product rationale | Highest | Strong documentation surface, but still missing some agent-development artifacts. |
| `tests/` | Placeholder top-level test namespaces | Low | Present, but current pytest config points mainly to `extraction/tests`. |
| `examples/`, `demos/`, `notebooks/` | Learning material and exploratory assets | Medium | Useful context for intended usage, but not always safe as implementation references. |
| `data/`, `logs/`, `neo4j/`, `opik/` | Runtime state, service data, local artifacts | Medium | Important for operating the stack, usually not a good edit target for feature work. |
| `.beads/` | Work tracking state | High | Part of the repository workflow and landing discipline. |
| `.github/` | CI and automation | High | Important for understanding quality gates and release expectations. |
| `tteon.github.io/` | Embedded docs website workspace | Medium | Increases confusion for agents because it looks like another repo/workspace inside this one. |

### Practical interpretation

- If an agent changes behavior visible to users, the most likely edit targets are `extraction/`, `evaluation/`, `scripts/`, and `docs/`.
- If an agent is trying to understand expected behavior, `docs/` plus `extraction/tests/` are more reliable than scattered example assets.
- If an agent is trying to land code safely, `AGENTS.md`, `CLAUDE.md`, `docs/WORKFLOW.md`, `docs/BEADS_OPERATING_MODEL.md`, and `scripts/land.sh` matter as much as the application code.

## 3. Agent-Friendliness Assessment

Overall assessment: strong intent, medium-to-high execution quality.

Suggested score: 7/10.

### What is already good

- The repo explicitly treats coding agents as first-class contributors through `AGENTS.md` and `CLAUDE.md`.
- Workflow, landing, sprint labels, and doc baselines are spelled out instead of being tribal knowledge.
- Runtime/API guardrails are repeated consistently: `workspace_id`, policy checks, offline-only Owlready2 boundary.
- There is an adapter layer for OpenAI Agents SDK signature drift in `extraction/agents_runtime.py`.
- There is meaningful automated coverage in `extraction/tests/`.
- There are scripts for linting agent docs, e2e smoke checks, landing, context events, and PM hygiene.

### What currently makes agent work harder

- `extraction/agent_server.py` is very large and mixes API models, dependency initialization, orchestration, policy use, and route handlers in one module.
- The runtime still relies on several module-level singletons initialized at import time, which raises edit risk and test coupling.
- `extraction/` mixes source code with outputs, notebooks, binaries, and domain-specific sample assets, which weakens directory signal.
- The role of `semantic/` versus `extraction/semantic_query_flow.py` is not documented clearly enough for a new agent.
- The repo has strong workflow docs, but weaker product and interface docs for implementation decisions.
- There is no dedicated coding style guide beyond short bullets and tool config.
- The embedded `tteon.github.io/` workspace increases the chance of accidental edits unless the task is explicitly docs-site related.

## 4. Recommended Improvement Priorities

### P0

1. Split `extraction/agent_server.py` into:
   - app factory / dependency wiring
   - route modules by surface (`platform`, `rules`, `semantic`, `health`)
   - request/response models
2. Move runtime state, outputs, and bundled binary/sample assets out of the main `extraction/` code path or document them as read-only.
3. Publish one clear "safe edit surfaces" guide for agents:
   - where behavior lives
   - where not to edit
   - which tests gate which changes

### P1

4. Clarify whether `semantic/` is production, experimental, or legacy.
5. Add a dedicated agent development guide with:
   - entrypoints
   - dependency graph
   - request path diagrams
   - edit and verification recipes
6. Consolidate API contract docs with request/response examples, error model, and stability expectations.

### P2

7. Add a real coding style guide beyond formatter settings.
8. Add a configuration matrix and deployment runbook.
9. Add an ownership map for runtime, data plane, docs, and operations surfaces.

## 5. Missing Or Weak Documents For Agent Development

The repo is not missing "all docs". It is missing a specific set of documents that reduce ambiguity during implementation.

| Needed Artifact | Current State | Why It Matters For Agents | Recommendation |
|---|---|---|---|
| PRD for current MVP | Missing | Architecture explains how, but not the prioritized user problem, success criteria, non-goals, and release scope. | Add `docs/PRD_MVP.md`. |
| User personas / core use-case catalog | Weak | There is a critical path, but not a canonical list of supported user journeys and expected outputs. | Add `docs/USE_CASES.md`. |
| Dedicated agent development guide | Missing | `ROADMAP.md` references `docs/AGENT_DEVELOPMENT.md`, but the file does not exist. | Add `docs/AGENT_DEVELOPMENT.md`. |
| Coding style guide | Partial | `CONTRIBUTING.md`, `AGENTS.md`, and `pyproject.toml` give minimum rules, but not enough for consistent edits. | Add `docs/CODING_STYLE.md`. |
| API contract reference | Partial | README lists endpoints, but there is no single contract doc covering schemas, examples, errors, and compatibility promises. | Add `docs/API_CONTRACTS.md`. |
| Configuration and environment matrix | Weak | Agents can find env vars in code and `.env.example`, but there is no authoritative matrix of required/optional variables by environment. | Add `docs/CONFIG_MATRIX.md`. |
| Deployment and operations runbook | Partial | There are setup and landing docs, but no single runbook for startup order, health checks, rollback, and common failures. | Add `docs/RUNBOOK.md`. |
| Test strategy / quality-gate matrix | Weak | Tests exist, but there is no concise guide mapping change types to required suites and mock policy. | Add `docs/TEST_STRATEGY.md`. |
| Prompt/config governance doc | Missing | Prompt YAML files exist, but there is no clear policy for changing prompts, validating them, or versioning prompt behavior. | Add `docs/PROMPT_GOVERNANCE.md`. |
| Ownership map / maintainer boundaries | Missing | Agents and contributors cannot easily tell who owns runtime, docs, data plane, evaluation UI, or ops automation. | Add `docs/OWNERSHIP.md`. |

### Highest-value missing documents

If only three new documents are created first, they should be:

1. `docs/PRD_MVP.md`
2. `docs/AGENT_DEVELOPMENT.md`
3. `docs/CODING_STYLE.md`

Those three would close most of the current ambiguity for both humans and coding agents.

## 6. Questions To Ask Maintainers

Keep this list under 10 items and answer them before asking agents to make broad architectural changes.

1. When `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`, and `docs/WORKFLOW.md` disagree, which file wins?
2. Is `semantic/` part of the active product path, or should agents treat it as secondary, experimental, or legacy?
3. Which directories inside `extraction/` are considered source code versus bundled assets or read-only sample material?
4. Do you want a dedicated `docs/AGENT_DEVELOPMENT.md` to become the source of truth for coding agents, or should `AGENTS.md` remain intentionally minimal?
5. What level of coding style enforcement do you want beyond `black`, `isort`, `flake8`, and basic type hints?
6. Which API surfaces are considered stable today, and which are still allowed to change without migration guarantees?
7. What are the release-blocking product acceptance criteria beyond `make e2e-smoke`?
8. What p95 latency, cost, or token-budget targets should semantic mode and debate mode stay within?
9. Which sample datasets, prompts, and fixtures are canonical for local validation and CI?
10. Should agents ever edit `tteon.github.io/` from this workspace, or is it always out of scope unless explicitly requested?

## 7. Short Recommendation

This repository is already better prepared for coding agents than most application repos because the maintainers documented workflow and guardrails early.

The next step is not "more architecture". The next step is reducing ambiguity:

- define the current product contract
- define the safe coding contract
- define the stable interface contract

Once those three are documented, the repo becomes meaningfully easier for both humans and agents to extend without accidental regressions.
