# ADR-0166: ablation A6 — server_share, the OS I/O plane and the bolt-rs gate

Date: 2026-08-15 · Status: accepted (measurement record) · seocho-xju · relates ADR-0163, ADR-0111

## Context

Ablation row A6 (wiki/os-ablation-study-design.md) and the ADR-0163 go/no-go gate
for further data-plane Rust. `execute_query` splits into a **control plane**
(Python governance: cypher hash, lane classify, scope enforcement, admission
acquire/release, EWMA observe, JSON serialize) and a **data plane** (Bolt
round-trip + PackStream decode). `server_share = data / (data + control)`.

## Method

`scripts/agentos/ablation_a6_server_share.py`, live DozerDB (`finbenchl10`,
20,470 Accounts). Data plane = median wall of `session.run(cypher).data()` over
50 iters (warmup 5); control plane = median wall of the real governance steps
(the same hash/classify/enforce/admit/observe/json `execute_query` runs). Two
classes: light (25 rows) and heavy (2,000-row TRANSFER traversal). Codec in use:
**rust-ext** (neo4j-rust-ext, ADR-0111) — confirmed at runtime.

## Result

| class | rows | data ms | control ms | server_share |
|---|---|---|---|---|
| light | 25 | 1.59 | 0.069 | 95.9% |
| heavy | 2000 | 43.96 | 0.062 | 99.9% |

## Reading it

1. **The OS control plane is nearly free.** All governance — the thing that makes
   this an operating layer — costs ~0.06–0.07ms, at most 4.1% of a light query
   and 0.1% of a heavy one. This is the ablation's composition result: turning on
   admission + classification + scope enforcement + binding + budget-metering
   plumbing + serialization adds negligible latency. Governance is not the
   bottleneck.
2. **The data plane dominates (96–99.9%)** — the Bolt round-trip + decode is
   nearly all the time. That is where a Rust driver would act.
3. **But the realized lever there is already pulled.** SEOCHO adopted the
   neo4j-rust-ext PackStream codec (ADR-0111: 1.62× live read, 3.57× bulk
   hydration, and it rejected the pure-Rust `neo4rs` as rc-quality). A further
   native `bolt-rs` driver targets an already-codec-accelerated data plane; its
   marginal gain is unproven and needs its own A/B — it is **not** an automatic
   go from server_share alone.

## Decision

- **bolt-rs = not-yet.** The gate's finding is that governance overhead is
  negligible (so the OS framing costs nothing at runtime) and the data-plane win
  is largely captured by the adopted rust-ext codec. A native driver stays a
  measured-A/B candidate, not a commitment (ADR-0163 discipline held).
- A6 completes the Level-2 ablation (A1–A6 all have a measured OFF/ON or a
  cost-share); the composition-overhead check the study asked for is this ~0.06ms.
