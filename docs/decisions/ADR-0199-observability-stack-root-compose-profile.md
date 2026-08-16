# ADR-0199: Observability stack as a first-class root-compose profile

- Status: accepted
- Date: 2026-08-16
- Related: ADR-0144 (local OTel observability + span/trace structure),
  ADR-0146 (production observability profiles + metric contract)

## Context

The local OTel stack (Collector + Tempo + Prometheus + Grafana + the SEOCHO
dashboards, ADR-0144) lived in `examples/observability/` as a standalone compose
file, brought up via `make observability-up` under its own project name
(`seocho-observability`) and joining the core stack's `seocho-net` as an
`external` network.

Two problems: (1) filing the stack under `examples/` framed observability as a
demo, when the AgenticOS thesis treats observability/provenance/auditability as a
first-class governance pillar; (2) a separate project + `external` network is
brittle — a pre-existing or foreign-labelled `seocho-net` makes bring-up fail,
and there was no single `docker compose` entry point that included it.

## Decision

Pull the observability stack into the **root `docker-compose.yml`** via Compose
`include:`, gated behind the existing **`observability` profile** (OFF by
default — a plain `docker compose up` starts nothing observability-related).

- The definition and the eight Grafana dashboards **stay** in
  `examples/observability/`; only the entry point moved. The root compose
  `include:`s the file.
- The included network is declared **identically** to the root network
  (`graphrag-net` → name `seocho-net`, `driver: bridge`, non-external), so
  `include` merges the two into ONE project-created network instead of unioning
  `external: true` into the core definition (which would have stopped the core
  stack from creating its own network). The four services attach to it.
- `make observability-up|down|logs` now drive the root compose with
  `--profile observability` and an explicit service list, so they bring up /
  tear down ONLY the four observability containers, leaving the core stack and
  the named data volumes untouched.

## Consequences

- One entry point: `docker compose --profile observability up -d …` (or
  `make observability-up`) from the repo root, alongside the core stack in the
  same project. Bringing the core stack up first is no longer required — the
  profile creates/attaches `seocho-net` itself.
- Observability reads as part of how you operate SEOCHO, not a side example,
  while the profile keeps it opt-in and the README's dev-grade caveats
  (anonymous Grafana, local-disk retention) intact. Opik stays the separate
  cloud/team backend (its own profile).
- Verified live via the new path: all four containers healthy, eight dashboards
  provisioned, Prometheus + Tempo datasources present, the Collector scrape
  target `up`. Default `docker compose config` still excludes the observability
  services and creates `seocho-net` exactly as before.
