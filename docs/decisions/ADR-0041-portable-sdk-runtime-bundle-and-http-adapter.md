# ADR-0041: Portable SDK Runtime Bundle And HTTP Adapter

Date: 2026-04-12
Status: Accepted

## Context

SEOCHO now has two real usage surfaces:

- SDK local engine mode for ontology-first authoring and experimentation
- HTTP client mode for remote consumption

That split is useful, but it also leaves a gap. A developer can build a strong
local SDK configuration with:

- ontology
- prompt template
- `AgentConfig`
- graph backend choice
- LLM backend choice

but another developer cannot directly consume that authored configuration
through the normal HTTP client mode without rebuilding the same setup by hand.

The project therefore needs a portable bridge from:

`SDK-authored local engine -> small deployable HTTP runtime`

without pretending that arbitrary Python hooks are portable.

## Decision

SEOCHO will add a portable bundle contract for SDK-authored local runtimes.

The portable contract includes:

- ontology payload
- optional ontology registry overrides
- portable subset of `AgentConfig`
- portable graph-store config
- portable LLM config
- optional inline extraction prompt template
- graph bindings for HTTP runtime metadata

The portable contract explicitly excludes:

- custom Python indexing/query strategies
- arbitrary in-process hooks
- secrets such as API keys and database passwords

Portable runtimes are served through a small FastAPI adapter that exposes a
compatibility subset of the HTTP surface:

- `POST /api/memories`
- `POST /api/memories/search`
- `POST /api/chat`
- `POST /run_agent_semantic`
- `GET /graphs`
- `GET /health/runtime`

The portable adapter is not the full main runtime. It is an intentionally
narrow bridge that lets HTTP client mode consume SDK-authored applications.

## Consequences

Positive:

- local SDK authoring becomes shareable over HTTP without rebuilding the app by
  hand
- HTTP client mode becomes the standard consumption path for SDK-authored apps
- portability limits become explicit instead of implicit

Tradeoffs:

- the portable runtime currently supports a subset of the full server surface
- only declarative configuration is portable; custom Python behavior is not
- backend export is intentionally narrow in the first version

## Implementation Notes

- bundle helpers: `seocho/runtime_bundle.py`
- HTTP adapter: `seocho/http_runtime.py`
- SDK methods:
  - `Seocho.export_runtime_bundle(...)`
  - `Seocho.from_runtime_bundle(...)`
- CLI:
  - `seocho bundle export`
  - `seocho bundle show`
  - `seocho serve-http --bundle ...`
