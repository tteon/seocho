"""RCU reader pin/epoch registry (seocho-ia4.3 B2)."""
from __future__ import annotations
import threading
from seocho.ontology.active_pointer import ActiveOntologyPointer
from seocho.ontology.version_pin import VersionPinRegistry


def _setup(tmp_path):
    p = ActiveOntologyPointer(str(tmp_path / "active.db"))
    p.publish("w", "pkg", version="1.0.0", fingerprint="fp0", fencing_token=1)
    return p, VersionPinRegistry(p)


def test_pin_returns_current_epoch_and_refcounts(tmp_path):
    p, reg = _setup(tmp_path)
    e = reg.pin("w", "pkg")
    assert e == 0 and reg.pin_count("w", "pkg", 0) == 1
    assert reg.min_pinned_epoch("w", "pkg") == 0
    reg.unpin("w", "pkg", e)
    assert reg.min_pinned_epoch("w", "pkg") is None      # None, never "current" (fix #6)


def test_pin_none_when_no_pointer(tmp_path):
    p = ActiveOntologyPointer(str(tmp_path / "a.db"))
    reg = VersionPinRegistry(p)
    assert reg.pin("w", "absent") is None


def test_pin_after_swap_tracks_new_epoch(tmp_path):
    p, reg = _setup(tmp_path)
    _, v = p.publish("w", "pkg", version="2.0.0", fingerprint="fp1", fencing_token=1, expected=(0, 0))
    assert v.epoch == 1
    e = reg.pin("w", "pkg")
    assert e == 1 and reg.min_pinned_epoch("w", "pkg") == 1


def test_increment_then_recheck_retries_on_mid_pin_swap(tmp_path, monkeypatch):
    # simulate a swap landing BETWEEN the reader's read and its recheck: the first two
    # reads see epoch 0 (read, then incr), the recheck sees epoch 1 -> must retry, and
    # end with the transient epoch-0 refcount released.
    p, reg = _setup(tmp_path)
    p.publish("w", "pkg", version="2.0.0", fingerprint="fp1", fencing_token=1, expected=(0, 0))  # now epoch 1
    from seocho.ontology.active_pointer import ActiveVersion
    seq = [ActiveVersion("w", "pkg", "1.0.0", "fp0", 0, 0, 1),   # pin: read -> E=0
           ActiveVersion("w", "pkg", "2.0.0", "fp1", 0, 1, 1),   # pin: recheck -> E2=1 (swap!)
           ActiveVersion("w", "pkg", "2.0.0", "fp1", 0, 1, 1),   # retry: read -> 1
           ActiveVersion("w", "pkg", "2.0.0", "fp1", 0, 1, 1)]   # retry: recheck -> 1 (stable)
    calls = {"i": 0}
    def fake_read(ws, pkg):
        v = seq[min(calls["i"], len(seq) - 1)]; calls["i"] += 1; return v
    monkeypatch.setattr(p, "read", fake_read)
    e = reg.pin("w", "pkg")
    assert e == 1                                          # pinned the stable current epoch
    assert reg.pin_count("w", "pkg", 0) == 0               # transient epoch-0 pin was released
    assert reg.pin_count("w", "pkg", 1) == 1


def test_min_pinned_reflects_oldest_live_pin(tmp_path):
    p, reg = _setup(tmp_path)
    e0 = reg.pin("w", "pkg")                                # epoch 0
    p.publish("w", "pkg", version="2.0.0", fingerprint="fp1", fencing_token=1, expected=(0, 0))
    e1 = reg.pin("w", "pkg")                                # epoch 1
    assert reg.min_pinned_epoch("w", "pkg") == 0           # oldest live pin
    reg.unpin("w", "pkg", e0)
    assert reg.min_pinned_epoch("w", "pkg") == 1           # advances after old reader leaves
    reg.unpin("w", "pkg", e1)
    assert reg.min_pinned_epoch("w", "pkg") is None


def test_pinned_context_manager_releases(tmp_path):
    p, reg = _setup(tmp_path)
    with reg.pinned("w", "pkg") as e:
        assert e == 0 and reg.pin_count("w", "pkg", 0) == 1
    assert reg.min_pinned_epoch("w", "pkg") is None


def test_concurrent_pins_all_counted(tmp_path):
    p, reg = _setup(tmp_path)
    barrier = threading.Barrier(16)
    def worker():
        barrier.wait()
        e = reg.pin("w", "pkg")
        reg.unpin("w", "pkg", e)
    ths = [threading.Thread(target=worker) for _ in range(16)]
    for t in ths: t.start()
    for t in ths: t.join()
    assert reg.min_pinned_epoch("w", "pkg") is None        # all released, no leak
