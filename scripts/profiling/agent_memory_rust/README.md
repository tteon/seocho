# Agent-memory Rust parity probe

This probe measures residual client/runtime overhead after PostgreSQL pooling
and atomic strict sequence allocation. It is not a second memory implementation
and it does not weaken the v1 consistency contract.

Each event commits, in one PostgreSQL transaction:

1. idempotency lookup;
2. per-aggregate advisory lock;
3. strict workspace sequence allocation;
4. immutable revision;
5. projection outbox entry;
6. idempotency receipt.

The run passes only when revision, receipt, outbox, and head cardinality equal
the requested event count. The Python comparison is
`scripts/benchmarks/agent_memory_sequence_scalability_live.py` with the same
event count, concurrency, aggregate count, and distribution.

```bash
cargo run --release --manifest-path \
  scripts/profiling/agent_memory_rust/Cargo.toml -- \
  --events 10000 --concurrency 64 --aggregate-count 10000 \
  --distribution uniform --output /tmp/sequence-rust.json
```

Pass the DSN through `SEOCHO_POSTGRES_DSN`; do not place credentials in command
arguments or artifacts. `uniform` and `hot-one` are supported. SQLx uses
database `now()` for event timestamps, which is stated in every artifact.

Rust can improve bounded async scheduling, hashing, serialization, and pool
use. It cannot parallelize transactions waiting on the same PostgreSQL row or
the required order of one aggregate. Interpret its result only beside the
phase-level Python measurements.
