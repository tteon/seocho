# bolt-rs — the I/O-plane organ (agent ↔ knowledge base)

The sixth SEOCHO agent-OS organ: the OS owns not just the control plane (allocator,
eviction, scheduling, isolation, versioning) but the **data-plane transport** between a
fleet of agents and the graph. This is a Rust replay harness (neo4j 0.2 sync driver →
DozerDB) that measures the agent↔DB exchange at fleet scale, with **no LLM in the loop** —
pure DB-side agent behaviour (lookups, paging, write tx, row→context serialization).

Maps to the AgenticOS CFP "Resource Management & Execution → cross-layer optimization
(compute, memory, **network**, cost, latency)" and "execution substrates".

## Arms (see EXPERIMENT.md)
- `scale <db> <N> <episodes>` — does thread-per-agent scale (Python sank at ~1.3 cores)?
- `mix <db> <N> <K> <secs>` — noisy pagers inflate lookup p99?
- `contend <N> <same|spread> <writes>` — hot-node write contention (isolated `agentcontend` DB)
- `dedup <db> <N> <R> <same|distinct>` — **the organ's headline**: the row→context path has NO
  cache, so identical result-sets are paid in full by every agent — the redundancy the OS's
  shared canonical pool / context cache eliminates, measured in bytes-on-the-wire.

## Build & run (against dozerdb-h0)
```
cd scripts/agentos/bolt_rs && cargo build --release
BOLT_HOST=127.0.0.1 BOLT_PORT=17687 BOLT_USER=neo4j BOLT_PASS=h0gatepass \
  ./target/release/rust-harness dedup finbenchl1 4 3 same
```
`make_driver()` is env-configurable (`BOLT_HOST/PORT/USER/PASS`).

## Measured (dozerdb-h0, finbenchl1, dedup 4×3 same) — outputs/agentos/bolt_rs_dedup_finbenchl1.json
`redundancy_factor=12` — 12 identical fetches, each paid in full; `total_bytes_json=82068`
vs `unique_bytes_json=6839` = **12× redundant bytes on the wire** for one answer's worth of
unique data. This is the I/O-organ's falsifiable metric: with the shared-memory / interning
organ (ADR-0203/0204) collapsing redundant entities, the redundancy_factor (and wire bytes)
must drop for the same answers.

## Roadmap
Wire the governed `execute_query` (SeochoOS) over this Rust bolt path so the syscall interface
runs on the fast transport; add `dedup redundancy_factor` + `p99 under contention` as a Plane-1
mechanism metric of the arm×organ study (bolt-rs on/off).
