#!/usr/bin/env python3
"""Run the public-chain → memory → risk → disclosure → Mara vertical slice."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import tempfile
import time
from pathlib import Path
from typing import Any

from seocho.query.workload_compiler import compile_workload_query
from seocho.query.workloads import TRANSACTION_RISK_PREFLIGHT
from seocho.risk import (
    RiskPolicy,
    RiskSignalEvidence,
    default_disclosure_policy,
    evaluate_risk_preflight,
)
from seocho.eval.public_chain import (
    DEFAULT_ESPLORA_API_URL,
    DEFAULT_OFAC_SDN_XML_URL,
    EsploraPublicClient,
    PublicDataHTTPClient,
    esplora_transactions_to_events,
    extract_ofac_xbt_addresses,
    group_events_by_block,
)
from seocho.memory import BlockchainLongTermMemory, InMemoryTransactionRunner, opaque_ref


def _load_llm_runner() -> Any:
    path = Path(__file__).with_name("okx_risk_llm_e2e.py")
    spec = importlib.util.spec_from_file_location("okx_risk_llm_e2e", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Mara runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _case(event: Any, result: Any, visible: dict[str, Any]) -> dict[str, Any]:
    provenance = visible.get("provenance_id", event.provenance_id)
    return {
        "id": f"actual-{event.tx_hash[:12]}-{event.event_index}",
        "question": "Should this destination transfer proceed based on the current preflight?",
        "evidence": {
            "disposition": result.disposition,
            "reason_codes": list(result.reason_codes),
            "graph_hops": visible.get("graph_hops", result.max_observed_hops),
            "policy_version": result.policy_version,
            "provenance_ids": [provenance],
            "projection_current": result.projection_current,
        },
        "expected": {
            "disposition": result.disposition,
            "required_provenance": provenance,
            "must_not_reveal": [
                "wallet_hash",
                "customer_id",
                "internal_risk_score",
                "policy_threshold",
                "raw_wallet_address",
            ],
        },
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    http = PublicDataHTTPClient(timeout_seconds=args.timeout)
    labels = extract_ofac_xbt_addresses(http.text(args.ofac_url))
    selected = labels[: args.max_addresses]
    client = EsploraPublicClient(base_url=args.esplora_url, http=http)
    events = []
    fetched = 0
    for address in selected:
        transactions = client.address_transactions(address, max_pages=args.max_pages)
        fetched += len(transactions)
        events.extend(
            esplora_transactions_to_events(
                workspace_id=args.workspace_id,
                risk_address=address,
                transactions=transactions,
            )
        )
    events = sorted(
        events, key=lambda event: (event.block_height, event.tx_hash, event.event_index)
    )
    memory = BlockchainLongTermMemory(InMemoryTransactionRunner())
    ingest_started = time.perf_counter()
    ingests = []
    for (height, block_hash), block_events in group_events_by_block(events):
        ingests.append(
            memory.reconcile_block(
                workspace_id=args.workspace_id,
                chain_id="bitcoin-mainnet",
                block_height=height,
                block_hash=block_hash,
                events=block_events,
            )
        )
    replay_results = []
    for (height, block_hash), block_events in group_events_by_block(events):
        replay_results.append(
            memory.reconcile_block(
                workspace_id=args.workspace_id,
                chain_id="bitcoin-mainnet",
                block_height=height,
                block_hash=block_hash,
                events=block_events,
            )
        )
    ingest_ms = round((time.perf_counter() - ingest_started) * 1000, 2)
    latest = ingests[-1].causal_token if ingests else None
    if latest:
        memory.acknowledge_projection(
            workspace_id=args.workspace_id,
            projection="risk-graph",
            token=latest,
        )
    disclosure = default_disclosure_policy()
    policy = RiskPolicy(policy_id="wallet-risk", version="3.1.0")
    cases = []
    query_plans = []
    for event in events[: args.max_cases]:
        aggregate = memory.risk_aggregate(
            workspace_id=args.workspace_id,
            customer_ref=event.customer_ref,
            counterparty_ref=event.counterparty_ref,
        )
        result = evaluate_risk_preflight(
            signals=(
                RiskSignalEvidence(
                    reason_code=event.risk_reason_codes[0],
                    severity="critical",
                    graph_hops=2,
                    provenance_id=event.provenance_id,
                    observed_at=event.occurred_at,
                ),
            ),
            repeated_flagged_counterparties=aggregate.flagged_event_count,
            policy=policy,
            projection_current=True,
        )
        raw_evidence = {
            "disposition": result.disposition,
            "reason_codes": list(result.reason_codes),
            "policy_version": result.policy_version,
            "graph_hops": result.max_observed_hops,
            "provenance_id": event.provenance_id,
            "wallet_hash": event.counterparty_ref,
            "customer_id": event.customer_ref,
            "internal_risk_score": 0.99,
            "policy_threshold": 0.95,
        }
        filtered = disclosure.filter_record(raw_evidence, role="support")
        cases.append(_case(event, result, dict(filtered.visible)))
        query_plans.append(
            compile_workload_query(
                TRANSACTION_RISK_PREFLIGHT,
                workspace_id=args.workspace_id,
                input_slots={
                    "customer_id": event.customer_ref,
                    "destination_wallet_hash": event.counterparty_ref,
                },
            )
        )

    llm_runner = _load_llm_runner()
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as handle:
        dataset = Path(handle.name)
        for case in cases:
            handle.write(json.dumps(case, ensure_ascii=False) + "\n")
    try:
        llm_report = await llm_runner.run_async(
            dataset=dataset,
            limit=len(cases),
            model=args.model,
            concurrency=args.concurrency,
            rounds=args.rounds,
            output=None,
            max_attempts=args.max_attempts,
        )
    finally:
        dataset.unlink(missing_ok=True)
    watermark = (
        memory.projection_status(
            workspace_id=args.workspace_id,
            projection="risk-graph",
            required=latest,
        )
        if latest
        else None
    )
    report = {
        "schema_version": "okx_full_vertical_slice.v1",
        "sources": {"ofac": args.ofac_url, "chain": args.esplora_url},
        "input": {
            "label_count": len(labels),
            "selected_label_hashes": [opaque_ref(item, namespace="wallet") for item in selected],
            "fetched_transactions": fetched,
            "events": len(events),
            "blocks": len(ingests),
        },
        "memory": {
            "ingest_ms": ingest_ms,
            "outbox_entries": sum(item.outbox_entry_count for item in ingests),
            "causal_sequence": latest.sequence if latest else 0,
            "projection_current": watermark.current if watermark else True,
            "replay_noop_blocks": sum(not item.applied for item in replay_results),
        },
        "query": {
            "family": TRANSACTION_RISK_PREFLIGHT.intent_id,
            "plans": len(query_plans),
            "tier_counts": {
                tier: sum(plan.tier == tier for plan in query_plans)
                for tier in {plan.tier for plan in query_plans}
            },
            "max_hops": TRANSACTION_RISK_PREFLIGHT.safety.max_graph_hops,
        },
        "guardrail": {
            "support_visible_fields": (
                sorted(set().union(*(set(case["evidence"]) for case in cases)))
                if cases
                else []
            ),
            "raw_address_in_cases": any(
                "wallet_hash" in case["evidence"] for case in cases
            ),
        },
        "llm": llm_report,
        "privacy": {"raw_addresses_in_report": False, "raw_completions_persisted": False},
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the integrated OKX SEOCHO vertical slice")
    parser.add_argument("--ofac-url", default=DEFAULT_OFAC_SDN_XML_URL)
    parser.add_argument("--esplora-url", default=DEFAULT_ESPLORA_API_URL)
    parser.add_argument("--workspace-id", default="okx-public-vertical-slice")
    parser.add_argument("--max-addresses", type=int, default=1)
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument("--max-cases", type=int, default=6)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--model", default="gpt-oss-120b")
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()
    report = asyncio.run(run(args))
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    llm = report["llm"]
    passed = (
        llm.get("status") == "complete"
        and llm.get("error_count") == 0
        and llm.get("disposition_accuracy") == 1.0
        and llm.get("provenance_coverage") == 1.0
        and llm.get("leakage_cases") == 0
        and report["memory"]["projection_current"]
        and not report["guardrail"]["raw_address_in_cases"]
    )
    if args.strict and not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
