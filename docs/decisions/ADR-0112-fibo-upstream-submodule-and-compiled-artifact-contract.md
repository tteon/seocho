# ADR-0112: FIBO Upstream Submodule And Compiled Artifact Contract

Date: 2026-06-14
Status: Accepted

## Context

SEOCHO already has FIBO-aware examples, curated FIBO module slices, TTL/OWL
loading helpers, FIBO runtime selection descriptors, and offline ontology
governance checks. That is enough to show FIBO alignment, but it does not fully
answer two operational questions:

- which official EDM Council FIBO revision a run was grounded against
- whether SEOCHO's curated runtime slices still match the official upstream
  ontology after FIBO changes

The official FIBO repository is large and OWL/RDF-oriented. Reading it directly
from request paths would violate SEOCHO's runtime guardrail that heavy ontology
reasoning stays out of the hot path.

## Decision

SEOCHO will vendor the official EDM Council FIBO repository as a pinned git
submodule at:

- `third_party/fibo`

The submodule is a source snapshot only. Runtime indexing and querying must use
compiled JSON artifacts, not the raw FIBO checkout.

An offline compiler now emits:

- `manifest.json`: pinned commit, ontology headers, imports, module/resource
  counts, and snapshot hash
- `catalog.json`: per-module label, definition, and IRI index for runtime
  selection
- `compatibility_report.json`: comparison between official FIBO labels/IRIs and
  SEOCHO's curated LPG FIBO slices
- `artifact_index.json`: small pointer file that records the source/runtime
  contract

Default output:

- `outputs/semantic_artifacts/fibo/latest`

The compiler intentionally does lightweight RDF/XML scanning and does not invoke
OWL reasoning. Owlready2/reasoner checks remain an optional offline governance
gate, not a request-path dependency.

## Consequences

Positive:

- FIBO-backed experiments can cite a concrete upstream commit and snapshot hash.
- SEOCHO can compare curated LPG slices against official FIBO labels and IRIs.
- Runtime selectors can stay fast by reading compiled catalogs only.
- FIBO updates can be benchmark-gated before promotion.

Tradeoffs:

- Developers must initialize/update the submodule when working on FIBO sync:
  `git submodule update --init --recursive`.
- The compiler is not a faithful OWL round-trip; it extracts governance and
  runtime-selection signals.
- Large upstream FIBO updates may change many labels/imports and require
  compatibility review before artifact approval.

## Implementation Notes

- Source snapshot: `third_party/fibo`
- Compiler: `scripts/ontology/compile_fibo_snapshot.py`
- Test: `tests/test_fibo_snapshot_compiler.py`
- Compiled artifacts: `outputs/semantic_artifacts/fibo/latest`
- Runtime rule: use compiled catalog/artifacts, not raw submodule OWL/RDF
