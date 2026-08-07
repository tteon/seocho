#!/usr/bin/env python3
"""Offline text-chunk -> isolated-view -> SDCR smoke test.

This validates the product contract without requiring DozerDB or an LLM key.
The runtime ingest endpoint uses the same workspace/category fields before
handing records to the canonical extraction engine.
"""
from __future__ import annotations

import json
from typing import Any

from seocho.query.otel_observability import OTelBridge
from seocho.query.sdcr import Capability, CapabilityRegistry, Evidence, SDCRRouter, verify_conflicts


def run(text: str, *, workspace_id: str = "smoke-workspace") -> dict[str, Any]:
    financial = Evidence("chunk:financial", "financials", "revenue_fy2023", "2.1B", provenance={"chunk": text[:80]})
    legal = Evidence("chunk:legal", "legal", "legal_risk_current", "patent_dispute", provenance={"chunk": text[80:]})
    registry = CapabilityRegistry(
        [
            Capability("financials", frozenset({financial.slot}), priority=2),
            Capability("legal", frozenset({legal.slot}), priority=1),
        ]
    )
    receipt = SDCRRouter().route(
        workspace_id=workspace_id,
        required_slots=[financial.slot, legal.slot],
        capabilities=registry.authorized(workspace_id),
    )
    evidence = [financial, legal]
    bridge = OTelBridge()
    bridge.record_route(receipt.reason, len(receipt.selected_views))
    for view in receipt.selected_views:
        bridge.record_agent_call(view)
    return {
        "workspace_id": workspace_id,
        "input_chars": len(text),
        "isolated_views": registry.snapshot(),
        "selected_evidence": [item.source_id for item in evidence if item.view_id in receipt.selected_views],
        "conflict_verification": verify_conflicts(evidence),
        "sdcr_receipt": receipt.as_dict(),
        "otel": "no-op unless an OTEL meter/tracer is injected",
    }


if __name__ == "__main__":
    chunk = "Acme reported revenue of $2.1 billion in fiscal year 2023. " "The company is involved in an ongoing patent dispute."
    print(json.dumps(run(chunk), indent=2, ensure_ascii=False))
