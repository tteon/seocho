# ADR-0163: the OS's I/O subsystem — control-plane / data-plane split

Date: 2026-08-15 · Status: accepted (architecture) · relates to ADR-0155, ADR-0144

## Context

SEOCHO is being assembled as an agent OS (ADR-0157; unified surface PR #510).
An OS mediates between processes and hardware via **drivers** behind a **syscall
boundary**. SEOCHO-as-OS mediates between agents and two "devices": the graph
database (Bolt / DozerDB) and the LLM (OpenAI-compatible API). hadry asked where
the Bolt / OpenAI-API protocol optimization (neo4j-bolt-rs and friends) belongs
now that this is an OS. It belongs to the OS's **I/O subsystem**, and that
subsystem has two planes that must not be conflated.

## Decision

Split, explicitly, the operating layer into a control plane and a data plane,
with a single named seam between them.

**Control plane** — policy and decisions, low QPS, stays in Python:
- admission / scheduling (`LaneScheduler`, `PriorityAdmission`)
- tenancy pinning + fail-closed read enforcement
- token-budget metering (RunHooks over the LLM path)
- cost classification (per-cypher-hash EWMA)
- observability (golden signals, decision receipts)

**Data plane** — the actual I/O, high QPS, the optimization surface:
- the Bolt round-trip + PackStream decode to DozerDB
- the LLM token stream over the OpenAI-compatible API
- connection pooling, batching, zero-copy hydration

**The seam is one call.** `SeochoOS.execute_query` (control: classify → admit →
pin → execute → release → observe) invokes `graph_store.query(...)` (data: the
Bolt round-trip). The LLM seam is `RunHooks` (control: budget) over the SDK's
model I/O (data). Optimizing the data plane must not require touching the control
plane, and vice versa — that is the whole point of naming the split.

## Where protocol optimization goes (and the gate before it)

neo4j-bolt-rs / a native Rust driver is a **data-plane driver** swap beneath the
control-plane gate. It replaces or accelerates `graph_store.query`'s transport +
codec without changing admission, tenancy, or scheduling. There is already
partial Rust on this plane: the neo4j 6.x driver's PackStream codec reports
`rust-ext` vs `pure-python` (`graph.py:packstream_codec()`), and ADR-0144 already
splits server time (ResultSummary) from client hydration at this boundary.

**The gate (per ADR-0155's discipline — no Rust on speculation):** before
investing in bolt-rs we measure the data plane's share of `execute_query` wall
time — `server_share` = (Bolt round-trip + decode) / (total governed-query
time). If the data plane dominates, bolt-rs pays; if LLM wait or control-plane
overhead dominates, it does not. This is a measurement gate, not a belief. The
OpenAI-API path is the other data-plane driver (token streaming); its
control-plane counterpart is budget metering, already built.

## Consequences

- The Rust data-plane work (ADR-0155) has a precise home and a precise trigger:
  data-plane driver, gated on `server_share`. A follow-up ticket owns the
  measurement.
- The control plane stays Python and framework-agnostic; it is where the OS's
  policy value lives and where it must stay legible.
- Design rule going forward: a change is control-plane XOR data-plane; if a PR
  touches both, it is doing two things and should be split.
- Observability must attribute wall time to the plane (server vs client vs
  control-plane overhead), extending ADR-0144, so `server_share` is a standing
  signal, not a one-off probe.
