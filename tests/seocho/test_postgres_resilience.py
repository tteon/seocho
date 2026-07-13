import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from seocho.memory.postgres_resilience import (
    AdmissionRejected,
    PostgresReadRouter,
    PostgresTarget,
    QueryDigestPolicy,
    RetryBudget,
    SchemaChangeGuard,
    SingleFlightCache,
    WorkloadAdmissionController,
    WorkloadTier,
)


def test_single_flight_coalesces_cache_miss_storm() -> None:
    cache: SingleFlightCache[int] = SingleFlightCache()
    calls = 0
    lock = threading.Lock()

    def loader() -> int:
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.02)
        return 42

    with ThreadPoolExecutor(max_workers=32) as executor:
        results = list(
            executor.map(
                lambda _: cache.get_or_load("same-key", loader, ttl_seconds=1),
                range(64),
            )
        )
    assert calls == 1
    assert {value for value, _ in results} == {42}
    assert {outcome for _, outcome in results} >= {"loaded", "coalesced"}


def test_workload_tiers_reject_without_cross_tier_starvation() -> None:
    controller = WorkloadAdmissionController(
        {WorkloadTier.CRITICAL: 1, WorkloadTier.BACKGROUND: 1}
    )
    entered = threading.Event()
    release = threading.Event()

    def background() -> None:
        entered.set()
        release.wait(1)

    thread = threading.Thread(
        target=lambda: controller.run(
            WorkloadTier.BACKGROUND, background, wait_seconds=0
        )
    )
    thread.start()
    entered.wait(1)
    with pytest.raises(AdmissionRejected):
        controller.run(WorkloadTier.BACKGROUND, lambda: None, wait_seconds=0)
    assert controller.run(WorkloadTier.CRITICAL, lambda: "ok", wait_seconds=0) == "ok"
    release.set()
    thread.join()


def test_replica_router_enforces_freshness_and_primary_fallback() -> None:
    targets = [
        PostgresTarget("primary", "primary", "kr", priority=1),
        PostgresTarget("local-stale", "replica", "kr", replay_lag_seconds=4),
        PostgresTarget("remote-fresh", "replica", "us", replay_lag_seconds=0.1),
    ]
    routed = PostgresReadRouter().choose(
        targets, client_region="kr", max_replay_lag_seconds=1
    )
    assert routed.target.target_id == "remote-fresh"
    assert routed.reason == "fresh_remote_replica"
    assert PostgresReadRouter().choose(
        targets, client_region="kr", max_replay_lag_seconds=0.01
    ).target.role == "primary"
    assert PostgresReadRouter().choose(
        targets, client_region="kr", require_primary=True
    ).reason == "strict_read"


def test_retry_budget_is_bounded_and_jittered() -> None:
    budget = RetryBudget(max_attempts=3, base_delay_seconds=0.01, max_delay_seconds=0.02)
    assert 0 <= budget.delay(1) <= 0.01
    assert 0 <= budget.delay(2) <= 0.02
    with pytest.raises(ValueError):
        budget.delay(3)


def test_query_digest_policy_blocks_query_shape_without_retaining_literals() -> None:
    policy = QueryDigestPolicy()
    first = "SELECT * FROM agent_memory_revisions WHERE workspace_id='tenant-a' AND sequence=42"
    second = "select * from agent_memory_revisions where workspace_id='tenant-b' and sequence=99"
    digest = policy.block(first)
    assert len(digest) == 24
    assert not policy.allows(second)


def test_schema_guard_allows_online_index_and_blocks_rewrite_risk() -> None:
    guard = SchemaChangeGuard(statement_timeout_ms=5000)
    assert guard.inspect(
        "CREATE INDEX CONCURRENTLY memory_seq_idx ON agent_memory_revisions(sequence)"
    ).allowed
    denied = guard.inspect(
        "ALTER TABLE agent_memory_revisions ALTER COLUMN sequence TYPE numeric"
    )
    assert not denied.allowed
    assert denied.reason == "column_type_rewrite_risk"
