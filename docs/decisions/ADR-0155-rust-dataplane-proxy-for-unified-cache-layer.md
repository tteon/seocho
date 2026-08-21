# ADR-0155: Rust data plane for the unified cache layer; the Python SDK driver stays

Date: 2026-08-15 · Status: proposed

## Context

The unified cache layer (epic seocho-fix, design note
`unified-cache-layer-design.md` v0.3) places SEOCHO in the wire path between
the agent loop, vLLM, and DozerDB: it must observe every Bolt result that
becomes LLM context, maintain a node→KV-block reverse index, subscribe to
vLLM KVEvents at block granularity, intercept writes as the primary
invalidation source (WP4 — DozerDB CE has no CDC), and serve the
translation-table lookups behind `pin class:X` (WP6.1a). These are per-block,
per-frame operations on the hot path of every request.

Two language questions were being conflated:

1. Should the **existing Python SDK's Bolt path** be rewritten in Rust?
2. What language should the **new data-plane component** be written in?

Evidence in hand:

- ADR-0111 already moved the SDK's byte-decoding hot spot to Rust
  (`neo4j-rust-ext`, 3.6x on the W2 hydration slice, parity-gated). What
  remains Python in the SDK path is orchestration, not codec.
- PR #482 added `db.client.operation.duration` and
  `db.client.operation.server_share`, so the server-vs-client split of real
  workloads is now measured rather than assumed.
- The `aie` KV-efficiency experiment referenced as motivation is an unrun
  scaffold (no commits, empty `data/` and `results/` as of 2026-08-15); it
  contributes design intent, not measurements.
- The data-plane component does per-request work no Python process should
  hold under load: token-block hashing for the KV reverse index, Bolt frame
  relay with statement extraction, Arrow IPC projection batches (ADR-0149),
  and concurrent CDC-less write interception — all GIL-hostile.

## Decision

1. **The cache-layer data plane is Rust from day one.** It is a new
   component, not a rewrite: a proxy process that (a) speaks Bolt to DozerDB
   as a client via the `neo4j` crate (docs.rs/neo4j, maintained by the
   Neo4j Python-driver author; `neo4rs` is the evaluated fallback if the
   crate stalls), (b) accepts Bolt connections on the listener leg with a
   minimal frame relay — enough parsing for statement/summary extraction and
   passthrough, not a full server implementation, (c) hosts the WP4
   node→KV-block reverse index and write interception, the WP6.1a
   translation table, and the ADR-0149 Arrow projection surface.
2. **The Python SDK's Bolt path is not rewritten now.** The gate is
   empirical: sustained `db.client.operation.server_share` measurements from
   production-shaped workloads. If the client share (1 − server_share) stays
   small, an SDK rewrite buys nothing; if it grows dominant on cache-layer
   workloads, reopen with that data attached.
3. **Python touches the data plane only at control points.** Policy
   decisions, ontology artifacts, and CLI stay in Python; the proxy exposes
   a control API (and PyO3 bindings only where in-process calls are
   unavoidable). No canonical SDK behavior moves out of `src/seocho/`.
4. **Kill criteria before code.** The component is built behind the WP0
   gate like everything else in the epic: if H0 (working-set overlap) fails,
   the proxy's cache responsibilities shrink to invalidation + observation,
   and this ADR's scope shrinks with it.

## Consequences

- A second toolchain (cargo) enters CI for one bounded component; the
  repository layout gains a `dataplane/` (or sibling-repo) surface — decided
  at implementation PR time with the layout-contract docs updated together.
- Bolt protocol version drift becomes our responsibility on the relay leg;
  pinning DozerDB (5.26 LTS line) bounds it.
- The `neo4j` crate is community-maintained; the fallback path (`neo4rs`)
  and the thin-relay design keep the exposure replaceable.
- Runtime guardrails are unchanged: `workspace_id` propagation and Cypher
  validation happen before the proxy; the proxy never rewrites statements.

## Validation

- Before merge of the first implementation PR: a relay-overhead benchmark
  (proxy in path vs direct) on the FinBench replay harness — the proxy must
  cost less than the hydration slice rust-ext recovered, or it does not ship.
- `server_share` dashboards reviewed after two weeks of collection to close
  or reopen the SDK-rewrite question with numbers.
