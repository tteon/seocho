# Coding Style Guide

Date: 2026-03-12
Status: Draft

This guide defines the practical coding style for SEOCHO. It expands the short rules in `AGENTS.md`, `CLAUDE.md`, and `CONTRIBUTING.md`.

## 1. Goals

Code should be:

- easy to review
- safe to change
- explicit about runtime contracts
- testable in isolation
- consistent with the current stack baseline

## 2. Tooling Baseline

Current formatting and lint baseline:

- `black`
- `isort`
- `flake8`
- `pytest`

CI-critical expectations:

- every runtime/API change should pass focused `pytest` suites before landing
- architecture/doc workflow changes should pass `scripts/pm/lint-agent-docs.sh`
- changed Python modules should at least pass import/compile validation before broader tests
- if a full suite is skipped, the exact gap must be written in the handoff or final update

From `pyproject.toml`:

- line length: 88
- Python target: 3.11
- pytest path baseline: `extraction/tests`

## 3. General Style Rules

### Required

- use type hints on function signatures
- prefer small, single-purpose functions
- use logging instead of `print`
- use centralized config through `extraction/config.py`
- preserve `workspace_id` in runtime-facing flows
- keep side effects explicit

### Avoid

- broad hidden side effects
- ad-hoc environment parsing scattered across modules
- silent exception swallowing
- route handlers that embed large amounts of business logic
- introducing new `sys.path` hacks

## 4. Python Conventions

### Imports

- keep imports grouped: standard library, third-party, local
- follow `isort` ordering
- do not add new import-time behavior unless it is unavoidable

### Functions and classes

- prefer descriptive names over abbreviations
- add docstrings when behavior is not obvious
- keep classes focused on one responsibility
- use dataclasses or Pydantic models for structured state instead of loose dictionaries when reasonable

### State management

- prefer explicit dependency passing over module-level singletons
- if a singleton already exists, do not expand its scope casually
- new shared mutable state requires a clear reason and tests

## 5. FastAPI and API Style

### Routes

- keep routes thin
- push business logic into service or orchestration modules
- always use request and response models for non-trivial payloads
- use structured error responses

### Naming

- prefer resource-oriented public names
- keep internal expert surfaces clearly separate from public simple surfaces
- avoid leaking internal orchestration terms into public APIs unless intentional

### Contracts

- `workspace_id` must be validated and propagated
- policy checks must wrap new runtime actions
- response fields should be stable and documented when user-facing

## 6. Configuration Rules

- new configuration should flow through `extraction/config.py`
- document new environment variables in `.env.example` and docs when user-visible
- use safe defaults where possible
- do not hardcode secrets, credentials, or environment-specific paths

## 7. Logging and Errors

### Logging

- use module-level loggers
- log operationally useful context without leaking secrets
- include identifiers such as `workspace_id`, request IDs, or database names when helpful

### Errors

- raise explicit exceptions instead of returning ambiguous sentinel values
- map domain errors to structured API responses
- error messages should help debugging without exposing secrets

## 8. Tests

### Expectations

- every changed behavior should have a focused test change
- prefer small deterministic tests over broad brittle integration tests
- mock heavy external dependencies in unit-level API tests

### Good patterns

- use fake connectors for semantic flow tests
- use ASGI transport for FastAPI endpoint tests
- assert both success path and failure or edge behavior when relevant
- add contract tests when changing public request or response fields

## 9. Documentation Rules

- update user-facing docs when behavior changes
- update ADRs for architecture or workflow changes
- when adding a new public contract, document examples and limits

## 10. Current Target Direction

The current codebase has some large modules and global initialization. New work should move the codebase toward:

- smaller route modules
- clearer dependency boundaries
- more consistent public API contracts
- fewer import-time side effects

Do not wait for a full refactor to move in this direction. Prefer incremental improvements each time a related area is touched.
