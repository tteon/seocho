# ADR-0045: Vendor-Neutral Tracing and Explicit Opik Opt-In

## Status

Accepted

## Date

2026-04-12

## Context

SEOCHO documentation and some runtime paths had drifted toward treating Opik
as if it were the tracing contract itself. In practice, there are three
separate concerns:

1. the runtime trace contract exposed by the product
2. the portable artifact that users can retain locally
3. the optional team-facing backend used for visualization and evaluation

This caused two concrete problems:

- SDK OpenAI clients were auto-wrapped with Opik when the package was installed,
  even if the user had not explicitly enabled Opik tracing.
- Documentation overstated Opik as mandatory, which does not match actual user
  needs for local, privacy-sensitive, or self-hosted deployments.

## Decision

SEOCHO adopts a vendor-neutral tracing contract with these rules:

- supported tracing backend values are `none | console | jsonl | opik`
- JSONL is the canonical neutral trace artifact
- Opik is a preferred team observability backend, not the runtime contract
- Opik instrumentation must be explicit
- self-hosted vs hosted Opik must be represented in configuration, not hidden in
  implementation defaults

### SDK rules

- `seocho.tracing.enable_tracing()` accepts `none`, `console`, `jsonl`, or `opik`
- `seocho.tracing.configure_tracing_from_env()` reads:
  - `SEOCHO_TRACE_BACKEND`
  - `SEOCHO_TRACE_JSONL_PATH`
  - `SEOCHO_TRACE_OPIK_MODE`
  - `OPIK_URL` / `OPIK_URL_OVERRIDE`
  - `OPIK_WORKSPACE`
  - `OPIK_PROJECT_NAME`
  - `OPIK_API_KEY`
- LLM client auto-wrapping with Opik is allowed only when the active tracing
  backend explicitly includes `opik`

### Extraction/runtime rules

- extraction runtime remains operational when tracing is disabled
- `SEOCHO_TRACE_BACKEND=opik` is the only mode that activates Opik-specific
  exporter integration inside `extraction/tracing.py`
- non-Opik values remain valid runtime contract values, even when the current
  extraction implementation does not attach equivalent exporter behavior yet

## Consequences

### Positive

- users can run the SDK with no remote observability dependency
- privacy-sensitive and self-hosted deployments have a clearer contract
- Opik becomes an optional exporter/backend rather than an implicit global side
  effect
- JSONL artifacts become the stable handoff/replay format across environments

### Tradeoffs

- extraction/runtime observability is still richer with Opik than with other
  backends
- some historical docs and older assumptions still mention Opik as a baseline;
  they must be updated incrementally
- explicit tracing activation adds a small amount of configuration work for
  teams that want automatic Opik visibility

## Follow-up

- keep improving parity between SDK JSONL traces and richer Opik views
- avoid reintroducing implicit vendor-specific tracing behavior in LLM/provider
  modules
- document retention and privacy guidance wherever hosted observability is
  recommended
