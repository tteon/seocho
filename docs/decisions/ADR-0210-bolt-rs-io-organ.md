# ADR-0210: bolt-rs — the I/O-plane organ (agent↔KB transport), measured

- Status: accepted
- Date: 2026-08-16
- Tickets: seocho-ia4 (structured runtime), the AgenticOS CFP mapping
- Related: ADR-0111 (rust-ext lever), ADR-0203/0204 (intern / cross-source), ADR-0205 (governed execute)

## Context

hadry's push: bolt-rs must be an explicit organ, not deferred. The prior panels ruled the
"agent OS" claim needs the I/O plane too, and the AgenticOS CFP asks for "execution substrates"
and "cross-layer optimization (compute, memory, **network**, cost, latency)". A working Rust
data-plane harness already existed (~/lab/AIsummit26/rust-harness): N OS-thread agents, each its
own session + tx against DozerDB via the neo4j 0.2 sync driver, measuring the agent↔DB exchange
at fleet scale with no LLM in the loop.

## Decision

Vendor the harness into SEOCHO as `scripts/agentos/bolt_rs/` (Cargo.toml + src + EXPERIMENT.md),
made env-configurable (`BOLT_HOST/PORT/USER/PASS`) so it runs against dozerdb-h0, and establish
it as the **sixth organ: the I/O plane**. Its `dedup` arm is the organ's falsifiable metric —
the row→context path has NO cache, so identical result-sets are paid in full by every agent, and
the shared canonical pool / interning organ (ADR-0203/0204) must reduce that redundancy.

## Consequences

- **Measured live (dozerdb-h0, finbenchl1, `dedup 4 3 same`,
  outputs/agentos/bolt_rs_dedup_finbenchl1.json):** `redundancy_factor = 12`,
  `total_bytes_json = 82068` vs `unique_bytes_json = 6839` — **12× redundant bytes on the wire**
  for one answer's worth of unique data; ~2.7-4e4 rows/s Rust data-plane throughput. The I/O
  organ is no longer a roadmap bullet — it has a number.
- This is the transport-level statement of the shared-memory thesis: interning that collapses
  redundant entities should drop the redundancy_factor and wire bytes for the same answers — a
  Plane-1 mechanism metric (bolt-rs on/off) alongside leak/conformance/torn-rate.
- The harness builds in ~1.5s (release) and is self-contained under scripts/ (not part of the
  Python package; `target/` gitignored).

## Roadmap
Wire the governed `execute_query` (SeochoOS, ADR-0205) over this Rust bolt path so the syscall
interface runs on the fast transport, and add the dedup/contention metrics to the arm×organ
Plane-1 study. Full productionization (async, connection-pool tuning) stays demand-gated (ADR-0111).
