# ADR-0024: OCR Fallback, Embedding Relatedness, and Artifact Approval Gate

- Date: 2026-02-21
- Status: Accepted

## Context

Runtime ingest required practical hardening after three-pass rollout:

1. scanned PDFs could fail text extraction when `pypdf` returns empty text
2. relatedness gating based only on lexical overlap could miss semantically close records
3. governance users needed explicit control over whether new ontology/SHACL candidates are auto-applied

## Decision

1. Add OCR fallback in PDF parser:
   - when `pypdf` extraction is empty, attempt OCR via optional stack (`PyMuPDF + pytesseract + Pillow`)
2. Extend relatedness with embedding signal:
   - keep lexical overlap score
   - add optional embedding cosine score and threshold-based decision path
3. Add semantic artifact approval gate in runtime ingest:
   - `auto`: apply draft artifacts immediately
   - `draft_only`: keep draft artifacts, do not apply to rule profile
   - `approved_only`: apply only provided approved artifacts

## Consequences

Positive:

- better ingest reliability for scanned PDFs
- more robust linking decisions across heterogeneous records
- governance-safe control over semantic artifact rollout

Tradeoffs:

- OCR fallback depends on optional third-party/system tools
- embedding calls add latency and API cost when enabled
- ingest response contract grows with artifact decision metadata

## Implementation Notes

Key files:

- `extraction/raw_material_parser.py`
- `extraction/runtime_ingest.py`
- `extraction/agent_server.py`
- `extraction/tests/test_raw_material_parser.py`
- `extraction/tests/test_runtime_ingest.py`
