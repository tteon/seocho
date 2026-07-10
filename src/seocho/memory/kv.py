"""Ordered transactional key-value runners used by long-term memory."""

from __future__ import annotations

import threading
from typing import Callable, Dict, Iterable, Protocol, Tuple, TypeVar


KeyPart = str | int
MemoryKey = Tuple[KeyPart, ...]
T = TypeVar("T")


class MemoryTransaction(Protocol):
    def get(self, key: MemoryKey) -> bytes | None:
        ...

    def set(self, key: MemoryKey, value: bytes) -> None:
        ...

    def scan_prefix(self, prefix: MemoryKey) -> Iterable[tuple[MemoryKey, bytes]]:
        ...


class TransactionRunner(Protocol):
    def transact(self, operation: Callable[[MemoryTransaction], T]) -> T:
        ...


class _InMemoryTransaction:
    def __init__(self, values: Dict[MemoryKey, bytes]) -> None:
        self._values = values

    def get(self, key: MemoryKey) -> bytes | None:
        return self._values.get(key)

    def set(self, key: MemoryKey, value: bytes) -> None:
        self._values[key] = bytes(value)

    def scan_prefix(self, prefix: MemoryKey) -> Iterable[tuple[MemoryKey, bytes]]:
        size = len(prefix)
        return tuple(
            (key, self._values[key])
            for key in sorted(self._values)
            if key[:size] == prefix
        )


class InMemoryTransactionRunner:
    """Serializable reference runner with rollback on exceptions."""

    def __init__(self) -> None:
        self._values: Dict[MemoryKey, bytes] = {}
        self._lock = threading.RLock()

    def transact(self, operation: Callable[[MemoryTransaction], T]) -> T:
        with self._lock:
            candidate = dict(self._values)
            result = operation(_InMemoryTransaction(candidate))
            self._values = candidate
            return result


class _FoundationDBTransaction:
    def __init__(self, transaction: object, tuple_module: object) -> None:
        self._transaction = transaction
        self._tuple = tuple_module

    def _pack(self, key: MemoryKey) -> bytes:
        return self._tuple.pack(key)  # type: ignore[attr-defined,no-any-return]

    def get(self, key: MemoryKey) -> bytes | None:
        future = self._transaction[self._pack(key)]  # type: ignore[index]
        value = future.wait()
        return None if value is None else bytes(value)

    def set(self, key: MemoryKey, value: bytes) -> None:
        self._transaction[self._pack(key)] = value  # type: ignore[index]

    def scan_prefix(self, prefix: MemoryKey) -> Iterable[tuple[MemoryKey, bytes]]:
        key_range = self._tuple.range(prefix)  # type: ignore[attr-defined]
        rows = self._transaction.get_range(  # type: ignore[attr-defined]
            key_range.start, key_range.stop
        )
        return tuple(
            (
                tuple(self._tuple.unpack(bytes(row.key))),  # type: ignore[attr-defined]
                bytes(row.value),
            )
            for row in rows
        )


class FoundationDBTransactionRunner:
    """Optional official FoundationDB Python-binding adapter.

    Construction is lazy so importing SEOCHO never requires the native FDB
    client. ``api_version`` is explicit because it must be compatible with the
    installed client and target cluster.
    """

    def __init__(self, database: object, *, fdb_module: object) -> None:
        self._database = database
        self._fdb = fdb_module

    @classmethod
    def connect(
        cls,
        *,
        api_version: int,
        cluster_file: str | None = None,
        tenant_name: bytes | None = None,
    ) -> "FoundationDBTransactionRunner":
        try:
            import fdb
            import fdb.tuple
        except ImportError as exc:
            raise ImportError(
                "FoundationDB runner requires the official fdb Python binding "
                "and matching native client"
            ) from exc
        fdb.api_version(api_version)
        database = fdb.open(cluster_file) if cluster_file else fdb.open()
        if tenant_name is not None:
            database = database.open_tenant(tenant_name)
        return cls(database, fdb_module=fdb)

    def transact(self, operation: Callable[[MemoryTransaction], T]) -> T:
        tuple_module = self._fdb.tuple  # type: ignore[attr-defined]

        @self._fdb.transactional  # type: ignore[attr-defined]
        def run(tr: object) -> T:
            return operation(_FoundationDBTransaction(tr, tuple_module))

        return run(self._database)
