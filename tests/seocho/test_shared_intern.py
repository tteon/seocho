"""SharedInternTable — canonical namespace + the reclamation half (seocho-ia4)."""

from __future__ import annotations

import threading

from seocho.index.shared_intern import SharedInternTable


def test_first_writer_wins_and_converges():
    t = SharedInternTable()
    a = t.intern("ws", "company|acme", "id-1")
    b = t.intern("ws", "company|acme", "id-2")   # loses — same address returned
    assert a == b == "id-1"
    assert t.get("ws", "company|acme") == "id-1"
    assert t.stats()["hits"] == 1


def test_workspace_isolation():
    t = SharedInternTable()
    t.intern("acme", "company|x", "a-x")
    t.intern("globex", "company|x", "g-x")
    assert t.get("acme", "company|x") == "a-x"
    assert t.get("globex", "company|x") == "g-x"


def test_bounded_reclamation_of_zero_ref_entries():
    """With max_entries set, unreferenced entries are reclaimed LRU-first so the
    table does not grow without bound (the 'heap with no free()' fix)."""
    t = SharedInternTable(shards=1, max_entries=10)
    for i in range(100):
        t.intern("ws", f"e|{i}", f"id-{i}")
    assert len(t) <= 10, "resident set must stay within the cap"
    assert t.stats()["reclaimed"] >= 90
    # the most recently interned survive; the oldest were reclaimed
    assert t.get("ws", "e|99") == "id-99"


def test_retained_entry_is_never_reclaimed():
    t = SharedInternTable(shards=1, max_entries=5)
    t.intern("ws", "pinned", "keep")
    t.retain("ws", "pinned")
    for i in range(50):                          # flood -> pressure
        t.intern("ws", f"junk|{i}", f"j-{i}")
    assert t.get("ws", "pinned") == "keep", "a retained entry survives pressure"
    assert t.pinned_count() == 1
    t.release("ws", "pinned")
    for i in range(50):                          # now it can be reclaimed
        t.intern("ws", f"more|{i}", f"m-{i}")
    assert t.get("ws", "pinned") == ""


def test_reclamation_preserves_stability_via_immutability():
    """A reclaimed entry re-interns to the SAME canonical because ids are a
    deterministic function of identity — reclamation is a cache miss, not a
    correctness change."""
    t = SharedInternTable(shards=1, max_entries=2)
    t.intern("ws", "e|a", "canon-a")             # canonical == f(identity)
    for i in range(10):
        t.intern("ws", f"e|filler{i}", f"canon-filler{i}")
    assert t.get("ws", "e|a") == ""              # reclaimed
    again = t.intern("ws", "e|a", "canon-a")     # same deterministic id
    assert again == "canon-a"


def test_sqlite_backing_is_cross_process_shared(tmp_path):
    """Two independent tables sharing a SQLite path see one namespace with
    atomic first-writer-wins — the fix for 'process-local + racy JSON'."""
    db = str(tmp_path / "intern.db")
    t1 = SharedInternTable(sqlite_path=db)
    t2 = SharedInternTable(sqlite_path=db)       # a separate process would look like this
    a = t1.intern("ws", "company|acme", "from-t1")
    b = t2.intern("ws", "company|acme", "from-t2")   # loses to t1's durable write
    assert a == b == "from-t1"
    assert t2.get("ws", "company|acme") == "from-t1"
    assert t1.stats()["backing"] == "sqlite"


def test_atomic_persist_roundtrip(tmp_path):
    t = SharedInternTable()
    t.intern("ws", "e|1", "id-1")
    t.intern("ws", "e|2", "id-2")
    p = tmp_path / "ns.json"
    t.persist(p)
    assert p.exists() and not list(tmp_path.glob("*.tmp*")), "no temp file left behind"
    t2 = SharedInternTable()
    loaded = t2.load(p)
    assert loaded == 2 and t2.get("ws", "e|1") == "id-1"


def test_thread_safe_intern_converges():
    t = SharedInternTable()

    def worker():
        for i in range(500):
            t.intern("ws", f"e|{i % 50}", f"id-{i % 50}")

    ts = [threading.Thread(target=worker) for _ in range(8)]
    for th in ts:
        th.start()
    for th in ts:
        th.join(timeout=10)
    # exactly 50 distinct entities, one canonical each, regardless of race
    assert len(t) == 50
    for i in range(50):
        assert t.get("ws", f"e|{i}") == f"id-{i}"
