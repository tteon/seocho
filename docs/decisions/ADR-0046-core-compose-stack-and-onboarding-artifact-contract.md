# ADR-0046: Core Compose Stack and Onboarding Artifact Contract

## Status

Accepted

## Date

2026-04-13

## Context

SEOCHO's local onboarding path had two avoidable problems:

1. the default `docker-compose.yml` booted more than the current product
   activation path actually needed
2. the README and quickstart docs did not clearly explain:
   - when `pip install seocho` is enough
   - when DozerDB/Neo4j is required
   - where ontology and runtime artifacts are stored locally

In practice, the current user activation path is:

- DozerDB/Neo4j
- `extraction-service`
- `evaluation-interface`

The standalone `semantic-service` still exists in the repository, but it is no
longer part of the main default runtime path.

## Decision

SEOCHO adopts the following local-stack and onboarding contract:

1. default `docker compose up -d` / `make up` starts the core local stack only:
   - `neo4j`
   - `extraction-service`
   - `evaluation-interface`
2. `semantic-service` is kept as a legacy opt-in profile:
   - `docker compose --profile legacy-semantic up -d semantic-service`
3. onboarding docs must distinguish three usage modes explicitly:
   - HTTP client mode
   - local SDK engine mode
   - local platform runtime mode
4. onboarding docs must document the main local file/artifact locations:
   - ontology file
   - graph data directory
   - semantic artifacts
   - rule profile registry
   - semantic run metadata
   - trace artifacts

## Consequences

### Positive

- default local startup better matches the real product path
- fewer unnecessary services start by default
- README and quickstarts become more honest about prerequisites
- operators and developers can find ontology and governance artifacts directly

### Tradeoffs

- legacy `semantic-service` becomes less discoverable unless documented
- local-engine installation is still heavier than the thin HTTP client package
- full packaging ergonomics may still need a future `local` extra instead of
  relying on `.[dev]`

## Follow-up

- consider a slimmer documented `seocho[local]` extra for SDK authoring
- continue splitting runtime contracts from legacy services and historical
  onboarding assumptions
