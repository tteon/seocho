# ADR-0232: Public product, experiment and contribution entrypoints

Date: 2026-09-13
Status: Accepted

## Context

The README mixed product and maintainer detail and described an embedded
first-run path that no longer matched the Bolt-only CLI. GitHub's repository
summary and machine-readable guidance were also stale. Users need to understand
what SEOCHO does, execute their own data, and report a failed run with enough
context for maintainers to act.

## Decision

Make README a product-first route through capabilities, actual prerequisites,
first run, user-data comparison, SDK/runtime usage and contribution. Keep full
protocols in the existing docs contracts. Link the docs index and llms.txt to
experiment and agent workflows; remove serverless-first-run claims. Issue forms
collect optional redacted E2E diagnostics and intended evaluation criteria. PRs
separate behavior, compatibility, validation, experiment evidence and gaps.

Align GitHub about metadata with the ontology-aligned middleware description,
the existing documentation homepage and discoverable technical topics. Keep
public issue/PR tracking distinct from local beads and private run receipts.

## Consequences

Public explanations are narrower and testable. CLI success is never presented
as answer accuracy or production readiness. Different audiences share one set
of source docs rather than new duplicated onboarding documents. Both existing
website generators must accept the source links. No deployment, pricing,
licensing or backend compatibility claim changes in this decision.

## Validation

Check source Markdown links, parse issue YAML, run repository/agent/root/doc
contracts and both required website workflows. Verify actual GitHub about
metadata after updating it. Treat the README's live graph/model examples as
instructions with named prerequisites, not a new benchmark result.
