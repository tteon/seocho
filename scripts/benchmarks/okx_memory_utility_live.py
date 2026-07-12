#!/usr/bin/env python3
"""Live utility evaluation for exchange-shaped long-term agent memory."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase

from seocho.eval.agent_memory_queries import (
    AGENT_MEMORY_QUERIES,
    build_augmented_prompt,
    classify_agent_memory_query,
    compile_agent_memory_query,
)
from seocho.eval.exchange_calibrated import generate_exchange_calibrated_events
from seocho.memory import PostgreSQLMemoryRepository
from seocho.store.llm import MaraBackend


def _safe(event: Any) -> dict[str, Any]:
    return {
        "intent_ref": event.intent_id,
        "sequence": event.sequence,
        "step": event.step,
        "actor": event.actor_agent,
        "recipient": event.recipient_agent,
        "provenance": event.provenance_id,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    workspace = f"memory-utility-{uuid.uuid4().hex[:10]}"
    events = list(
        generate_exchange_calibrated_events(
            intent_count=args.intents, seed=args.seed, workspace_id=workspace
        )
    )
    grouped: dict[str, list[Any]] = defaultdict(list)
    for event in events:
        grouped[event.intent_id].append(event)
    representatives = {
        scenario: next(values for values in grouped.values() if values[0].scenario == scenario)
        for scenario in {query.scenario for query in AGENT_MEMORY_QUERIES}
    }

    repository = PostgreSQLMemoryRepository.connect(args.dsn)
    ingest_started = time.perf_counter()
    applied = 0
    for event in events:
        result = repository.commit_revision(
            workspace_id=workspace,
            memory_id=event.intent_id,
            event_type=f"exchange.{event.step}",
            occurred_at=event.ingest_time,
            provenance_id=event.provenance_id,
            payload=event.to_dict(),
            idempotency_key=event.event_id,
        )
        applied += int(result.applied)
    ingest_ms = (time.perf_counter() - ingest_started) * 1000

    lag_intent = representatives["unknown_then_reconciled"][0].intent_id
    graph_rows = []
    for values in grouped.values():
        for index, event in enumerate(values):
            if event.intent_id == lag_intent and index == len(values) - 1:
                continue
            graph_rows.append(
                {
                    **_safe(event),
                    "event_id": event.event_id,
                    "parent_id": event.causal_parent_id,
                    "workspace": workspace,
                }
            )
    driver = GraphDatabase.driver(args.bolt_uri, auth=("neo4j", args.graph_password))
    project_started = time.perf_counter()
    with driver.session() as session:
        for offset in range(0, len(graph_rows), 500):
            session.run(
                "UNWIND $rows AS row "
                "MERGE (i:ExchangeIntent {id:row.intent_ref, workspace:row.workspace}) "
                "MERGE (e:ExchangeMemoryEvent {id:row.event_id, workspace:row.workspace}) "
                "SET e.sequence=row.sequence,e.step=row.step,e.actor=row.actor,"
                "e.recipient=row.recipient,e.provenance=row.provenance "
                "MERGE (i)-[:HAS_EVENT]->(e) "
                "WITH row,e OPTIONAL MATCH (p:ExchangeMemoryEvent {id:row.parent_id,workspace:row.workspace}) "
                "FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END | MERGE (p)-[:NEXT]->(e))",
                rows=graph_rows[offset : offset + 500],
            ).consume()
    projection_ms = (time.perf_counter() - project_started) * 1000

    backend = MaraBackend(model=args.model)
    rows = []
    for query in AGENT_MEMORY_QUERIES:
        classified = classify_agent_memory_query(query.question)
        if classified is None or classified.query_id != query.query_id:
            raise RuntimeError(f"intent classification failed: {query.query_id}")
        values = representatives[query.scenario]
        expected = values[-1].step
        plan = compile_agent_memory_query(
            classified,
            workspace_id=workspace,
            intent_id=values[0].intent_id,
        )
        with driver.session() as session:
            records = session.run(plan.cypher, **plan.params).data()
        projection_lag = values[-1].sequence > max(
            (record["sequence"] for record in records), default=0
        )
        steps = [record["step"] for record in records]
        evidence = {
            "state": expected,
            "events": records,
            "projection_lag": projection_lag,
            "support_status": "partial" if projection_lag else "supported",
        }
        system, user_prompt, prompt_metadata = build_augmented_prompt(
            classified, evidence=evidence
        )
        started = time.perf_counter()
        response = await backend.acomplete(
            system=system,
            user=user_prompt,
            temperature=0.0,
            max_tokens=500,
            response_format={"type": "json_object"},
            mode="pipeline",
            model=args.model,
        )
        parsed = response.json()
        rendered = json.dumps(parsed)
        rows.append(
            {
                "query_id": query.query_id,
                "classified_query_id": classified.query_id,
                "query_tier": plan.tier,
                "prompt_prefix_hash": prompt_metadata["prompt_prefix_hash"],
                "expected_state": expected,
                "answer_state": parsed.get("state"),
                "state_correct": parsed.get("state") == expected,
                "support_status_correct": parsed.get("support_status")
                == ("partial" if projection_lag else "supported"),
                "evidence_contract_ok": (
                    bool(records)
                    and bool(records[-1].get("provenance"))
                    and (
                        query.query_id != "cancel-fill-race"
                        or ("cancel_requested" in steps and "filled" in steps)
                    )
                    and (
                        query.query_id != "agent-handoff"
                        or all(
                            record.get("actor") and record.get("recipient")
                            for record in records
                        )
                    )
                ),
                "projection_lag_detected": projection_lag if query.query_id == "projection-lag" else None,
                "leakage": any(field in rendered for field in query.denied_fields),
                "graph_event_count": len(records),
                "llm_ms": round((time.perf_counter() - started) * 1000, 3),
            }
        )

    target = representatives["reconnect_snapshot"]
    optimized = [_safe(event) for event in target]
    noise = [event for event in events if event.intent_id != target[0].intent_id]
    full = [_safe(event) for event in noise[-args.full_context_events :] + target]
    expected = target[-1].step
    context_arms = []
    for arm, context in (("full", full), ("optimized", optimized)):
        long_query = next(query for query in AGENT_MEMORY_QUERIES if query.query_id == "long-context")
        system, user_prompt, _ = build_augmented_prompt(
            long_query,
            evidence={"state": expected, "target": target[0].intent_id, "events": context, "support_status": "supported"},
        )
        response = await backend.acomplete(
            system=system,
            user=user_prompt,
            temperature=0.0,
            max_tokens=500,
            response_format={"type": "json_object"},
            mode="pipeline",
            model=args.model,
        )
        payload = response.json()
        serialized = json.dumps(context, sort_keys=True)
        context_arms.append(
            {
                "arm": arm,
                "events": len(context),
                "estimated_tokens": round(len(serialized) / 4),
                "state_correct": payload.get("state") == expected,
            }
        )
    driver.close()
    full_arm, optimized_arm = context_arms
    token_reduction = 1 - optimized_arm["estimated_tokens"] / full_arm["estimated_tokens"]
    unique_event_count = len({event.event_id for event in events})
    passed = (
        applied == unique_event_count
        and all(
            (row["state_correct"] or row["query_id"] == "projection-lag")
            and row["support_status_correct"]
            and row["evidence_contract_ok"]
            and not row["leakage"]
            for row in rows
        )
        and rows[4]["projection_lag_detected"] is True
        and all(arm["state_correct"] for arm in context_arms)
    )
    return {
        "schema_version": "seocho.okx-memory-utility-live.v1",
        "source": "synthetic-exchange-shaped-live-postgresql-dozerdb-mara",
        "workspace_hash": hashlib.sha256(workspace.encode()).hexdigest()[:16],
        "intents": args.intents,
        "events": len(events),
        "unique_events": unique_event_count,
        "duplicate_deliveries": len(events) - unique_event_count,
        "applied_revisions": applied,
        "ingest_ms": round(ingest_ms, 3),
        "projection_ms": round(projection_ms, 3),
        "model": args.model,
        "query_pass_count": sum(
            (row["state_correct"] or row["query_id"] == "projection-lag")
            and row["support_status_correct"]
            and row["evidence_contract_ok"]
            and not row["leakage"]
            for row in rows
        ),
        "query_count": len(rows),
        "llm_p95_ms": sorted(row["llm_ms"] for row in rows)[round((len(rows) - 1) * 0.95)],
        "context_ab": {"arms": context_arms, "estimated_token_reduction": round(token_reduction, 4)},
        "rows": rows,
        "passed": passed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--bolt-uri", default="bolt://127.0.0.1:7687")
    parser.add_argument("--graph-password", required=True)
    parser.add_argument("--model", default="gpt-oss-120b")
    parser.add_argument("--intents", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260711)
    parser.add_argument("--full-context-events", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
