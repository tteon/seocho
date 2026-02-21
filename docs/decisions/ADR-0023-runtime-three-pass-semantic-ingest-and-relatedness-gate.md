# ADR-0023: Runtime Three-Pass Semantic Ingest and Relatedness Gate

- Date: 2026-02-21
- Status: Accepted

## Context

Runtime ingest previously treated input as plain text and used a single extraction+linking call.
To align with SEOCHO philosophy, ingest must support:

1. heterogeneous raw material parsing (`text`/`csv`/`pdf`)
2. LLM-driven semantic-layer extraction (ontology + SHACL + entities)
3. continuity-aware linking decisions when new records arrive

## Decision

1. Add raw material parser layer:
   - `raw_material_parser.py`
   - normalize `source_type=text|csv|pdf` into extraction-ready text
2. Add three-pass semantic orchestrator:
   - pass A: ontology candidate extraction
   - pass B: SHACL candidate extraction
   - pass C: entity graph extraction with pass A/B context injection
3. Add relatedness gate before linking:
   - compare candidate entities with known entities in target graph + batch memory
   - run linker when relatedness threshold is met (or bootstrap record)
4. Return semantic artifacts in runtime ingest response:
   - merged ontology candidate
   - merged SHACL candidate
   - relatedness summary
5. Keep fallback extraction path for local/offline reliability.

## Consequences

Positive:

- runtime ingest now matches semantic-layer-first design intent
- PDF/CSV onboarding path no longer requires manual pre-normalization
- linking decisions become explainable through explicit relatedness metadata

Tradeoffs:

- more LLM calls per record in full path
- more response metadata and ingest-path complexity

## Implementation Notes

Key files:

- `extraction/raw_material_parser.py`
- `extraction/semantic_pass_orchestrator.py`
- `extraction/runtime_ingest.py`
- `extraction/agent_server.py`
- `extraction/conf/prompts/default.yaml`
- tests under `extraction/tests/`
