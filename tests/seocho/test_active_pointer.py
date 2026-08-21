"""RCU active-version pointer + atomic CAS (seocho-ia4.3 B1)."""
from __future__ import annotations
import threading
from seocho.ontology.active_pointer import ActiveOntologyPointer


def _p(tmp_path):
    return ActiveOntologyPointer(str(tmp_path / "active.db"))


def test_first_publish_and_read(tmp_path):
    p = _p(tmp_path)
    ok, cur = p.publish("w", "pkg", version="1.0.0", fingerprint="fp1", fencing_token=1)
    assert ok and cur.epoch == 0 and cur.generation == 0 and cur.version == "1.0.0"
    r = p.read("w", "pkg")
    assert r.version == "1.0.0" and r.expected() == (0, 0)


def test_second_first_publish_fails(tmp_path):
    p = _p(tmp_path)
    p.publish("w", "pkg", version="1.0.0", fingerprint="fp1", fencing_token=1)
    ok, cur = p.publish("w", "pkg", version="9.9.9", fingerprint="fpx", fencing_token=1)
    assert not ok and cur.version == "1.0.0"          # can't first-publish over an existing pointer


def test_cas_swap_bumps_epoch(tmp_path):
    p = _p(tmp_path)
    _, v0 = p.publish("w", "pkg", version="1.0.0", fingerprint="fp1", fencing_token=1)
    ok, v1 = p.publish("w", "pkg", version="2.0.0", fingerprint="fp2", fencing_token=1,
                       expected=v0.expected())
    assert ok and v1.epoch == 1 and v1.version == "2.0.0"


def test_cas_wrong_expected_fails(tmp_path):
    p = _p(tmp_path)
    p.publish("w", "pkg", version="1.0.0", fingerprint="fp1", fencing_token=1)
    ok, cur = p.publish("w", "pkg", version="2.0.0", fingerprint="fp2", fencing_token=1,
                        expected=(0, 99))             # stale epoch
    assert not ok and cur.epoch == 0 and cur.version == "1.0.0"


def test_stale_fencing_token_rejected(tmp_path):
    p = _p(tmp_path)
    _, v0 = p.publish("w", "pkg", version="1.0.0", fingerprint="fp1", fencing_token=5)
    ok, cur = p.publish("w", "pkg", version="2.0.0", fingerprint="fp2", fencing_token=3,
                        expected=v0.expected())       # token below stored -> fenced off
    assert not ok and cur.version == "1.0.0"


def test_concurrent_cas_exactly_one_winner(tmp_path):
    p = _p(tmp_path)
    _, v0 = p.publish("w", "pkg", version="1.0.0", fingerprint="fp0", fencing_token=1)
    expected = v0.expected()
    wins = []
    barrier = threading.Barrier(12)

    def worker(i):
        barrier.wait()
        ok, _ = p.publish("w", "pkg", version=f"2.0.{i}", fingerprint=f"fp{i}",
                          fencing_token=1, expected=expected)
        if ok:
            wins.append(i)

    ths = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    assert len(wins) == 1                              # exactly one CAS wins
    assert p.read("w", "pkg").epoch == 1               # epoch bumped exactly once


def test_recreate_bumps_generation_monotonic(tmp_path):
    p = _p(tmp_path)
    p.publish("w", "pkg", version="1.0.0", fingerprint="fp1", fencing_token=1)
    r1 = p.recreate("w", "pkg", version="1.0.0", fingerprint="fp1", fencing_token=1)
    assert r1.generation == 1 and r1.epoch == 0        # generation strictly increased
    r2 = p.recreate("w", "pkg", version="1.0.0", fingerprint="fp1", fencing_token=1)
    assert r2.generation == 2                          # never reuses a prior generation


def test_workspace_package_isolation(tmp_path):
    p = _p(tmp_path)
    p.publish("acme", "pkg", version="1.0.0", fingerprint="a", fencing_token=1)
    p.publish("globex", "pkg", version="1.0.0", fingerprint="b", fencing_token=1)
    assert p.read("acme", "pkg").fingerprint == "a"
    assert p.read("globex", "pkg").fingerprint == "b"  # no cross-workspace collision
