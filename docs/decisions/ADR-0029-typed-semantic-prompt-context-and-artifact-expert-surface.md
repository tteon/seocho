# ADR-0029: Typed Semantic Prompt Context And Artifact Expert Surface

Date: 2026-03-13
Status: Accepted

## Context

SEOCHO already had runtime support for semantic artifacts and per-request metadata overrides, but the expert developer surface was still too ad-hoc:

- Python callers had to pass raw dictionaries for ontology, SHACL, and vocabulary hints
- artifact lifecycle operations were only available through low-level HTTP endpoints
- there was no single documented precedence model for graph metadata, approved artifacts, request overrides, and runtime drafts

That shape made advanced usage possible, but not stable enough for teams that want to inject governed ontology or vocabulary into entity extraction and linking flows.

## Decision

SEOCHO will expose a typed expert surface above the existing runtime APIs:

1. add typed SDK models for semantic prompt context, approved artifacts, and semantic artifact payloads
2. expose artifact lifecycle operations in the official SDK and CLI
3. keep the governed path as the default expert workflow via approved semantic artifacts
4. keep `prompt_context` as a per-request override layer, not the primary governance mechanism
5. apply semantic prompt composition with this precedence:
   - graph target metadata
   - approved semantic artifacts
   - request-level semantic prompt context
   - runtime draft candidates

## Consequences

Positive:

- advanced developers can add ontology, SHACL, vocabulary, and known-entity hints without hand-authoring fragile JSON everywhere
- artifact governance is accessible from both Python and CLI workflows
- entity extraction and entity linking receive a stable, explicit prompt-composition contract

Tradeoffs:

- the SDK surface area grows beyond the minimal memory-first path
- typed models still need to coexist with dict input for compatibility
- lifecycle ergonomics improve, but bulk operations such as `delete --all` remain separate follow-up work

## Implementation Notes

- typed semantic models live in `seocho/semantic.py`
- artifact lifecycle methods are exposed from `seocho/client.py`
- expert CLI operations live under `seocho artifacts ...`
- runtime prompt composition remains centralized in `extraction/semantic_context.py`
