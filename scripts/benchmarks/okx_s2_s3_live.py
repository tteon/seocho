#!/usr/bin/env python3
"""Live S2 concurrent-writer and S3 point-in-time isolation scenarios."""

from __future__ import annotations

import argparse
import json
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import psycopg

from seocho.memory import PostgreSQLMemoryRepository


def run(dsn: str) -> dict:
    workspace = f"s2-s3-{uuid.uuid4().hex[:12]}"
    repository = PostgreSQLMemoryRepository.connect(dsn)
    memory_id = "intent-concurrent-001"
    initial = repository.commit_revision(
        workspace_id=workspace,
        memory_id=memory_id,
        event_type="intent_created",
        occurred_at="2026-07-12T00:00:00+00:00",
        provenance_id="scenario:s2:initial",
        payload={"state": "intent_created"},
        idempotency_key="s2-initial",
    )

    def writer(state: str):
        return repository.commit_revision(
            workspace_id=workspace,
            memory_id=memory_id,
            event_type=state,
            occurred_at="2026-07-12T00:00:01+00:00",
            provenance_id=f"scenario:s2:{state}",
            payload={"state": state},
            idempotency_key=f"s2-{state}",
            allowed_previous_event_types=("intent_created",),
        )

    successes = []
    rejections = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(writer, state): state for state in ("approved", "placed", "cancel_requested")}
        for future in as_completed(futures):
            try:
                successes.append(future.result().revision.event_type)
            except ValueError:
                rejections.append(futures[future])
    with psycopg.connect(dsn) as connection:
        revisions, outbox = connection.execute(
            "SELECT (SELECT count(*) FROM agent_memory_revisions WHERE workspace_id=%s),"
            "(SELECT count(*) FROM agent_memory_outbox WHERE workspace_id=%s)",
            (workspace, workspace),
        ).fetchone()
    s2_passed = (
        initial.applied and len(successes) == 1 and len(rejections) == 2
        and revisions == 2 and outbox == 2
    )

    historical_id = "intent-history-001"
    partial = repository.commit_revision(
        workspace_id=workspace,
        memory_id=historical_id,
        event_type="partially_filled",
        occurred_at="2026-07-12T00:01:00+00:00",
        provenance_id="scenario:s3:partial",
        payload={"state": "partially_filled", "fill_size": "1"},
        idempotency_key="s3-partial",
    )
    settled = repository.commit_revision(
        workspace_id=workspace,
        memory_id=historical_id,
        event_type="settled",
        occurred_at="2026-07-12T00:02:00+00:00",
        provenance_id="scenario:s3:settled",
        payload={"state": "settled", "fill_size": "2"},
        idempotency_key="s3-settled",
        allowed_previous_event_types=("partially_filled",),
    )
    historical = repository.read_revision(
        workspace_id=workspace,
        memory_id=historical_id,
        at_sequence=partial.causal_token.sequence,
    )
    current = repository.read_revision(workspace_id=workspace, memory_id=historical_id)
    historical_bundle = {
        "state": historical.payload["state"],
        "provenance": historical.provenance_id,
        "sequence": historical.sequence,
    }
    current_bundle = {
        "state": current.payload["state"],
        "provenance": current.provenance_id,
        "sequence": current.sequence,
    }
    s3_passed = (
        historical_bundle == {
            "state": "partially_filled",
            "provenance": "scenario:s3:partial",
            "sequence": partial.causal_token.sequence,
        }
        and current_bundle["state"] == "settled"
        and current_bundle["sequence"] == settled.causal_token.sequence
        and "settled" not in json.dumps(historical_bundle)
    )
    return {
        "schema_version": "seocho.s2-s3-live.v1",
        "source": "live-postgresql",
        "s2": {
            "successes": successes,
            "rejections": sorted(rejections),
            "revision_count": revisions,
            "outbox_count": outbox,
            "passed": s2_passed,
        },
        "s3": {
            "historical": historical_bundle,
            "current": current_bundle,
            "cross_contamination": "settled" in json.dumps(historical_bundle),
            "passed": s3_passed,
        },
        "passed": s2_passed and s3_passed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.dsn)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
