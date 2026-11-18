# ADR-0239: Rust lifecycle authority (`seocho-lifecycled`) owns the lifecycle invariant kernel

- Status: proposed
- Date: 2026-10-23
- Amends: ADR-0163 (the "control plane stays in Python" rule and the
  "control-plane XOR data-plane" PR rule, and adds a correctness exception to
  its `server_share` gate)
- Related: ADR-0155 (Rust cache-layer data plane; not amended), ADR-0219
  (`seochod` projection daemon), ADR-0151 (fenced leases),
  `docs/reference/SEMANTIC_CONTROL_PLANE.md` ("Plane Boundary And Admission
  Policy"), and the in-flight single-host co-located appliance ADR (not yet on
  this branch's base; cite it by number once it lands)
- Design: seocho-rust `docs/design/rust-lifecycle-authority.md`
  (direction 2, `seocho-lifecycled`). That document holds the full design:
  API, schema v2, triggers, invariants I1-I13, failure modes and per-phase
  exit criteria. This ADR records the decision only.
- Tickets (seocho-rust beads issues, not this repository's tracker): sr-xsu
  (fencing-token reuse defect, closed), sr-x9x (merge the sr-xsu fix), sr-5e6
  (mixed old/new minters), sr-5zg (seocho-rust remote)

## Context

### The sr-xsu defect

ADR-0163 placed every control-plane concern in Python, including the ontology
lifecycle store (`src/seocho/ontology/lifecycle.py`, `active_pointer.py`).
That store mints the fencing token that `seochod` (ADR-0219) stamps onto
DozerDB as `_fencing_token` / `_fence_scope`. `seochod` then uses
`_fencing_token <= $fence` to order writers within one `(workspace, package)`
scope.

On `main`, `acquire()` derives the next token from the lease rows that still
exist: `max(active.fencing_token + 1, MAX(ontology_lease.fencing_token) + 1)`.
`release()` deletes the row. A released token can therefore be minted again.
When a token is reused, a stale writer passes the fence or a correct writer is
fenced out. This is a latent defect with a known mechanism, caught in
development review of `seochod`'s `assign_storage_fence`, not a field
failure: reuse was harmless so far because pre-upgrade `seochod` never
stamped `_fencing_token`. `seochod` states the contract only as a comment ("the store issues
these strictly increasing and never reused per (workspace, package)"). Neither
the type system nor any test can see it.

The sr-xsu fix (branch `fix/monotonic-lease-fencing-token`, not yet merged; its
merge is tracked as sr-x9x) adds a durable `ontology_fence_high_water` table
and mints `max(high_water, live max, pointer) + 1` before inserting the lease.
Its upgrade safety, however, rests on a docstring rule: upgrade every Python
opener first, and never run old and new `acquire()` at the same time. sr-5e6
records that mixed old/new minters can still issue duplicate tokens.

### The ownership split behind it

The lifecycle invariants are spread across two languages and several callers:

- Callers choose the activation token. The CLI accepts `--fencing-token`,
  benchmark fixture scripts publish `pointer + 1` (for example
  `scripts/benchmarks/prepare_governed_text2cypher_fixture.py`,
  `token = ... int(current["fencing_token"]) + 1`), and `publish` accepts an equal
  token. Any caller can fence every lease by publishing a large token.
- `renew` runs without a transaction, TTL bound or purpose check.
- Python `admission()` checks liveness only. Only `seochod` checks purpose and
  stale pointers.
- Expiry is written with Python `time.time()` and compared with Rust `now_ms`,
  so two clocks decide one expiry.
- `projection_idempotency` is created by Python DDL but written only by Rust.
  The Rust test fixtures copy the Python DDL by hand.

These are correctness defects in a small set of state machines, not a
performance problem. Fixing them one at a time in Python does not remove the
underlying cause: no single owner enforces the invariants, and any process
that can open the SQLite file can break them.

### Governance constraints

- `SEMANTIC_CONTROL_PLANE.md` already says that the plane names describe
  **authority, not process placement**. It also says data-plane workers may
  execute approved work but **may not decide what is active**.
- ADR-0163 says the control plane "stays in Python". Read literally, that rule
  forbids moving any lifecycle code to Rust.
- ADR-0163 gates further Rust on its plane "per ADR-0155's discipline — no
  Rust on speculation", using a measured `server_share`. That gate is about
  performance and does not fit a correctness defect. ADR-0155 itself does not
  block this work: its `server_share` gate (Decision 2) covers only rewriting
  the Python SDK's Bolt path, and its Decision 1 already makes the cache-layer
  data plane Rust from day one.

## Decision

### 1. Split policy from the invariant kernel

ADR-0163's control plane is split into two layers.

**Policy stays in Python.** This covers promotion decisions, policy mode
(`direct`/`shadow`/`governed`/`lockdown`), purpose policy, bundle validation
and hashing, ontology/profile selection, admission/scheduling, tenancy,
budgets, cost classification and observability. All of ADR-0163's
control-plane list stays where it is.

**The lifecycle invariant kernel moves to a Rust authority.** The kernel is
the small set of state machines whose correctness the data plane depends on:

- the lease state machine (acquire/renew/release/expiry, one held lease per
  `(workspace, package, purpose)`, owner checks, a single authority clock);
- the monotonic fencing-token mint, the only source of tokens, strictly
  increasing and never reused per `(workspace, package)`, durable across
  release, expiry, prune, restart, activation and legacy writers;
- the active-pointer CAS on `(generation, epoch)`, with authority-minted
  activation tokens (callers may pass a `min_token` assertion but never pick a
  token);
- the projection idempotency state machine (claim/complete, no automatic
  deletion of pending rows);
- control-DB schema ownership: the only DDL source, schema versioning,
  `ddl_sha256` tamper detection and the triggers that make legacy writers
  fail closed.

The kernel decides nothing about policy. Python asks it to apply transitions
that policy has already approved. The kernel guarantees that those transitions
keep the invariants for any caller in any language.

### 2. Authority is not the writer

The kernel runs in a **new Rust process, `seocho-lifecycled`**, distinct from
the DozerDB writer `seochod`. The properties below are the **target state**,
reached at the phase exits in Rollout (the opener and linkage properties at
the P3 exit, Python write-path removal at P4). During P1-P2 `seochod` still
opens the control DB directly and writes `projection_idempotency`, and Python
remains a read-write opener when `SEOCHO_LIFECYCLED_SOCKET` is unset.

- `seocho-lifecycled` becomes the only read-write opener of the control DB and the
  only minter (from P3 for `seochod`, P4 for Python). It holds no Bolt credentials and never writes to DozerDB.
- `seochod` stays the DozerDB writer and becomes a **verifier-only client** of
  the authority (P3). It will link only the wire/proto crate, so it cannot
  mint, and CI will enforce that through a `cargo tree` check and a `compile_fail` test.
  It will re-check capabilities just before commit through the authority.
- Python callers keep the `OntologyLifecycleStore` API and the admission dict
  shape `{lease_id, fingerprint, generation, epoch, fencing_token}`. They
  delegate mutating operations to the authority over a local socket when
  `SEOCHO_LIFECYCLED_SOCKET` is set.
- Each op set has its own Unix socket (operator, client, writer) with its own
  dispatch table. A process that can write DozerDB therefore cannot activate
  an ontology, and a client cannot run writer operations.

Putting the mint inside `seochod` was considered and rejected. It would put
the Bolt password and the minting authority in one process, which breaks the
"data-plane workers may not decide what is active" rule. Loading a Rust
library into each Python process through PyO3 was also rejected. It would give
every Python process full authority, it could not keep the sr-xsu rollback
path, and it would load two SQLite builds into one process.

### 3. Correctness exception to ADR-0163's measurement gate

ADR-0163's gate ("per ADR-0155's discipline — no Rust on speculation", gated
on measured `server_share`) applies to Rust adopted **for performance**. It
does not apply when Rust is adopted **to enforce a correctness or safety
invariant** that has a documented defect with a known mechanism. The sr-xsu
fencing-token reuse, caught in development review, is such a defect. ADR-0155
is not amended: its gate covers only the Python SDK Bolt-path rewrite.

Under this exception:

- The entry evidence is the documented defect (defect record, failing invariant and
  reproduction), not a latency or throughput number.
- Performance stays gated, not assumed. The new IPC hops on the admission and
  projection paths must be measured per plane (extending ADR-0163's
  observability rule) at the P2 gate. A regression is reported with the
  number and may block the rollout. It cannot be used to argue the
  correctness work out of existence.
- The exception covers only the invariant kernel listed in Decision 1. It does
  not license moving policy, query control or SDK behavior to Rust.

### 4. Amendments to ADR-0163

- "Control plane ... stays in Python" now reads: *control-plane **policy**
  stays in Python; the lifecycle invariant kernel is owned by the Rust
  authority `seocho-lifecycled` (ADR-0239).*
- The "control-plane XOR data-plane" PR rule still holds. Work that touches
  both the authority and the writer is split into separate PRs (P3a/P3b below).
- The `server_share` gate in ADR-0163 still governs bolt-rs and other
  data-plane driver swaps.

### 5. Scope: single host

The authority's backend is SQLite/WAL on one host. It holds a process-lifetime
file lock as the single writer and runs with explicit `synchronous=FULL`.
This matches the single-host rule in `SEMANTIC_CONTROL_PLANE.md` and the
single-host co-located appliance topology used for the paper runs. Multi-host operation is out of
scope. The backend is a trait so that a linearizable shared authority
(PostgreSQL with transactional CAS, or etcd) can replace it later, and that
change needs its own ADR plus a rerun of fencing and failover tests. The
control DB must not be shared over a network filesystem. Signed capabilities
(Ed25519) are deferred to that multi-host phase. On one host, the precommit
`check` RPC is authoritative.

## Rollout

Phases follow the design document (§7). Each phase ships alone and can be
rolled back.

| Phase | Content | Exit | Rollback |
| --- | --- | --- | --- |
| P0 governance | merge sr-xsu (sr-x9x); this ADR; seocho-rust remote (sr-5zg); file tracking issues | ADR accepted, sr-xsu merged, remote exists | documentation only |
| P1 library + audit | `seocho-lifecycle-proto`, `seocho-lifecycle`, `seocho-lifecycled audit`; schema v2 and triggers; model, legacy-writer replay and golden-fixture tests; `seochod` test fixtures use the owned schema | CI green; `audit` finds zero violations on the operating control DB | no production change |
| P2 opt-in authority | `serve`; `migrate` (backup first, DozerDB token floor or explicit attestation required, v0 DBs refused); Python `LifecycleClient`/`LeaseKeeper` behind `SEOCHO_LIFECYCLED_SOCKET`; `--fencing-token` becomes assert-only; `seochod` unchanged | existing lifecycle tests green on both backends; differential scenarios match; kill -9 chaos; per-plane latency measured | `downgrade --to-schema 1` keeps the high-water and lets sr-xsu Python resume; downgrade to v0 is refused |
| P3 `seochod` pure client | (a) writer-socket ops in the authority; (b) `seochod` `LifecycledVerifier`, `Admitted`-only governed writes, `db_uuid` preflight; online query admission uses `read_admit` | one governed FinDER run; kill -9 during projection; `seochod` no longer opens the control DB | `SqliteReadVerifier` kept behind `SEOCHOD_CONTROL_DB` for one release |
| P4 retirement | remove Python write SQL and DDL (read-only reads remain); fold in `retired_ontology`; retire `dataplane/seochod` in this repository | test asserts no Python lease INSERT or token mint remains | revert binaries/wheel to the previous release; schema stays v2 |
| P5 multi-host (conditional) | Postgres/etcd backend, mTLS, signed capabilities | fencing/failover chaos rerun | per-backend rollback; needs its own ADR |

Python changes are made in a dedicated worktree branch and touch only files
that are clean in the owner's checkout. They do not change the public API.

## Consequences

- The sr-xsu contract moves from a code comment into three enforcement layers:
  a type that only the mint can construct, database triggers that apply to
  writers in any language, and model, multi-process and legacy-writer replay
  tests. Mixed old/new minters (sr-5e6) fail closed instead of issuing
  duplicate tokens.
- The authority is a **new single point of failure** and adds one supervised
  process. While it is down, every governed path fails closed. Pinned reads
  continue through a read-only SQLite reader, and `LeaseKeeper` covers short
  outages. This is accepted. Projection already depends on `seochod` the same
  way.
- After migration to schema v2, un-upgraded Python openers, including sr-xsu
  Python, abort on triggers. This is intended, so cutover happens per
  deployment unit after a read-only `audit`.
- On a single-uid development box, the three-socket privilege split reduces to
  a process boundary. It provides real separation only when the appliance runs
  the processes as separate OS users.
- A second Rust process enters the repository's operational surface. Its code
  lives in seocho-rust. This repository gains only a thin client facade and,
  in P4, loses its duplicate `dataplane/seochod` copy.

## Validation

- P1 exit: the read-only `audit` run against the operating control DB reports
  no duplicate tokens, no high-water below the live or pointer maximum, and no
  expired-but-held rows. The result is attached to the tracking issue.
- P2 exit: differential scenarios produce the same observable results on the
  legacy Python backend and the authority backend, and per-plane latency of
  the added IPC hops is recorded.
- P3 exit: one governed FinDER run completes end to end with `seochod` as a
  pure verifier client, and a kill -9 during projection leaves no reused token
  and no deleted pending idempotency row.
