# ADR-0033: Public Python SDK And Pip Distribution Contract

Date: 2026-04-09
Status: Accepted

## Context

SEOCHO already had a typed Python client and CLI, but the public package still
behaved more like a repository-local helper than a distributable SDK.

The main gaps were:

- the published package contract was still memory-first and did not expose the
  broader runtime surfaces developers actually need (`semantic`, `debate`,
  `platform chat`, runtime registries)
- the import surface favored explicit client construction only, which made
  quick scripts and notebook usage more verbose than necessary
- the default package dependencies included the full repository runtime stack,
  making `pip install` heavier and less reliable than the SDK itself requires

That combination made the codebase usable for maintainers but weaker than it
should be for external Python developers.

## Decision

SEOCHO will treat the `seocho` Python package as a public SDK first.

1. expose runtime-facing client methods beyond memory CRUD:
   - `router`
   - `semantic`
   - `debate`
   - `platform_chat`
   - `session_history`
   - `reset_session`
   - `raw_ingest`
   - `databases`
   - `agents`
   - `ensure_fulltext_indexes`
2. add module-level convenience functions so quick scripts can use:
   - `seocho.configure(...)`
   - `seocho.ask(...)`
   - `seocho.chat(...)`
   - `seocho.debate(...)`
3. keep explicit `Seocho` / `AsyncSeocho` clients as the recommended surface
   for applications and libraries
4. make the default package dependencies SDK-focused and lightweight
5. move repository-development dependencies into optional extras so public
   `pip install seocho` remains practical

## Consequences

Positive:

- public install and first use become much closer to normal Python SDK
  expectations
- developers can access the main runtime modes without dropping to raw HTTP
- quick scripts, notebooks, and examples become simpler to write and teach

Tradeoffs:

- the package now has two public usage styles (module-level convenience and
  explicit clients), which must remain aligned
- editable repository setup becomes an explicit extras-based workflow
- the public package remains an SDK for a running SEOCHO backend, not a
  standalone replacement for the repository runtime stack

## Implementation Notes

- client surface lives in `seocho/client.py`
- module-level convenience API lives in `seocho/api.py`
- typed response models live in `seocho/types.py`
- packaging metadata lives in `pyproject.toml`
- onboarding docs live in `README.md` and `docs/PYTHON_INTERFACE_QUICKSTART.md`
