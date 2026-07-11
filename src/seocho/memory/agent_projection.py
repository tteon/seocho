"""DozerDB projection contract for authoritative agent transaction memory."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..tracing import start_span
from ..metrics import get_metrics


@dataclass(frozen=True, slots=True)
class AgentProjectionEntry:
    workspace_id: str
    sequence: int
    ordinal: int
    aggregate_id: str
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class AgentProjectionResult:
    applied_entries: int
    applied_sequence: int
    nodes_written: int
    relationships_written: int


class AgentTransactionProjector:
    """Convert outbox entries into idempotent property-graph upserts."""

    def __init__(self, *, graph_store: Any, repository: Any) -> None:
        self._graph_store = graph_store
        self._repository = repository

    def project_pending(
        self, *, workspace_id: str, database: str, limit: int = 100
    ) -> AgentProjectionResult:
        started = time.perf_counter()
        metrics = get_metrics()
        entries = self._repository.read_outbox_batch(
            workspace_id=workspace_id, limit=limit
        )
        if not entries:
            metrics.set(
                "seocho.projection.outbox.pending", 0, {"projection": database}
            )
            return AgentProjectionResult(0, 0, 0, 0)
        nodes, relationships = self._build_graph(entries)
        max_sequence = max(entry.sequence for entry in entries)
        try:
            with start_span(
                "projection.batch",
                metadata={
                    "seocho.projection.entry_count": len(entries),
                    "seocho.projection.max_sequence": max_sequence,
                    "seocho.projection.database": database,
                },
            ):
                summary = self._graph_store.write(
                    nodes,
                    relationships,
                    database=database,
                    workspace_id=workspace_id,
                    source_id=f"agent-memory:{max_sequence}",
                )
                self._repository.acknowledge_projection(
                    workspace_id=workspace_id,
                    projection=database,
                    applied_sequence=max_sequence,
                    entries=entries,
                )
        except Exception:
            metrics.record(
                "seocho.projection.batch.duration",
                time.perf_counter() - started,
                {"projection": database, "outcome": "error"},
            )
            raise
        metrics.record(
            "seocho.projection.batch.duration",
            time.perf_counter() - started,
            {"projection": database, "outcome": "success"},
        )
        metrics.record(
            "seocho.projection.batch.entry_count",
            len(entries),
            {"projection": database},
        )
        metrics.set(
            "seocho.projection.watermark",
            max_sequence,
            {"projection": database},
        )
        return AgentProjectionResult(
            applied_entries=len(entries),
            applied_sequence=max_sequence,
            nodes_written=int(summary.get("nodes_created", len(nodes)) or 0),
            relationships_written=int(
                summary.get("relationships_created", len(relationships)) or 0
            ),
        )

    @staticmethod
    def _build_graph(
        entries: Sequence[AgentProjectionEntry],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        nodes: dict[str, dict[str, Any]] = {}
        relationships: dict[tuple[str, str, str, str], dict[str, Any]] = {}

        def node(node_id: str, label: str, properties: Mapping[str, Any]) -> None:
            nodes[node_id] = {
                "id": node_id,
                "label": label,
                "properties": dict(properties),
            }

        def rel(
            source: str,
            target: str,
            rel_type: str,
            event_id: str,
            properties: Mapping[str, Any],
        ) -> None:
            relationships[(source, target, rel_type, event_id)] = {
                "source": source,
                "target": target,
                "type": rel_type,
                "properties": dict(properties),
            }

        for entry in entries:
            payload = dict(entry.payload)
            event_id = str(payload["event_id"])
            intent_id = str(payload["transaction_intent_id"])
            actor = str(payload["actor_agent"])
            recipient = str(payload["recipient"])
            order_id = str(payload["exchange_order_ref"])
            common = {
                "workspace_id": entry.workspace_id,
                "memory_sequence": entry.sequence,
                "schema_version": payload.get("schema_version", ""),
            }
            actor_label = "Exchange" if actor == "okx_demo" else "Agent"
            node(actor, actor_label, {**common, "agent_id": actor})
            recipient_label = "Exchange" if recipient == "okx_demo" else "Agent"
            node(recipient, recipient_label, {**common, "actor_id": recipient})
            node(
                intent_id,
                "TransactionIntent",
                {
                    **common,
                    "intent_id": intent_id,
                    "decision": payload.get("decision", ""),
                    "conversation_id": payload.get("conversation_id", ""),
                },
            )
            node(
                order_id,
                "Order",
                {
                    **common,
                    "order_ref": order_id,
                    "instrument_id": payload.get("instrument_id", ""),
                    "state": payload.get("exchange_state", ""),
                    "side": payload.get("side", ""),
                    "size": payload.get("size", "0"),
                },
            )
            rel(actor, intent_id, "ACTED_ON", event_id, {**common, "action": payload["action"]})
            rel(actor, recipient, "HANDED_OFF_TO", event_id, {**common, "event_id": event_id})
            rel(intent_id, order_id, "MATERIALIZED_AS", event_id, common)

            action = str(payload["action"])
            if action in {"partial_fill", "fill_order"}:
                fill_id = f"fill:{event_id}"
                node(
                    fill_id,
                    "Fill",
                    {
                        **common,
                        "fill_size": payload.get("accumulated_fill_size", "0"),
                        "average_price": payload.get("average_fill_price", "0"),
                    },
                )
                rel(order_id, fill_id, "HAS_FILL", event_id, common)
            if action == "settle_position":
                settlement_id = f"settlement:{intent_id}"
                node(settlement_id, "Settlement", {**common, "status": "settled"})
                rel(order_id, settlement_id, "SETTLED_BY", event_id, common)
            if action in {"publish_memory", "record_rejection"}:
                memory_id = f"memory:{intent_id}:{entry.sequence}"
                node(memory_id, "MemoryRevision", {**common, "event_id": event_id})
                rel(intent_id, memory_id, "RECORDED_AS", event_id, common)

        return list(nodes.values()), list(relationships.values())


__all__ = [
    "AgentProjectionEntry",
    "AgentProjectionResult",
    "AgentTransactionProjector",
]
