"""Authoritative, versioned blockchain memory over a transactional KV runner."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from typing import Any, Mapping, Sequence

from ..tracing import log_span
from .kv import MemoryKey, MemoryTransaction, TransactionRunner
from .models import (
    BlockIngestResult,
    CausalToken,
    ProjectionOutboxEntry,
    ProjectionStatus,
    RiskAggregate,
    TransactionEvent,
    TransactionEventRevision,
    workspace_token,
)


_SCHEMA = "v1"
_MAX_VALUE_BYTES = 90_000


class BlockReplayConflictError(ValueError):
    """Raised when the same block hash is replayed with different contents."""


def _json_bytes(value: Any) -> bytes:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    if len(encoded) > _MAX_VALUE_BYTES:
        raise ValueError("authoritative-memory value exceeds 90 KiB")
    return encoded


def _json_value(value: bytes | None, *, default: Any) -> Any:
    if value is None:
        return default
    return json.loads(value.decode("utf-8"))


def _event_from_dict(value: Mapping[str, Any]) -> TransactionEvent:
    return TransactionEvent(
        workspace_id=str(value["workspace_id"]),
        chain_id=str(value["chain_id"]),
        block_height=int(value["block_height"]),
        block_hash=str(value["block_hash"]),
        tx_hash=str(value["tx_hash"]),
        event_index=int(value["event_index"]),
        customer_ref=str(value["customer_ref"]),
        counterparty_ref=str(value["counterparty_ref"]),
        provenance_id=str(value["provenance_id"]),
        occurred_at=str(value["occurred_at"]),
        asset=str(value.get("asset", "")),
        amount=str(value.get("amount", "0")),
        risk_reason_codes=tuple(value.get("risk_reason_codes", ())),
        metadata=dict(value.get("metadata") or {}),
    )


def _revision_bytes(revision: TransactionEventRevision) -> bytes:
    return _json_bytes(
        {
            "event": revision.event.canonical_payload(),
            "revision": revision.revision,
            "status": revision.status,
            "memory_sequence": revision.memory_sequence,
            "reorged_by_block_hash": revision.reorged_by_block_hash,
        }
    )


def _revision_from_bytes(value: bytes) -> TransactionEventRevision:
    payload = _json_value(value, default={})
    return TransactionEventRevision(
        event=_event_from_dict(payload["event"]),
        revision=int(payload["revision"]),
        status=str(payload["status"]),
        memory_sequence=int(payload["memory_sequence"]),
        reorged_by_block_hash=str(payload.get("reorged_by_block_hash", "")),
    )


def _outbox_bytes(entry: ProjectionOutboxEntry) -> bytes:
    return _json_bytes(asdict(entry))


def _outbox_from_bytes(value: bytes) -> ProjectionOutboxEntry:
    return ProjectionOutboxEntry(**_json_value(value, default={}))


class BlockchainLongTermMemory:
    """Versioned blockchain memory with atomic outbox and aggregates."""

    def __init__(
        self, runner: TransactionRunner, *, max_events_per_transaction: int = 128
    ) -> None:
        if max_events_per_transaction < 1:
            raise ValueError("max_events_per_transaction must be positive")
        self._runner = runner
        self._max_events_per_transaction = max_events_per_transaction

    @staticmethod
    def _root(workspace_id: str) -> MemoryKey:
        return ("seocho", "blockchain_memory", _SCHEMA, workspace_token(workspace_id))

    @staticmethod
    def _block_digest(events: Sequence[TransactionEvent]) -> str:
        rows = sorted((event.event_id, event.payload_hash) for event in events)
        encoded = json.dumps(rows, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _validate_block(
        *,
        workspace_id: str,
        chain_id: str,
        block_height: int,
        block_hash: str,
        events: Sequence[TransactionEvent],
    ) -> None:
        if not workspace_id.strip() or not chain_id.strip() or not block_hash.strip():
            raise ValueError("workspace_id, chain_id, and block_hash are required")
        if block_height < 0:
            raise ValueError("block_height must be non-negative")
        identities: set[str] = set()
        for event in events:
            actual = (
                event.workspace_id,
                event.chain_id,
                event.block_height,
                event.block_hash,
            )
            expected = (workspace_id, chain_id, block_height, block_hash)
            if actual != expected:
                raise ValueError("event does not belong to the reconciled block")
            if event.event_id in identities:
                raise ValueError("block contains a duplicate event identity")
            identities.add(event.event_id)

    @staticmethod
    def _current_key(root: MemoryKey, event: TransactionEvent) -> MemoryKey:
        return root + ("current", event.chain_id, event.tx_hash, event.event_index)

    @staticmethod
    def _history_key(
        root: MemoryKey, event: TransactionEvent, revision: int
    ) -> MemoryKey:
        return root + (
            "history",
            event.chain_id,
            event.tx_hash,
            event.event_index,
            revision,
        )

    @staticmethod
    def _risk_key(root: MemoryKey, event: TransactionEvent) -> MemoryKey:
        return root + (
            "risk_aggregate",
            event.customer_ref,
            event.counterparty_ref,
        )

    @staticmethod
    def _adjust_risk(
        transaction: MemoryTransaction,
        root: MemoryKey,
        event: TransactionEvent,
        *,
        delta: int,
        sequence: int,
    ) -> None:
        if not event.risk_reason_codes:
            return
        key = BlockchainLongTermMemory._risk_key(root, event)
        current = _json_value(transaction.get(key), default={})
        count = max(0, int(current.get("flagged_event_count", 0)) + delta)
        transaction.set(
            key,
            _json_bytes(
                {
                    "customer_ref": event.customer_ref,
                    "counterparty_ref": event.counterparty_ref,
                    "flagged_event_count": count,
                    "last_sequence": sequence,
                }
            ),
        )

    def reconcile_block(
        self,
        *,
        workspace_id: str,
        chain_id: str,
        block_height: int,
        block_hash: str,
        events: Sequence[TransactionEvent],
    ) -> BlockIngestResult:
        """Atomically apply a bounded block observation or compensate a reorg."""

        materialized = tuple(events)
        if len(materialized) > self._max_events_per_transaction:
            raise ValueError(
                "block observation exceeds max_events_per_transaction; "
                "partition the ingestion workload"
            )
        self._validate_block(
            workspace_id=workspace_id,
            chain_id=chain_id,
            block_height=block_height,
            block_hash=block_hash,
            events=materialized,
        )
        digest = self._block_digest(materialized)
        root = self._root(workspace_id)

        def apply(transaction: MemoryTransaction) -> BlockIngestResult:
            sequence_key = root + ("sequence",)
            head_key = root + ("block_head", chain_id, block_height)
            receipt_key = root + ("block_receipt", chain_id, block_height, block_hash)
            head = _json_value(transaction.get(head_key), default={})
            receipt = _json_value(transaction.get(receipt_key), default={})

            if head.get("block_hash") == block_hash and receipt:
                if receipt.get("digest") != digest:
                    raise BlockReplayConflictError(
                        "same canonical block hash was replayed with different events"
                    )
                token = CausalToken.for_workspace(workspace_id, int(receipt["sequence"]))
                return BlockIngestResult(False, token, 0, 0, 0)

            sequence = int(_json_value(transaction.get(sequence_key), default=0)) + 1
            ordinal = 0
            orphaned_count = 0
            previous_hash = str(head.get("block_hash", ""))

            if previous_hash and previous_hash != block_hash:
                old_prefix = root + (
                    "block_event",
                    chain_id,
                    block_height,
                    previous_hash,
                )
                for _, descriptor_bytes in transaction.scan_prefix(old_prefix):
                    descriptor = _json_value(descriptor_bytes, default={})
                    current_key = root + (
                        "current",
                        chain_id,
                        str(descriptor["tx_hash"]),
                        int(descriptor["event_index"]),
                    )
                    current_bytes = transaction.get(current_key)
                    if current_bytes is None:
                        continue
                    current = _revision_from_bytes(current_bytes)
                    if current.status != "canonical" or current.event.block_hash != previous_hash:
                        continue
                    orphaned = replace(
                        current,
                        revision=current.revision + 1,
                        status="orphaned",
                        memory_sequence=sequence,
                        reorged_by_block_hash=block_hash,
                    )
                    encoded = _revision_bytes(orphaned)
                    transaction.set(current_key, encoded)
                    transaction.set(
                        self._history_key(root, orphaned.event, orphaned.revision), encoded
                    )
                    self._adjust_risk(
                        transaction, root, orphaned.event, delta=-1, sequence=sequence
                    )
                    outbox = ProjectionOutboxEntry(
                        workspace_token=workspace_token(workspace_id),
                        sequence=sequence,
                        ordinal=ordinal,
                        operation="retract",
                        event_id=orphaned.event.event_id,
                        event_revision=orphaned.revision,
                        chain_id=chain_id,
                        block_height=block_height,
                        block_hash=previous_hash,
                    )
                    transaction.set(
                        root + ("outbox", sequence, ordinal), _outbox_bytes(outbox)
                    )
                    ordinal += 1
                    orphaned_count += 1

            for event in materialized:
                current_key = self._current_key(root, event)
                current_bytes = transaction.get(current_key)
                previous_revision = (
                    _revision_from_bytes(current_bytes).revision if current_bytes else 0
                )
                canonical = TransactionEventRevision(
                    event=event,
                    revision=previous_revision + 1,
                    status="canonical",
                    memory_sequence=sequence,
                )
                encoded = _revision_bytes(canonical)
                transaction.set(current_key, encoded)
                transaction.set(
                    self._history_key(root, event, canonical.revision), encoded
                )
                transaction.set(
                    root
                    + (
                        "block_event",
                        chain_id,
                        block_height,
                        block_hash,
                        event.event_id,
                    ),
                    _json_bytes(
                        {"tx_hash": event.tx_hash, "event_index": event.event_index}
                    ),
                )
                self._adjust_risk(
                    transaction, root, event, delta=1, sequence=sequence
                )
                outbox = ProjectionOutboxEntry(
                    workspace_token=workspace_token(workspace_id),
                    sequence=sequence,
                    ordinal=ordinal,
                    operation="upsert",
                    event_id=event.event_id,
                    event_revision=canonical.revision,
                    chain_id=chain_id,
                    block_height=block_height,
                    block_hash=block_hash,
                )
                transaction.set(
                    root + ("outbox", sequence, ordinal), _outbox_bytes(outbox)
                )
                ordinal += 1

            transaction.set(sequence_key, _json_bytes(sequence))
            transaction.set(
                head_key,
                _json_bytes({"block_hash": block_hash, "sequence": sequence}),
            )
            transaction.set(
                receipt_key,
                _json_bytes({"digest": digest, "sequence": sequence}),
            )
            return BlockIngestResult(
                applied=True,
                causal_token=CausalToken.for_workspace(workspace_id, sequence),
                canonical_event_count=len(materialized),
                orphaned_event_count=orphaned_count,
                outbox_entry_count=ordinal,
            )

        result = self._runner.transact(apply)
        log_span(
            "memory.block.reconcile",
            output_data={
                "seocho.memory.applied": result.applied,
                "seocho.memory.sequence": result.causal_token.sequence,
                "seocho.memory.canonical_event_count": result.canonical_event_count,
                "seocho.memory.orphaned_event_count": result.orphaned_event_count,
                "seocho.memory.outbox_entry_count": result.outbox_entry_count,
            },
            metadata={
                "seocho.workspace.hash": workspace_token(workspace_id),
                "seocho.chain.id": chain_id,
                "seocho.block.height": block_height,
            },
            tags=["memory", "blockchain"],
        )
        return result

    def get_current_event(
        self,
        *,
        workspace_id: str,
        chain_id: str,
        tx_hash: str,
        event_index: int,
    ) -> TransactionEventRevision | None:
        root = self._root(workspace_id)
        key = root + ("current", chain_id, tx_hash, event_index)
        return self._runner.transact(
            lambda transaction: (
                _revision_from_bytes(value) if (value := transaction.get(key)) else None
            )
        )

    def event_history(
        self,
        *,
        workspace_id: str,
        chain_id: str,
        tx_hash: str,
        event_index: int,
    ) -> tuple[TransactionEventRevision, ...]:
        root = self._root(workspace_id)
        prefix = root + ("history", chain_id, tx_hash, event_index)
        return self._runner.transact(
            lambda transaction: tuple(
                _revision_from_bytes(value)
                for _, value in transaction.scan_prefix(prefix)
            )
        )

    def risk_aggregate(
        self,
        *,
        workspace_id: str,
        customer_ref: str,
        counterparty_ref: str,
    ) -> RiskAggregate:
        root = self._root(workspace_id)
        key = root + ("risk_aggregate", customer_ref, counterparty_ref)
        payload = self._runner.transact(
            lambda transaction: _json_value(transaction.get(key), default={})
        )
        return RiskAggregate(
            customer_ref=customer_ref,
            counterparty_ref=counterparty_ref,
            flagged_event_count=int(payload.get("flagged_event_count", 0)),
            last_sequence=int(payload.get("last_sequence", 0)),
        )

    def outbox_entries(
        self,
        *,
        workspace_id: str,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[ProjectionOutboxEntry, ...]:
        if after_sequence < 0 or limit < 1:
            raise ValueError("after_sequence must be non-negative and limit positive")
        root = self._root(workspace_id)
        prefix = root + ("outbox",)
        return self._runner.transact(
            lambda transaction: tuple(
                _outbox_from_bytes(value)
                for key, value in transaction.scan_prefix(prefix)
                if int(key[-2]) > after_sequence
            )[:limit]
        )

    def acknowledge_projection(
        self,
        *,
        workspace_id: str,
        projection: str,
        token: CausalToken,
    ) -> ProjectionStatus:
        token.assert_workspace(workspace_id)
        if not projection.strip():
            raise ValueError("projection is required")
        root = self._root(workspace_id)

        def acknowledge(transaction: MemoryTransaction) -> ProjectionStatus:
            latest = int(_json_value(transaction.get(root + ("sequence",)), default=0))
            if token.sequence > latest:
                raise ValueError("projection cannot acknowledge a future sequence")
            key = root + ("projection_watermark", projection)
            current = int(_json_value(transaction.get(key), default=0))
            applied = max(current, token.sequence)
            transaction.set(key, _json_bytes(applied))
            return ProjectionStatus(projection, applied, token.sequence, applied >= token.sequence)

        return self._runner.transact(acknowledge)

    def projection_status(
        self,
        *,
        workspace_id: str,
        projection: str,
        required: CausalToken,
    ) -> ProjectionStatus:
        required.assert_workspace(workspace_id)
        root = self._root(workspace_id)
        key = root + ("projection_watermark", projection)
        applied = self._runner.transact(
            lambda transaction: int(_json_value(transaction.get(key), default=0))
        )
        return ProjectionStatus(
            projection=projection,
            applied_sequence=applied,
            required_sequence=required.sequence,
            current=applied >= required.sequence,
        )
