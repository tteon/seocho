# seochod

`seochod` is SEOCHO's local Rust data-plane daemon. It owns a Unix-domain
socket and uses the Rust `neo4j` Bolt driver to project already-approved,
ontology-shaped nodes and relationships into DozerDB. It intentionally does
not accept arbitrary Cypher, raw documents, ontology reasoning, or LLM input.

Configure its Bolt connection only through process environment variables:
`SEOCHOD_BOLT_URI` (or `SEOCHOD_BOLT_HOST` and `SEOCHOD_BOLT_PORT`),
`SEOCHOD_BOLT_USER`, and `SEOCHOD_BOLT_PASSWORD` (see "Bolt connection"
below). Start it with
`SEOCHOD_CONTROL_DB=<lifecycle.sqlite> cargo run -p seochod -- "$XDG_RUNTIME_DIR/seochod/seochod.sock"`
(see the socket and governance sections below), then set
`SEOCHO_RUST_PROJECTOR_SOCKET` to the same path for the SEOCHO CLI.

The daemon fails closed: when the socket is configured, a failed projection is
returned as an indexing failure rather than silently falling back to Python.

## Socket policy

At startup an existing socket path is only removed when it is a stale socket
(a connect attempt is refused). A path that is not a socket, or a socket that
still accepts connections ("socket in use"), makes the daemon refuse to start.
After binding, the socket is chmod'ed to `0600`. Because there is a short
window between `bind` and `chmod`, place the socket in a directory owned by the
daemon user with mode `0700` (for example `$XDG_RUNTIME_DIR/seochod/`), not
directly in a shared directory such as `/tmp`.

## Governance mode

`SEOCHOD_CONTROL_DB=<lifecycle.sqlite>` points the daemon at the ontology
lifecycle store; every projection is then admitted against the live lease and
active pointer. Without it the daemon cannot check admission, so it refuses to
start unless `SEOCHOD_ALLOW_UNGOVERNED=1` is set explicitly (development and
`direct`/`shadow` comparison arms only).

The control DB schema is owned by the Python lifecycle store. seochod creates
no tables in it: it only reads the lifecycle tables (`ontology_lease`,
`active_ontology`) and writes rows to the existing `projection_idempotency`
table.

## Projection idempotency

With a control DB every projection needs an `idempotency_key`, claimed in the
`projection_idempotency` table before any write:

- `completed`: only successful (`ok=true`) responses are cached; a retry with
  the same key replays the stored receipt without writing.
- `failed`: an `ok=false` response is stored with its `response_json` but is
  not replayed. The next claim with the same key re-claims it (back to
  `pending`) and re-runs the writes, which are idempotent `MERGE`s.
- `pending`: a claim in flight blocks retries, unless it is older than
  `SEOCHOD_PENDING_RECLAIM_MS` (default `600000`, 10 minutes), in which case its
  holder is presumed dead and the key is re-claimed atomically. A holder whose
  claim was taken over cannot record its outcome afterwards.

Request checks that need no control DB (bookmarks, `SEOCHOD_WRITE_MODE`) run
before the key is claimed, so such a rejection leaves no `pending` claim and a
corrected retry with the same key proceeds at once. A rejection found after the
claim (an invalid item, for example) is recorded as `failed`.

A key bound to a different workspace or bundle fingerprint is always rejected.
If the outcome cannot be recorded, the response is turned into `ok=false` with
an `error` and the failure is logged to stderr.

## Atomic, fenced projection

All node and relationship writes of one request run in a single explicit Bolt
transaction. Every item is validated before any write, and any write error, a
relationship whose `MATCH` finds no endpoint nodes, or a failed admission
re-check fails the request (`ok=false`) and rolls the transaction back, so a
request is never partially projected. Lifecycle admission is checked before
claiming the idempotency key and again immediately before commit.

Admission checks the request's lease capability against the lease row in the
control DB and requires the lease's fencing token to be strictly greater than
the active pointer's token (the lifecycle store issues leases that way and
never lowers the pointer token, so a lease at or below it has been
superseded). Only leases whose `purpose` is `projection` are admitted; query
and other purpose leases are rejected. Upgrade the Python lifecycle store
first, then seochod.

seochod relies on the lifecycle store's token contract
(`src/seocho/ontology/lifecycle.py` in this repository): for every (workspace, package), lease
fencing tokens are strictly increasing over the lifetime of the control DB and
never reused, even after leases are released, deleted or expire. The store
issues them from a durable high-water inside the same write transaction as the
lease insert, and initialises that high-water on existing DBs from the largest
lease or pointer token. seochod does not assign or persist fences itself.

The storage fence is therefore the raw, admission-verified lease
`fencing_token`, stamped as `_fencing_token`, with the lease's package as
`_fence_scope`, on nodes and relationships. Fences are compared only within
one package; across packages only `_writer_ts` orders writes. If an item
carries a newer fence of the same package, the request was made under a
superseded lease: it fails (`ok=false`) and rolls back instead of silently
skipping that item. Ungoverned writes carry no fence, so they fail the same way
on an item a governed write has fenced. An item with a newer `_writer_ts` is a
silent last-writer-wins no-op, as in the Python writer.

`merge_conflicts` uses the Python writer's shape,
`{label, key, property, existing, incoming, source_id}`: one entry per
user-facing node property whose stored non-null value differs from the
incoming one.

A governed transaction gets a server-side timeout that ends it before its
pending idempotency claim can be re-claimed (the reclaim window minus a margin
of up to 5 s), so a second holder cannot overlap it; every retry attempt (see
below) gets only the part of that window still left. An ungoverned
transaction gets `SEOCHOD_UNGOVERNED_TX_TIMEOUT_MS` (default 60 s), so a stuck
transaction cannot block the serial daemon indefinitely. This is new: before,
an ungoverned transaction had only the server's `db.transaction.timeout` (none
by default), so an ungoverned projection that needs more than 60 s must raise
the value, or set it to `0` to keep the server default. If the commit itself
fails without a server answer, the error says the outcome is unknown rather
than rolled back (`error_kind: "commit_unknown"`); the key is recorded as
`failed` and a client retry re-runs the idempotent `MERGE`s.

## Batched writes

By default (`SEOCHOD_WRITE_MODE=unwind`) the writes of one request are sent as
one `UNWIND $rows` statement per node label and per (relationship type,
source label, target label), with at most `SEOCHOD_UNWIND_BATCH_ROWS` rows
(default 1000, max 10000) each, all inside the same explicit transaction.
Node statements run before relationship statements. Each row keeps the
per-item guards above (`_fencing_token`/`_fence_scope` fence, `_writer_ts`
freshness, `merge_conflicts`) and returns its row index, so a fenced item or a
relationship without both endpoints still fails and rolls back the whole
request, naming that item. (The Python writer's `MATCH`-based batch drops such
rows silently; seochod does not.) Rows of one statement do not see each
other's writes, so two items that may touch the same graph entity never share
a statement and always run in request order, even when they belong to
different groups. The entity is the node id under any label (one id written
as `Person` and as `Org` can be one multi-label node) and, for relationships,
(type, source id, target id) under any endpoint labels (a relationship given
once with labels and once without can be one relationship). Such an item joins
its group's newest statement only if that statement runs after the one
holding the previous write of the same entity; otherwise it opens a new
statement. Repeated items therefore apply in request order and report the
same `merge_conflicts` as per-item writes; unrelated items are free to be
grouped. `merge_conflicts` are reported in request order.

`SEOCHOD_WRITE_MODE=per_item` restores one MERGE statement per item (the
pre-batching shapes); the live suite checks both modes produce the same graph
and responses. Batching pays off once each label has an `(id)` index (the
Python ontology creates one); without an index a batch is planned as a label
scan per row. Batching replaces one round trip and statement per item with
one `UNWIND` statement per group; latency measurements will be published in a
live-run evidence report with the full execution context (service versions,
dataset, concurrency, hardware/container limits, warmup, skipped components)
before any numbers are quoted here. The ignored `bench` test below produces the
raw per-item vs UNWIND timings.

A relationship without `source_label`/`target_label` takes the label of the
request's own node with that id, when the request gives that id exactly one
label. This narrows the match: before label inference an unlabeled endpoint
matched every node with that id under any label, so one relationship could
attach to several same-id nodes. `SEOCHOD_INFER_ENDPOINT_LABELS=0` turns
inference off and restores that matching (in both write modes). An endpoint
that stays unlabeled is matched by id over every node (`AllNodesScan`), so
its cost grows with the database;
`SEOCHOD_REQUIRE_ENDPOINT_LABELS=1` rejects such relationships before any
write.

## Property values

Values are checked before any Bolt I/O, so a bad value names its item instead
of aborting the transaction with a server `TypeError`: integers beyond the
64-bit signed range are rejected (they are not silently stored as floats),
lists must hold non-null scalars of one type (integers and floats may mix and
are stored as floats; empty lists are fine), and nested lists or objects are
rejected. A JSON `null` property removes the stored property (`SET n +=`), as
in the Python writer, and is not reported as a conflict. JSON carries no
temporal types: dates stay strings.

### Reserved properties

seochod owns these node and relationship properties and stamps them itself:

| Property | Set from |
| --- | --- |
| `_source_id`, `_writer_agent` | request `source_id` |
| `_workspace_id` | request `workspace_id` |
| `_writer_ts` | request `writer_ts` (or the daemon clock) |
| `_rdf_bundle_sha256`, `_rdf_data_graph_sha256`, `_agent_profile_sha256`, `_projection_receipt_sha256` | `semantic_receipt`, when given |
| `_fencing_token`, `_fence_scope` | the admitted lease (governed writes only) |

An item whose `properties` contain any of them is rejected in every mode,
including `SEOCHOD_ALLOW_UNGOVERNED=1`, with an `invalid_request` error naming
the item and the keys, and the whole request writes nothing. A caller cannot
plant a storage fence or forge receipt provenance this way. Other
underscore-prefixed properties are stored as given.

## Bolt connection

`SEOCHOD_BOLT_URI` (for example `bolt://127.0.0.1:7687`, or `neo4j://...` for
a routed cluster) wins over `SEOCHOD_BOLT_HOST`/`SEOCHOD_BOLT_PORT`; the
HOST/PORT form connects directly (`bolt://`, no routing), which is all a
single DozerDB instance needs. The driver uses a small pool
(`SEOCHOD_BOLT_POOL_SIZE`, default 4), a 5 s acquisition and 3 s connect
timeout, TCP keepalive, user agent `seochod/<version>`, no driver telemetry,
and a liveness check on any pooled connection idle longer than
`SEOCHOD_BOLT_IDLE_TEST_MS` (default 1000; `0` checks every time), so the
first request after a DozerDB restart does not fail on a dead socket. Every
transaction carries metadata (`app`, `workspace_id`, `source_id`,
`request_id`, `idempotency_key`) visible in `SHOW TRANSACTIONS` and the
server's query log.

The workspace patches the `neo4j` 0.2.0 driver (`vendor/neo4j-0.2.0`, see its
`SEOCHO_PATCHES.md`) to set TCP_NODELAY: upstream does not, and every Bolt
message over 8 KiB then stalls about 40 ms (150 per-item writes of 12 KB
nodes: 7.0 s without, well under 2 s with; the live suite guards this).

## Retries and error kinds

A transaction that fails before COMMIT with a failure the driver marks
retryable (a dropped or refused connection, a deadlock or another
`TransientError`) was rolled back, so the whole transaction is re-run,
statements and admission re-check included, with a doubling backoff from
100 ms to 2 s, for at most `SEOCHOD_BOLT_MAX_ATTEMPTS` (default 10) attempts
within `SEOCHOD_BOLT_RETRY_BUDGET_MS` (default 15000, below the Python
client's 30 s socket timeout; `0` disables retries). `nodes_created` and
`relationships_created` stay exact because only the committed attempt counts.
Once COMMIT was sent, only a server FAILURE reply counts as rolled back; any
other error leaves the outcome unknown and is never retried by the daemon. A
TLS misconfiguration also surfaces as a retryable connection failure and so
fails only after the budget.

Every `ok=false` response carries a stable `error_kind`:

| `error_kind` | Meaning |
| --- | --- |
| `invalid_request` | malformed request or item, rejected before any write |
| `admission` | lifecycle admission failed (before writes, or at the re-check before commit; rolled back) |
| `idempotency` | the idempotency key could not be claimed (pending, other scope, ...) |
| `claim_window` | too little of the reclaim window is left to start the transaction |
| `graph_rejected` | a fenced item or a missing relationship endpoint; rolled back |
| `transient` | retryable Bolt failure that persisted through the retry budget; rolled back |
| `fatal` | non-retryable driver or server error (auth, syntax, type, ...); rolled back |
| `commit_unknown` | COMMIT sent, no answer: the writes may have been applied |
| `receipt_unrecorded` | the outcome could not be recorded in the idempotency table |

`attempts` reports how many transaction attempts ran, when Bolt I/O happened.

## Bookmarks

A successful projection returns `bookmarks`, the commit bookmarks of its
transaction (also stored in, and replayed from, the idempotency receipt). Pass
them to a reader session (`graph.query(bookmarks=...)`) for read-your-writes
across agents, or as the optional request field `bookmarks` (at most 64) to
make a later projection causally follow them. On a single DozerDB instance
this mostly documents the contract; it is required once reads go to replicas.
Both fields are optional and additive: older clients ignore them and older
receipts replay without them.

## Writer timestamps

`writer_ts` (seconds since the Unix epoch; defaults to daemon time) drives the
`_writer_ts` last-writer-wins guard. A non-finite value, or one more than
`SEOCHOD_MAX_CLOCK_SKEW_MS` (default `300000`, 5 minutes) ahead of the
daemon's clock, is rejected with `ok=false` before any write, because a
future timestamp would block every later legitimate update of that item.

## Environment summary

| Variable | Default | Meaning |
| --- | --- | --- |
| `SEOCHOD_BOLT_HOST` / `_PORT` / `_USER` / `_PASSWORD` | `127.0.0.1` / `7687` / `neo4j` / required | DozerDB Bolt connection |
| `SEOCHOD_CONTROL_DB` | unset | lifecycle SQLite; enables admission and idempotency |
| `SEOCHOD_ALLOW_UNGOVERNED` | unset | `1` allows starting without `SEOCHOD_CONTROL_DB` |
| `SEOCHOD_REQUIRE_GOVERNANCE` | unset | `1` requires a semantic receipt on every projection |
| `SEOCHOD_PENDING_RECLAIM_MS` | `600000` | age after which a pending idempotency claim may be re-claimed; also bounds the governed transaction timeout (`0` falls back to the default) |
| `SEOCHOD_MAX_CLOCK_SKEW_MS` | `300000` | max amount `writer_ts` may lead daemon time |
| `SEOCHOD_BOLT_URI` | unset | Bolt URI (`bolt://`, `neo4j://`, `+s`/`+ssc` variants); overrides HOST/PORT |
| `SEOCHOD_BOLT_POOL_SIZE` | `4` | max pooled Bolt connections |
| `SEOCHOD_BOLT_ACQUIRE_TIMEOUT_MS` / `_CONNECT_TIMEOUT_MS` | `5000` / `3000` | connection acquisition / TCP connect timeouts |
| `SEOCHOD_BOLT_IDLE_TEST_MS` | `1000` | idle time after which a pooled connection is liveness-checked |
| `SEOCHOD_BOLT_RETRY_BUDGET_MS` / `_MAX_ATTEMPTS` | `15000` / `10` | retry budget for transient failures before COMMIT |
| `SEOCHOD_UNGOVERNED_TX_TIMEOUT_MS` | `60000` | server-side timeout of an ungoverned transaction (`0` uses the server default) |
| `SEOCHOD_WRITE_MODE` | `unwind` | `per_item` sends one statement per item |
| `SEOCHOD_UNWIND_BATCH_ROWS` | `1000` | max rows per `UNWIND` statement (max 10000) |
| `SEOCHOD_REQUIRE_ENDPOINT_LABELS` | unset | `1` rejects relationships whose endpoint labels stay unresolved |
| `SEOCHOD_INFER_ENDPOINT_LABELS` | `1` | `0` stops taking missing endpoint labels from the request's own nodes |

## Live integration tests

`tests/bolt_it.rs` runs the real binary against a live DozerDB/Neo4j, each
test in a database of its own, and is skipped unless `SEOCHOD_IT_BOLT_URI` and
`SEOCHOD_IT_BOLT_PASSWORD` are set (`SEOCHOD_IT_BOLT_USER` defaults to
`neo4j`). It covers batch vs per-item equivalence, fence and missing-endpoint
rollback, `_writer_ts` last-writer-wins, `merge_conflicts`, value rejection,
bookmark read-your-writes, an index-seek plan for relationship batches and a
TCP_NODELAY latency guard. `SEOCHOD_IT_DESTRUCTIVE=1` adds a DozerDB restart
under a running daemon (`docker restart $SEOCHOD_IT_CONTAINER`, default
`seocho-rs-it-dozer`).

A skipped test still shows as passed (Rust has no runtime skip), so a plain
`cargo test --workspace` is no evidence that the live suite ran. Set
`SEOCHOD_IT_REQUIRE=1` to make every live test fail instead of skipping when
the Bolt variables are missing.

```sh
export SEOCHOD_IT_BOLT_URI=bolt://127.0.0.1:7687 SEOCHOD_IT_BOLT_PASSWORD=<password>
SEOCHOD_IT_REQUIRE=1 cargo test --offline -p seochod --test bolt_it
# per-item vs UNWIND latency, JSON lines:
cargo test --offline -p seochod --test bolt_it -- --ignored --nocapture bench
```
