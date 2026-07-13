"""Application-side PostgreSQL overload and read-routing controls."""

from __future__ import annotations

import random
import hashlib
import re
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Generic, TypeVar

from ..metrics import ProductionMetrics, get_metrics


T = TypeVar("T")


class WorkloadTier(str, Enum):
    CRITICAL = "critical"
    INTERACTIVE = "interactive"
    BACKGROUND = "background"


class AdmissionRejected(RuntimeError):
    pass


class WorkloadAdmissionController:
    """Independent concurrency budgets prevent noisy-neighbor starvation."""

    def __init__(
        self,
        limits: dict[WorkloadTier, int],
        *,
        metrics: ProductionMetrics | None = None,
    ) -> None:
        if not limits or any(limit < 1 for limit in limits.values()):
            raise ValueError("every workload tier requires a positive limit")
        self._semaphores = {
            tier: threading.BoundedSemaphore(limit) for tier, limit in limits.items()
        }
        self._metrics = metrics or get_metrics()

    def run(self, tier: WorkloadTier, operation: Callable[[], T], *, wait_seconds: float) -> T:
        started = time.perf_counter()
        semaphore = self._semaphores.get(tier)
        if semaphore is None:
            self._metrics.add(
                "seocho.postgres.admission.count", 1, {"tier": tier.value, "outcome": "unconfigured"}
            )
            raise AdmissionRejected(f"unconfigured workload tier: {tier.value}")
        if not semaphore.acquire(timeout=max(0.0, wait_seconds)):
            self._metrics.add(
                "seocho.postgres.admission.count", 1, {"tier": tier.value, "outcome": "rejected"}
            )
            self._metrics.record(
                "seocho.postgres.admission.wait",
                time.perf_counter() - started,
                {"tier": tier.value, "outcome": "rejected"},
            )
            raise AdmissionRejected(f"{tier.value} workload budget exhausted")
        try:
            self._metrics.add(
                "seocho.postgres.admission.count", 1, {"tier": tier.value, "outcome": "admitted"}
            )
            self._metrics.record(
                "seocho.postgres.admission.wait",
                time.perf_counter() - started,
                {"tier": tier.value, "outcome": "admitted"},
            )
            return operation()
        finally:
            semaphore.release()


@dataclass(slots=True)
class _CacheEntry(Generic[T]):
    value: T
    expires_at: float


class SingleFlightCache(Generic[T]):
    """TTL cache where one leader fills a missing key and followers wait."""

    def __init__(self, *, metrics: ProductionMetrics | None = None) -> None:
        self._values: dict[str, _CacheEntry[T]] = {}
        self._inflight: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._metrics = metrics or get_metrics()

    def get_or_load(
        self,
        key: str,
        loader: Callable[[], T],
        *,
        ttl_seconds: float,
        wait_seconds: float = 5.0,
    ) -> tuple[T, str]:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        leader = False
        with self._lock:
            entry = self._values.get(key)
            if entry is not None and entry.expires_at > time.monotonic():
                self._metrics.add("seocho.postgres.cache.request.count", 1, {"outcome": "hit"})
                return entry.value, "hit"
            event = self._inflight.get(key)
            if event is None:
                event = threading.Event()
                self._inflight[key] = event
                leader = True
        if not leader:
            if not event.wait(wait_seconds):
                raise TimeoutError("single-flight cache fill timed out")
            with self._lock:
                entry = self._values.get(key)
                if entry is None or entry.expires_at <= time.monotonic():
                    raise RuntimeError("single-flight leader did not populate cache")
                self._metrics.add("seocho.postgres.cache.request.count", 1, {"outcome": "coalesced"})
                return entry.value, "coalesced"
        try:
            value = loader()
            with self._lock:
                self._values[key] = _CacheEntry(
                    value=value, expires_at=time.monotonic() + ttl_seconds
                )
            self._metrics.add("seocho.postgres.cache.request.count", 1, {"outcome": "loaded"})
            return value, "loaded"
        finally:
            with self._lock:
                self._inflight.pop(key, None)
                event.set()


@dataclass(frozen=True, slots=True)
class PostgresTarget:
    target_id: str
    role: str
    region: str
    available: bool = True
    replay_lag_seconds: float = 0.0
    priority: int = 0


@dataclass(frozen=True, slots=True)
class RouteDecision:
    target: PostgresTarget
    reason: str


class PostgresReadRouter:
    """Route bounded-staleness reads to replicas and strict reads to primary."""

    def __init__(self, *, metrics: ProductionMetrics | None = None) -> None:
        self._metrics = metrics or get_metrics()

    def choose(
        self,
        targets: list[PostgresTarget],
        *,
        client_region: str,
        require_primary: bool = False,
        max_replay_lag_seconds: float = 1.0,
    ) -> RouteDecision:
        available = [target for target in targets if target.available]
        primaries = [target for target in available if target.role == "primary"]
        if require_primary:
            if not primaries:
                raise LookupError("no available primary")
            decision = RouteDecision(max(primaries, key=lambda item: item.priority), "strict_read")
            self._record(decision)
            return decision
        replicas = [
            target
            for target in available
            if target.role == "replica"
            and target.replay_lag_seconds <= max_replay_lag_seconds
        ]
        if replicas:
            replicas.sort(
                key=lambda item: (
                    item.region != client_region,
                    item.replay_lag_seconds,
                    -item.priority,
                    item.target_id,
                )
            )
            reason = (
                "fresh_local_replica"
                if replicas[0].region == client_region
                else "fresh_remote_replica"
            )
            decision = RouteDecision(replicas[0], reason)
            self._record(decision)
            return decision
        if primaries:
            decision = RouteDecision(max(primaries, key=lambda item: item.priority), "replica_fallback")
            self._record(decision)
            return decision
        raise LookupError("no PostgreSQL target satisfies freshness requirements")

    def _record(self, decision: RouteDecision) -> None:
        self._metrics.add(
            "seocho.postgres.route.count",
            1,
            {"role": decision.target.role, "reason": decision.reason},
        )


@dataclass(slots=True)
class RetryBudget:
    max_attempts: int
    base_delay_seconds: float = 0.05
    max_delay_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")

    def delay(self, attempt: int) -> float:
        if attempt < 1 or attempt >= self.max_attempts:
            raise ValueError("attempt must describe a retry before max_attempts")
        ceiling = min(self.max_delay_seconds, self.base_delay_seconds * 2 ** (attempt - 1))
        return random.uniform(0.0, ceiling)


class QueryDigestPolicy:
    """Emergency load shedding by opaque normalized-query digest."""

    _LITERALS = re.compile(r"'(?:''|[^'])*'|\b\d+(?:\.\d+)?\b")
    _SPACE = re.compile(r"\s+")

    def __init__(self, blocked_digests: set[str] | None = None) -> None:
        self._blocked = set(blocked_digests or ())

    @classmethod
    def digest(cls, statement: str) -> str:
        normalized = cls._SPACE.sub(
            " ", cls._LITERALS.sub("?", statement.strip().lower())
        )
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]

    def block(self, statement: str) -> str:
        digest = self.digest(statement)
        self._blocked.add(digest)
        return digest

    def allows(self, statement: str) -> bool:
        return self.digest(statement) not in self._blocked


@dataclass(frozen=True, slots=True)
class SchemaChangeDecision:
    allowed: bool
    reason: str
    statement_timeout_ms: int


class SchemaChangeGuard:
    """Fail closed on table-rewrite-prone online DDL."""

    def __init__(self, *, statement_timeout_ms: int = 5000) -> None:
        if statement_timeout_ms < 1:
            raise ValueError("statement_timeout_ms must be positive")
        self.statement_timeout_ms = statement_timeout_ms

    def inspect(self, statement: str) -> SchemaChangeDecision:
        sql = " ".join(statement.strip().upper().split())
        if re.match(r"^CREATE (UNIQUE )?INDEX CONCURRENTLY\b", sql):
            return SchemaChangeDecision(True, "concurrent_index", self.statement_timeout_ms)
        if re.match(r"^DROP INDEX CONCURRENTLY\b", sql):
            return SchemaChangeDecision(True, "concurrent_index_drop", self.statement_timeout_ms)
        if re.match(r"^ALTER TABLE\b.*\bADD COLUMN\b", sql) and " DEFAULT " not in sql:
            return SchemaChangeDecision(True, "metadata_only_column", self.statement_timeout_ms)
        if " ALTER COLUMN " in sql and " TYPE " in sql:
            return SchemaChangeDecision(False, "column_type_rewrite_risk", self.statement_timeout_ms)
        if re.match(r"^(CREATE|DROP) TABLE\b", sql):
            return SchemaChangeDecision(False, "table_lifecycle_requires_review", self.statement_timeout_ms)
        return SchemaChangeDecision(False, "ddl_not_allowlisted", self.statement_timeout_ms)
