#!/usr/bin/env python3
"""Evaluate blockchain memory against current public Bitcoin/OFAC data."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a bounded public-mainnet blockchain memory benchmark."
    )
    parser.add_argument("--ofac-url", default=DEFAULT_OFAC_SDN_XML_URL)
    parser.add_argument("--esplora-url", default=DEFAULT_ESPLORA_API_URL)
    parser.add_argument("--workspace-id", default="okx-public-mainnet-benchmark")
    parser.add_argument("--max-addresses", type=int, default=2)
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument("--output")
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.max_addresses < 1 or args.max_pages < 1:
        raise ValueError("max-addresses and max-pages must be positive")
    http = PublicDataHTTPClient()
    addresses = extract_ofac_xbt_addresses(http.text(args.ofac_url))
    esplora = EsploraPublicClient(base_url=args.esplora_url, http=http)
    selected = addresses[: args.max_addresses]
    events = []
    fetched_transactions = 0
    for address in selected:
        transactions = esplora.address_transactions(address, max_pages=args.max_pages)
        fetched_transactions += len(transactions)
        events.extend(
            esplora_transactions_to_events(
                workspace_id=args.workspace_id,
                risk_address=address,
                transactions=transactions,
            )
        )

    memory = BlockchainLongTermMemory(InMemoryTransactionRunner())
    grouped = group_events_by_block(events)
    started = time.perf_counter()
    results = []
    for (height, block_hash), block_events in grouped:
        results.append(
            memory.reconcile_block(
                workspace_id=args.workspace_id,
                chain_id="bitcoin-mainnet",
                block_height=height,
                block_hash=block_hash,
                events=block_events,
            )
        )
    elapsed = time.perf_counter() - started

    replay_results = []
    for (height, block_hash), block_events in grouped:
        replay_results.append(
            memory.reconcile_block(
                workspace_id=args.workspace_id,
                chain_id="bitcoin-mainnet",
                block_height=height,
                block_hash=block_hash,
                events=block_events,
            )
        )

    report = {
        "schema_version": "okx_public_chain_memory_benchmark.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": {
            "sanctions": args.ofac_url,
            "chain": args.esplora_url,
            "label_semantics": "address interaction; no wallet-owner attribution",
        },
        "input": {
            "available_xbt_labels": len(addresses),
            "selected_label_hashes": [
                opaque_ref(address, namespace="wallet") for address in selected
            ],
            "fetched_transactions": fetched_transactions,
            "confirmed_interaction_events": len(events),
            "canonical_blocks": len(grouped),
        },
        "memory": {
            "applied_blocks": sum(result.applied for result in results),
            "outbox_entries": sum(result.outbox_entry_count for result in results),
            "elapsed_ms": round(elapsed * 1000, 3),
            "events_per_second": round(len(events) / elapsed, 2) if elapsed else 0,
            "replay_noop_blocks": sum(not result.applied for result in replay_results),
        },
        "privacy": {
            "raw_addresses_in_report": False,
            "raw_transaction_payloads_persisted": False,
        },
    }
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def main() -> None:
    args = _parser().parse_args()
    print(json.dumps(run(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
