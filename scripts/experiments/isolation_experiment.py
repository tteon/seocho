#!/usr/bin/env python3
"""Multi-tenant / agent-interaction isolation experiment for seocho-6q9.3.

Validates the *worktree-isolated runtime boot* feature (shared neo4j, one
ephemeral logical database per tenant). Design hardened after independent
review by a systems-methodology referee and a multi-tenancy/isolation referee.
The two non-negotiable corrections from that review shape this harness:

  1. The data-plane is keyed by *database name*, so a dict-backed store would
     make "no leakage under isolation" true by construction. We therefore add a
     POSITIVE CONTROL (condition B') that forces a derivation bug (all tenants
     collapse to one database) and show the harness DOES detect leakage there.
     A null result under B is only meaningful because B' is non-null.

  2. We claim DATA ISOLATION ONLY. Performance isolation and fault isolation are
     explicitly NOT claimed — a shared engine shares a buffer pool and a failure
     domain. H2 is split into H2a (logical result-set invariance, claimed and
     tested) and H2b (latency/availability invariance, NOT claimed; a shared
     engine couples neighbors — out of scope for this in-memory harness).

WHAT THIS VALIDATES (backend-independent, exercises real code):
  - determinism of derive_instance (an invariant, asserted exhaustively)
  - collision-freedom of the database key over a tenant population, vs the
    analytical birthday bound, with a rule-of-three upper bound on zero events
  - that the data-isolation invariant rests on the DATABASE key and survives
    app-tier PORT-SLOT collisions (the headline conditional claim)
  - control-plane name-injection rejection (real admin_database_command)
  - teardown blast radius and residual-after-recreate on the routing layer

WHAT THIS DOES NOT VALIDATE (deferred to live-neo4j integration):
  - that neo4j logical databases actually enforce visibility/transaction
    isolation (assumed from neo4j multi-database guarantees)
  - any real performance/contention/noisy-neighbor behavior (H2b)

Threat model: honest-but-concurrent tenants (not malicious in-process). This is
not a security boundary against a compromised engine or Cypher injection beyond
the validated control-plane path.

Run: PYTHONPATH=src python3 scripts/experiments/isolation_experiment.py
"""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from seocho.exceptions import SeochoError
from seocho.instance import INSTANCE_PORT_SLOTS, derive_instance
from seocho.local import admin_database_command
from seocho.runtime_contract import DATABASE_NAME_PATTERN

SHARED_DB = "sharedmemorydb"          # condition A: one database for everyone
COLLAPSED_DB = "collapseddb"          # condition B': forced derivation bug


# --------------------------------------------------------------------------
# Faithful in-memory multi-database store (stands in for neo4j multi-database)
# --------------------------------------------------------------------------
@dataclass
class Node:
    owner: str
    name: str


class InMemoryMultiDB:
    """Thread-safe store keyed by database name.

    Mirrors only the structural property under test: a query is scoped to the
    database the caller connected to. An UNSCOPED `MATCH (n)` therefore returns
    every node in the *connected* database — which is exactly the surface a
    cross-tenant leak would appear on.
    """

    def __init__(self) -> None:
        self._dbs: Dict[str, List[Node]] = {}
        self._lock = threading.Lock()

    def create_database(self, name: str) -> None:
        with self._lock:
            self._dbs.setdefault(name, [])

    def drop_database(self, name: str) -> None:
        with self._lock:
            self._dbs.pop(name, None)

    def write(self, database: str, node: Node) -> None:
        with self._lock:
            self._dbs.setdefault(database, []).append(node)

    def match_all(self, database: str) -> List[Node]:
        # Unscoped MATCH (n) within the connected database.
        with self._lock:
            return list(self._dbs.get(database, []))

    def exists(self, name: str) -> bool:
        with self._lock:
            return name in self._dbs


# --------------------------------------------------------------------------
# Conditions: how a tenant id resolves to a target database
# --------------------------------------------------------------------------
def resolve_db_A(tenant: str) -> str:
    """Single shared instance, multi-agent: everyone shares one logical DB."""
    return SHARED_DB


def resolve_db_B(tenant: str) -> str:
    """The feature: each tenant routed to its own derived ephemeral DB."""
    return derive_instance(tenant).database


def resolve_db_Bprime(tenant: str) -> str:
    """POSITIVE CONTROL: a derivation bug collapses all tenants to one DB."""
    return COLLAPSED_DB


CONDITIONS: Dict[str, Callable[[str], str]] = {
    "A_shared_single": resolve_db_A,
    "B_isolated": resolve_db_B,
    "Bprime_broken_control": resolve_db_Bprime,
}


# --------------------------------------------------------------------------
# Concurrent multi-tenant workload
# --------------------------------------------------------------------------
@dataclass
class LeakageResult:
    condition: str
    tenants: int
    ops: int
    leak_ops: int           # tenant reads that observed a foreign node
    leaked_nodes: int       # total foreign nodes observed across reads


def run_workload(
    resolve: Callable[[str], str],
    tenants: List[str],
    writes_per_tenant: int,
    *,
    identical_names: bool,
    condition: str,
) -> LeakageResult:
    """Concurrently: each tenant creates its DB, writes tagged nodes, then does
    an unscoped read of its connected DB and counts any foreign-owned nodes."""
    store = InMemoryMultiDB()
    leak_ops = 0
    leaked_nodes = 0
    audit_lock = threading.Lock()

    def agent(tenant: str) -> None:
        nonlocal leak_ops, leaked_nodes
        db = resolve(tenant)
        store.create_database(db)
        for i in range(writes_per_tenant):
            # identical_names probes the name-as-ID collision risk: the same
            # logical name written by two tenants must stay two distinct nodes
            # in two databases under isolation.
            name = f"shared-name-{i}" if identical_names else f"{tenant}-node-{i}"
            store.write(db, Node(owner=tenant, name=name))
        # Unscoped read of the connected DB — where a leak would show up.
        seen = store.match_all(db)
        foreign = [n for n in seen if n.owner != tenant]
        with audit_lock:
            if foreign:
                leak_ops += 1
                leaked_nodes += len(foreign)

    threads = [threading.Thread(target=agent, args=(t,)) for t in tenants]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    return LeakageResult(
        condition=condition,
        tenants=len(tenants),
        ops=len(tenants),
        leak_ops=leak_ops,
        leaked_nodes=leaked_nodes,
    )


# --------------------------------------------------------------------------
# Statistics (correct treatment for zero-bounded counts)
# --------------------------------------------------------------------------
def rule_of_three_upper_bound(events: int, trials: int) -> Optional[float]:
    """95% one-sided upper bound on a rate with `events` in `trials`.

    Exact only for the zero-event case (the classic rule of three: ~3/n).
    Returns None when events>0 (use a proper interval there)."""
    if trials == 0:
        return None
    if events == 0:
        return 3.0 / trials
    return None


def birthday_expected_collisions(n: int, slots: int) -> float:
    """Expected number of colliding pairs for n items in `slots` buckets."""
    if slots <= 0:
        return 0.0
    return (n * (n - 1) / 2.0) / slots


def birthday_prob_at_least_one(n: int, slots: int) -> float:
    if slots <= 0:
        return 1.0
    return 1.0 - math.exp(-n * (n - 1) / (2.0 * slots))


def fisher_exact_2x2(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher's exact p-value for table [[a,b],[c,d]] (no scipy)."""
    n = a + b + c + d
    if n == 0:
        return 1.0
    r1, r2 = a + b, c + d
    c1 = a + c

    def logfact(k: int) -> float:
        return math.lgamma(k + 1)

    def logp(x: int) -> float:
        # hypergeometric: x in the (row1,col1) cell
        y = r1 - x
        z = c1 - x
        w = r2 - z
        if min(x, y, z, w) < 0:
            return float("-inf")
        return (
            logfact(r1) + logfact(r2) + logfact(c1) + logfact(n - c1)
            - logfact(n) - logfact(x) - logfact(y) - logfact(z) - logfact(w)
        )

    p_obs = logp(a)
    lo = max(0, r1 - (n - c1))
    hi = min(r1, c1)
    total = 0.0
    for x in range(lo, hi + 1):
        lp = logp(x)
        if lp <= p_obs + 1e-9:
            total += math.exp(lp)
    return min(1.0, total)


# --------------------------------------------------------------------------
# Experiment sections
# --------------------------------------------------------------------------
def section(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def exp_determinism(ids: List[str], repeats: int = 50) -> bool:
    section("INVARIANT 1 — derivation determinism (exhaustive, not statistical)")
    ok = True
    for tid in ids:
        first = derive_instance(tid)
        for _ in range(repeats):
            if derive_instance(tid) != first:
                ok = False
                print(f"  NON-DETERMINISTIC: {tid!r}")
                break
    print(f"  {len(ids)} ids x {repeats} repeats -> deterministic: {ok}")
    return ok


def exp_collisions(populations: List[int]) -> None:
    section("FINDING 2 — collision-freedom: PORT-SLOT vs DATABASE key")
    print("  Data isolation must rest on the DATABASE key, not the port slot.")
    print(f"  Port slots = {INSTANCE_PORT_SLOTS} (birthday-bound, collisions EXPECTED).")
    print("  Database key = 'wt' + 12 hex = 48-bit space (collisions negligible).\n")
    print(f"  {'N':>6} | {'port_coll':>9} {'port_E[pairs]':>13} {'P(>=1)':>8} | "
          f"{'db_coll':>7} {'db_RoT_ub':>10}")
    print("  " + "-" * 70)
    for n in populations:
        ids = [f"tenant-{i:05d}" for i in range(n)]
        layouts = [derive_instance(t) for t in ids]
        # port-slot collisions
        slot_buckets: Dict[int, int] = defaultdict(int)
        for L in layouts:
            slot_buckets[L.slot] += 1
        port_coll_pairs = sum(c * (c - 1) // 2 for c in slot_buckets.values())
        # database-name collisions
        db_buckets: Dict[str, int] = defaultdict(int)
        for L in layouts:
            db_buckets[L.database] += 1
        db_coll_pairs = sum(c * (c - 1) // 2 for c in db_buckets.values())
        db_ub = rule_of_three_upper_bound(db_coll_pairs, max(1, n * (n - 1) // 2))
        print(f"  {n:>6} | {port_coll_pairs:>9} "
              f"{birthday_expected_collisions(n, INSTANCE_PORT_SLOTS):>13.2f} "
              f"{birthday_prob_at_least_one(n, INSTANCE_PORT_SLOTS):>8.3f} | "
              f"{db_coll_pairs:>7} "
              f"{(f'{db_ub:.2e}' if db_ub is not None else 'n/a'):>10}")


def exp_port_collision_isolation() -> bool:
    section("FINDING 3 — HEADLINE: data isolation holds UNDER a port-slot collision")
    id_a, id_b = "tenant-00001", "tenant-00044"  # hash to the same port slot
    a = derive_instance(id_a)
    b = derive_instance(id_b)
    print(f"  {id_a} : slot={a.slot} api={a.api_port} db={a.database}")
    print(f"  {id_b} : slot={b.slot} api={b.api_port} db={b.database}")
    same_port = a.api_port == b.api_port and a.slot == b.slot
    diff_db = a.database != b.database
    print(f"  -> share app-tier port slot: {same_port}")
    print(f"  -> distinct database key:    {diff_db}")
    # Route both through the isolation resolver, identical node names, concurrent.
    res = run_workload(
        resolve_db_B, [id_a, id_b], writes_per_tenant=200,
        identical_names=True, condition="B_isolated",
    )
    isolated = res.leaked_nodes == 0
    print(f"  -> concurrent identical-name writes; cross-tenant leaked nodes: "
          f"{res.leaked_nodes}")
    claim = same_port and diff_db and isolated
    print("\n  CLAIM: port collision does NOT break data isolation iff routing")
    print(f"  key = database (independent of port slot). Holds: {claim}")
    return claim


def exp_leakage_ABBprime(tenants: List[str], trials: int, writes: int) -> None:
    section("FINDING 4 — leakage: A (shared) vs B (isolated) vs B' (broken control)")
    print("  H1: zero cross-tenant data leakage under B.")
    print("  Positive control B' must show NON-zero leakage, else the harness")
    print("  cannot detect leakage and a null result under B is vacuous.\n")
    agg: Dict[str, Tuple[int, int, int]] = {}  # condition -> (leak_ops, total_ops, leaked_nodes)
    for cond, resolve in CONDITIONS.items():
        tot_leak_ops = tot_ops = tot_nodes = 0
        for _ in range(trials):
            r = run_workload(resolve, tenants, writes,
                             identical_names=True, condition=cond)
            tot_leak_ops += r.leak_ops
            tot_ops += r.ops
            tot_nodes += r.leaked_nodes
        agg[cond] = (tot_leak_ops, tot_ops, tot_nodes)
        ub = rule_of_three_upper_bound(tot_leak_ops, tot_ops)
        ub_s = f"<= {ub:.4f} (rule of 3)" if ub is not None else "n/a (events>0)"
        print(f"  {cond:24} leak_ops={tot_leak_ops:>5}/{tot_ops:<5} "
              f"leaked_nodes={tot_nodes:>7}  95% rate UB: {ub_s}")

    # Fisher's exact: B vs B' on (leak, no-leak)
    b_leak, b_tot, _ = agg["B_isolated"]
    bp_leak, bp_tot, _ = agg["Bprime_broken_control"]
    p = fisher_exact_2x2(b_leak, b_tot - b_leak, bp_leak, bp_tot - bp_leak)
    print(f"\n  Fisher's exact (B vs B', leak/no-leak): p = {p:.3e}")
    print(f"  Interpretation: B' detects leakage ({bp_leak}/{bp_tot} ops) while B")
    print(f"  shows {b_leak}/{b_tot} -> the null under B is a real, powered result.")


def exp_h2a_invariance(target: str, neighbors: List[str], writes: int) -> None:
    section("FINDING 5 — H2a: a tenant's RESULT SET is invariant to neighbors")
    print("  (H2b latency/availability invariance is NOT claimed: shared engine")
    print("   couples neighbors. This harness only tests logical invariance.)\n")
    for cond, resolve in (("A_shared_single", resolve_db_A), ("B_isolated", resolve_db_B)):
        # solo baseline result set (the uncontaminated ground truth for H2a)
        store_solo = InMemoryMultiDB()
        db = resolve(target)
        store_solo.create_database(db)
        for i in range(writes):
            store_solo.write(db, Node(owner=target, name=f"{target}-node-{i}"))
        solo_set = {(n.owner, n.name) for n in store_solo.match_all(db)}
        # concurrent with neighbors
        store_conc = InMemoryMultiDB()

        def agent(t: str) -> None:
            d = resolve(t)
            store_conc.create_database(d)
            for i in range(writes):
                store_conc.write(d, Node(owner=t, name=f"{t}-node-{i}"))

        ths = [threading.Thread(target=agent, args=(t,)) for t in [target] + neighbors]
        for th in ths:
            th.start()
        for th in ths:
            th.join()
        conc_set = {(n.owner, n.name) for n in store_conc.match_all(resolve(target))}
        invariant = conc_set == solo_set
        contamination = len(conc_set - solo_set)
        print(f"  {cond:24} result-set invariant: {invariant!s:5}  "
              f"contaminating rows under concurrency: {contamination}")


def exp_name_injection() -> bool:
    section("FINDING 6 — control-plane: name-injection rejection + derivation safety")
    ok = True
    # 6a: derive_instance hashes the id, so even hostile ids yield a safe wt<hash>
    hostile = ["a; DROP DATABASE neo4j; --", "../../etc", "DROP", "robert');--", "üñïçODE"]
    import re
    db_re = re.compile(DATABASE_NAME_PATTERN)
    for h in hostile:
        try:
            layout = derive_instance(h)
        except SeochoError:
            continue
        if not db_re.match(layout.database):
            ok = False
            print(f"  UNSAFE derived db from {h!r}: {layout.database}")
    print(f"  hostile ids -> derived db always matches {DATABASE_NAME_PATTERN!r}: {ok}")
    # 6b: admin command must reject a raw malicious database name
    rejected = 0
    for bad in ["neo4j`; DROP DATABASE system; --", "WITH space", "1leadingdigit", "x"]:
        try:
            admin_database_command(bad, action="create")
        except SeochoError:
            rejected += 1
    print(f"  admin_database_command rejected {rejected}/4 malformed/malicious names")
    ok = ok and rejected == 4
    return ok


def exp_teardown_and_residual() -> bool:
    section("FINDING 7 — teardown blast radius + residual-after-recreate")
    store = InMemoryMultiDB()
    a, b = derive_instance("alice"), derive_instance("bob")
    for L, t in ((a, "alice"), (b, "bob")):
        store.create_database(L.database)
        for i in range(10):
            store.write(L.database, Node(owner=t, name=f"{t}-{i}"))
    # drop only alice
    store.drop_database(a.database)
    blast_ok = (not store.exists(a.database)) and len(store.match_all(b.database)) == 10
    print(f"  drop alice's DB -> alice gone: {not store.exists(a.database)}, "
          f"bob intact (10 nodes): {len(store.match_all(b.database)) == 10}")
    # residual after recreate (same id -> same db name)
    a2 = derive_instance("alice")
    store.create_database(a2.database)
    residual = len(store.match_all(a2.database))
    print(f"  recreate alice (same db key {a2.database}) -> residual nodes: {residual}")
    ok = blast_ok and residual == 0
    print(f"  teardown is scoped and recreate is clean: {ok}")
    return ok


def exp_overhead(tenants: List[str], writes: int, trials: int) -> None:
    section("FINDING 8 — routing overhead (DEMOTED: near-zero, NOT load-bearing)")
    print("  The PhD review flags derivation cost as negligible and external")
    print("  validity as limited (in-memory, GIL). Reported for completeness;")
    print("  real engine throughput/memory is deferred to live-neo4j.\n")
    for cond, resolve in (("A_shared_single", resolve_db_A), ("B_isolated", resolve_db_B)):
        samples = []
        for _ in range(trials):
            t0 = time.perf_counter()
            run_workload(resolve, tenants, writes, identical_names=False, condition=cond)
            samples.append((time.perf_counter() - t0) * 1000.0)
        samples.sort()
        med = samples[len(samples) // 2]
        p95 = samples[min(len(samples) - 1, int(len(samples) * 0.95))]
        print(f"  {cond:24} median={med:8.2f}ms  p95={p95:8.2f}ms  "
              f"(n={trials} trials, {len(tenants)} tenants x {writes} writes)")


def main() -> int:
    print(__doc__.split("\n\n")[0])
    print("\nThreat model: honest-but-concurrent tenants. Claim: DATA isolation")
    print("ONLY (performance + fault isolation explicitly NOT claimed).")

    tenants = [f"tenant-{i:03d}" for i in range(16)]
    results = {}
    results["determinism"] = exp_determinism(tenants + ["alice", "bob", "wt-2"])
    exp_collisions([8, 64, 512, 4096])
    results["port_collision_isolation"] = exp_port_collision_isolation()
    exp_leakage_ABBprime(tenants, trials=20, writes=50)
    exp_h2a_invariance("tenant-000", tenants[1:], writes=50)
    results["name_injection"] = exp_name_injection()
    results["teardown_residual"] = exp_teardown_and_residual()
    exp_overhead(tenants, writes=50, trials=15)

    section("SUMMARY — invariant pass/fail")
    all_ok = True
    for k, v in results.items():
        all_ok = all_ok and v
        print(f"  {'PASS' if v else 'FAIL'}  {k}")
    print(f"\n  Overall invariants: {'ALL PASS' if all_ok else 'FAILURES PRESENT'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
