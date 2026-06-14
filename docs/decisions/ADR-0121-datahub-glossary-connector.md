# ADR-0121: DataHub Connector (PoC) — Ontology → Business Glossary

Date: 2026-06-14
Status: Proposed

## Context

The ambiguity-review mapping surface and the broader distribution play target
**DataHub**, not a bespoke Streamlit app (user decision, 2026-06-14): couple to an
existing metadata ecosystem the user already operates. SEOCHO remains the
authoring/quality engine (scorecard + OntoClean — which DataHub lacks); DataHub
provides the glossary tree, search, lineage, and approval workflow we ride rather
than rebuild.

## Decision

Add `seocho.datahub_export` (PoC, Phase A): a **pure, offline** mapping from an
Ontology to DataHub Business-Glossary Metadata Change Proposals (MCPs), plus an
optional, import-guarded live emitter.

- `ontology_to_glossary_mcps(ontology)` → list of MCP dicts (the shape the
  `datahub` SDK's `MetadataChangeProposalWrapper` serializes to). Mapping:
  package → `glossaryNode`; class → `glossaryTerm` (definition, parentNode,
  customProperties: aliases / same_as / identity_keys / version); `broader` →
  `glossaryRelatedTerms.isRelatedTerms` (DataHub "Is A"); relationship types →
  terms under a `<package> Relationships` node with source/target/cardinality.
- Deterministic URNs (`urn:li:glossaryTerm:<package_id>.<label>`) → re-export is
  an idempotent UPSERT.
- `emit_to_datahub(mcps, gms_server=, token=, dry_run=)` — emits via
  `DatahubRestEmitter` when the `datahub` SDK + a server are available; otherwise
  returns the dry-run payload (never crashes). CLI: `seocho ontology datahub
  --schema X [--output mcps.json] [--gms URL --token T --emit]`.

Offline/data-plane; the connector core has no `datahub` dependency (testable
without it). Exact aspect field names follow DataHub's documented model and must
be verified against the target `datahub` version when wiring live emit.

## Validation

`tests/seocho/test_datahub_export.py` (7): package node + terms count, deterministic/
idempotent URNs, customProperties carry SEOCHO metadata, is-a edge aspect,
relationship endpoints, dry-run + no-server safety. CLI demo: `fibo_plus.jsonld`
→ 19 MCPs (17 terms, 2 nodes). `run_basic_ci` green.

## Consequences

- SEOCHO's governed ontology becomes visible/actionable in DataHub without
  rebuilding a UI; the glossary is the natural home for the ambiguity-review
  mapping decisions (seocho-2mg Phase 3) and for surfacing scorecard/OntoClean
  governance.
- Follow-ups (seocho-qxj): Phase B — emit ambiguity clusters as glossary-term
  proposals + round-trip the mapping-spec; Phase C — scorecard/OntoClean verdicts
  as Structured Properties and numeric-validation (P3) results as Assertions /
  Data Contracts; verify aspect shapes against a live DataHub; add `datahub` as an
  optional extra (`seocho[datahub]`).
