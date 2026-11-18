from __future__ import annotations

from seocho.ontology.active_pointer import ActiveOntologyPointer
from seocho.ontology import NodeDef, Ontology, Property, build_rdf_ontology_bundle
from seocho.ontology.lifecycle import OntologyLifecycleStore, load_agent_profile, slice_agent_profile


def _manifest(bundle, package: str = "demo", version: str = "1.0.0") -> None:
    import hashlib
    import json

    bundle.mkdir()
    for name, content in {"ontology.jsonld": "{}", "ontology.ttl": "", "shapes.ttl": ""}.items():
        (bundle / name).write_text(content, encoding="utf-8")
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in bundle.iterdir()}
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    (bundle / "manifest.json").write_text(json.dumps({"ontology": {"package_id": package, "version": version}, "files": files, "bundle_sha256": digest}), encoding="utf-8")


def test_lifecycle_activate_and_exclusive_lease(tmp_path):
    bundle = tmp_path / "bundle"; _manifest(bundle)
    store = OntologyLifecycleStore(tmp_path / "state.sqlite")
    ok, active = store.activate("ws", "demo", bundle, fencing_token=1)
    assert ok and active and active.epoch == 0
    lease = store.acquire("ws", "demo", purpose="projection", owner="pid:1", ttl_seconds=30)
    assert store.status("ws", "demo")["live_leases"][0]["lease_id"] == lease.lease_id
    try:
        store.acquire("ws", "demo", purpose="projection", owner="pid:2", ttl_seconds=30)
    except ValueError as exc:
        assert "live lease" in str(exc)
    else:
        raise AssertionError("exclusive lease unexpectedly acquired")
    assert store.release(lease.lease_id, owner="pid:1")


def test_activation_requires_pointer_cas_and_valid_manifest(tmp_path):
    bundle = tmp_path / "bundle"; _manifest(bundle)
    store = OntologyLifecycleStore(tmp_path / "state.sqlite")
    assert store.activate("ws", "demo", bundle, fencing_token=1)[0]
    assert not store.activate("ws", "demo", bundle, fencing_token=2)[0]
    assert store.activate("ws", "demo", bundle, fencing_token=2, expected=(0, 0))[0]
    assert ActiveOntologyPointer(tmp_path / "state.sqlite").read("ws", "demo").epoch == 1


def test_admission_is_minimal_and_requires_a_live_lease(tmp_path):
    bundle = tmp_path / "bundle"; _manifest(bundle)
    store = OntologyLifecycleStore(tmp_path / "state.sqlite")
    assert store.activate("ws", "demo", bundle, fencing_token=1)[0]
    lease = store.acquire("ws", "demo", purpose="projection", owner="pid:1", ttl_seconds=30)
    assert store.admission(lease.lease_id) == {
        "lease_id": lease.lease_id,
        "fingerprint": lease.fingerprint,
        "generation": 0,
        "epoch": 0,
        "fencing_token": lease.fencing_token,
    }


def test_verified_profile_and_jit_slice_are_bounded(tmp_path):
    ontology = Ontology(name="people", namespace="https://example.test/people#", version="1.0.0", nodes={"Person": NodeDef(properties={"name": Property(str, required=True)})})
    bundle = build_rdf_ontology_bundle(ontology, tmp_path / "bundle")
    profile = load_agent_profile(bundle.directory, "query")
    assert profile["purpose"] == "query"
    sliced = slice_agent_profile(bundle.directory, "query", ["Person"], max_chars=4000)
    assert sliced["canonical_bundle_sha256"] == bundle.digest
    assert "Person" in sliced["allowed_node_labels"]


# --- sr-xsu: durable monotonic lease fencing tokens -------------------------


def _active_store(tmp_path, *, pointer_token: int = 5):
    bundle = tmp_path / "bundle"; _manifest(bundle)
    store = OntologyLifecycleStore(tmp_path / "state.sqlite")
    assert store.activate("ws", "demo", bundle, fencing_token=pointer_token)[0]
    return store


def test_released_lease_tokens_are_never_reissued(tmp_path):
    # Reviewer's sequence: pointer P; query lease takes P+1; projection lease A
    # takes P+2; both released; the next lease must be > P+2 (not P+1 again).
    p = 5
    store = _active_store(tmp_path, pointer_token=p)
    query = store.acquire("ws", "demo", purpose="query", owner="q", ttl_seconds=30)
    proj_a = store.acquire("ws", "demo", purpose="projection", owner="a", ttl_seconds=30)
    assert (query.fencing_token, proj_a.fencing_token) == (p + 1, p + 2)
    assert store.release(query.lease_id, owner="q")
    assert store.release(proj_a.lease_id, owner="a")
    proj_b = store.acquire("ws", "demo", purpose="projection", owner="b", ttl_seconds=30)
    assert proj_b.fencing_token > p + 2
    # And a release of only the newest lease must not let its token come back.
    assert store.release(proj_b.lease_id, owner="b")
    again = store.acquire("ws", "demo", purpose="query", owner="q2", ttl_seconds=30)
    assert again.fencing_token > proj_b.fencing_token


def test_high_water_survives_reopen_and_is_scoped_per_package(tmp_path):
    store = _active_store(tmp_path, pointer_token=1)
    lease = store.acquire("ws", "demo", purpose="projection", owner="a", ttl_seconds=30)
    assert store.release(lease.lease_id, owner="a")
    reopened = OntologyLifecycleStore(tmp_path / "state.sqlite")
    nxt = reopened.acquire("ws", "demo", purpose="projection", owner="b", ttl_seconds=30)
    assert nxt.fencing_token > lease.fencing_token
    # Another package keeps its own counter (fences compare only within one scope).
    other = tmp_path / "other"; _manifest(other, package="other")
    assert reopened.activate("ws", "other", other, fencing_token=1)[0]
    assert reopened.acquire("ws", "other", purpose="projection", owner="c", ttl_seconds=30).fencing_token == 2


def test_lease_token_stays_above_pointer_token(tmp_path):
    store = _active_store(tmp_path, pointer_token=1)
    first = store.acquire("ws", "demo", purpose="projection", owner="a", ttl_seconds=30)
    assert store.release(first.lease_id, owner="a")
    bundle = tmp_path / "bundle"
    assert store.activate("ws", "demo", bundle, fencing_token=100, expected=(0, 0))[0]
    nxt = store.acquire("ws", "demo", purpose="projection", owner="b", ttl_seconds=30)
    assert nxt.fencing_token == 101


def test_concurrent_acquire_from_separate_connections_never_duplicates(tmp_path):
    import threading

    _active_store(tmp_path, pointer_token=1)
    tokens: list[int] = []
    errors: list[BaseException] = []
    guard = threading.Lock()

    def worker(index: int) -> None:
        # Each worker has its own store instance => its own SQLite connections.
        store = OntologyLifecycleStore(tmp_path / "state.sqlite")
        try:
            for round_ in range(15):
                purpose = f"w{index}-r{round_}"
                lease = store.acquire("ws", "demo", purpose=purpose, owner=purpose, ttl_seconds=30)
                with guard:
                    tokens.append(lease.fencing_token)
                assert store.release(lease.lease_id, owner=purpose)
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert len(tokens) == 60
    assert len(set(tokens)) == len(tokens)
    assert min(tokens) > 1


def test_concurrent_acquire_from_separate_processes_never_duplicates(tmp_path):
    import multiprocessing

    _active_store(tmp_path, pointer_token=1)
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(2) as pool:
        results = pool.starmap(_acquire_release_many, [(str(tmp_path / "state.sqlite"), f"p{i}", 10) for i in range(2)])
    tokens = [t for chunk in results for t in chunk]
    assert len(tokens) == 20 and len(set(tokens)) == 20


def _acquire_release_many(db: str, prefix: str, count: int) -> list[int]:
    store = OntologyLifecycleStore(db)
    out = []
    for i in range(count):
        purpose = f"{prefix}-{i}"
        lease = store.acquire("ws", "demo", purpose=purpose, owner=purpose, ttl_seconds=30)
        out.append(lease.fencing_token)
        store.release(lease.lease_id, owner=purpose)
    return out


def test_existing_db_is_migrated_from_leases_and_pointer(tmp_path):
    import sqlite3

    db = tmp_path / "state.sqlite"
    store = _active_store(tmp_path, pointer_token=3)
    live = store.acquire("ws", "demo", purpose="projection", owner="a", ttl_seconds=30)
    # Simulate a pre-sr-xsu DB: no high-water table, a surviving lease with a
    # high token for one package, and a pointer-only package.
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE ontology_fence_high_water")
        conn.execute("UPDATE ontology_lease SET fencing_token=40 WHERE lease_id=?", (live.lease_id,))
        conn.execute(
            "INSERT INTO active_ontology (workspace_id, package_id, version, fingerprint, generation, epoch, fencing_token) "
            "VALUES ('ws', 'pointer-only', '1', 'fp', 0, 0, 77)"
        )
    migrated = OntologyLifecycleStore(db)
    with sqlite3.connect(db) as conn:
        rows = dict(((r[0], r[1]), r[2]) for r in conn.execute("SELECT * FROM ontology_fence_high_water"))
    assert rows[("ws", "demo")] == 40
    assert rows[("ws", "pointer-only")] == 77
    # Releasing the surviving lease must not let 40 (or lower) be reissued.
    assert migrated.release(live.lease_id, owner="a")
    assert migrated.acquire("ws", "demo", purpose="projection", owner="b", ttl_seconds=30).fencing_token == 41
    assert migrated.acquire("ws", "pointer-only", purpose="projection", owner="c", ttl_seconds=30).fencing_token == 78
    # Re-running the migration never lowers the high-water.
    OntologyLifecycleStore(db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT high_water FROM ontology_fence_high_water WHERE package_id='demo'").fetchone()[0] == 41


def test_expired_lease_token_is_not_reissued_even_if_row_is_pruned(tmp_path, monkeypatch):
    import sqlite3
    import types

    from seocho.ontology import lifecycle

    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(lifecycle, "time", types.SimpleNamespace(time=lambda: clock["now"]))
    store = _active_store(tmp_path, pointer_token=1)
    old = store.acquire("ws", "demo", purpose="projection", owner="a", ttl_seconds=1)
    clock["now"] += 5
    try:
        store.admission(old.lease_id)
    except ValueError as exc:
        assert "expired" in str(exc)
    else:
        raise AssertionError("expired lease was admitted")
    new = store.acquire("ws", "demo", purpose="projection", owner="b", ttl_seconds=30)
    assert new.fencing_token > old.fencing_token
    # Even if expired/released rows are garbage-collected, tokens keep rising.
    with sqlite3.connect(tmp_path / "state.sqlite") as conn:
        conn.execute("DELETE FROM ontology_lease")
    newest = store.acquire("ws", "demo", purpose="projection", owner="c", ttl_seconds=30)
    assert newest.fencing_token > new.fencing_token


def _migrated_db_with_live_lease(tmp_path):
    store = _active_store(tmp_path, pointer_token=3)
    lease = store.acquire("ws", "demo", purpose="projection", owner="a", ttl_seconds=300)
    # Raising the pointer above the high-water is the normal post-activate state
    # and must not by itself force a write on the next open.
    assert store.activate("ws", "demo", tmp_path / "bundle", fencing_token=50, expected=(0, 0))[0]
    return tmp_path / "state.sqlite", lease


def test_open_of_migrated_db_takes_no_write_lock(tmp_path):
    import sqlite3
    import time as _time

    db, lease = _migrated_db_with_live_lease(tmp_path)
    blocker = sqlite3.connect(db, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")  # hold the write lock
    try:
        started = _time.monotonic()
        store = OntologyLifecycleStore(db)
        assert store.admission(lease.lease_id)["fencing_token"] == lease.fencing_token
        assert _time.monotonic() - started < 2.0
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()


def test_migrated_db_opens_read_only_via_uri(tmp_path):
    db, lease = _migrated_db_with_live_lease(tmp_path)
    ro = OntologyLifecycleStore(f"file:{db}?mode=ro")
    status = ro.status("ws", "demo")
    assert status["active"]["fencing_token"] == 50
    assert [row["lease_id"] for row in status["live_leases"]] == [lease.lease_id]
    assert ro.admission(lease.lease_id)["fencing_token"] == lease.fencing_token


def test_migrated_db_opens_when_file_is_not_writable(tmp_path):
    import os
    import stat

    import pytest

    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root ignores file permissions")
    db, lease = _migrated_db_with_live_lease(tmp_path)
    OntologyLifecycleStore(db)  # checkpoint so the main file is complete
    mode = db.stat().st_mode
    db.chmod(stat.S_IRUSR | stat.S_IRGRP)
    try:
        ro = OntologyLifecycleStore(db)
        assert ro.status("ws", "demo")["live_leases"][0]["lease_id"] == lease.lease_id
        assert ro.admission(lease.lease_id)["fencing_token"] == lease.fencing_token
    finally:
        db.chmod(mode)


def test_open_migrates_lease_minted_by_pre_upgrade_code(tmp_path):
    # Table already exists, but an old-code acquire inserted a lease row without
    # touching the high-water: the cheap check must detect and fold it in.
    import sqlite3

    db, lease = _migrated_db_with_live_lease(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE ontology_lease SET fencing_token=90 WHERE lease_id=?", (lease.lease_id,))
    store = OntologyLifecycleStore(db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT high_water FROM ontology_fence_high_water WHERE package_id='demo'").fetchone()[0] == 90
    assert store.release(lease.lease_id, owner="a")
    assert store.acquire("ws", "demo", purpose="projection", owner="b", ttl_seconds=30).fencing_token == 91


def test_acquire_is_safe_without_open_time_migration(tmp_path, monkeypatch):
    # Even if the open-time migration is skipped, acquire() still mints above
    # every live lease and the pointer.
    import sqlite3

    db, lease = _migrated_db_with_live_lease(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE ontology_lease SET fencing_token=90 WHERE lease_id=?", (lease.lease_id,))
    monkeypatch.setattr(OntologyLifecycleStore, "_migrate_fence_high_water", staticmethod(lambda conn: None))
    store = OntologyLifecycleStore(db)
    assert store.acquire("ws", "demo", purpose="query", owner="q", ttl_seconds=30).fencing_token == 91
