# OKX-style transaction risk preflight

This synthetic example demonstrates the second observable workload without
using customer data or executing a transaction.

It separates three responsibilities:

1. Coordination pointers suitable for etcd: active policy version and graph
   projection watermark.
2. Authoritative risk evidence: represented here as in-memory typed objects;
   the production target is FoundationDB plus a rebuildable graph projection.
3. Disclosure filtering: a compiled ontology property policy removes fields
   the caller's role or subject policy may not receive before an LLM prompt is
   assembled.

Run from the repository root:

    uv run python examples/okx-risk-preflight/quickstart.py

Expected result: the two-hop critical signal produces `policy_block`, while
the customer-facing payload contains only disposition, safe reason codes, and
policy version. It does not contain a wallet address, customer identifier,
internal score, provenance identifier, or policy threshold.
