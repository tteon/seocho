# seocho-lifecycled

SEOCHO lifecycle authority daemon. PR-1 ships only the read-only `audit`
subcommand. `serve`, `migrate` and `downgrade` come in P2 (design §7).

```
seocho-lifecycled audit <db> [--json] [--strict] [--now-ms <ms>]
```

`audit` opens `<db>` as `file:<db>?mode=ro`, detects whether it is v0
(Python main), v1 (#783, with `ontology_fence_high_water`) or v2 (this
crate), and reports what it finds.

| kind | severity |
|---|---|
| `duplicate_token`: one token on two or more lease/tombstone rows of a scope | violation |
| `high_water_below_lease`: high-water missing or below a lease token (v1/v2) | violation |
| `multiple_live_leases`: two or more live leases for one `(scope, purpose)` | violation |
| `schema_tampered`: v2 DDL digest mismatch | violation |
| `high_water_below_pointer` | violation on v2, warning on v1 (normal right after a v1 `activate`) |
| `pointer_token_reused`: pointer token equals a lease token | violation on v2, warning on v0/v1 (caller-chosen publish tokens) |
| `expired_but_held`: an expired row is still in `ontology_lease` | warning |
| `no_high_water_table` (v0): released tokens can't be audited | warning |

Exit codes: `0` means clean; `1` means violations were found (with
`--strict`, warnings also count); `2` means a usage error or a database that
can't be read.

## Read-only behaviour

The audit never writes the database or its WAL content. On a WAL-mode
database with no live connection, SQLite may still create the `<db>-shm`
index and an empty `<db>-wal` file, because a reader needs them. To audit a
database inside a checkout you must not touch, audit a copy.

## Deviation from the design

Design §9 lists "expired held row" as a violation. Here it is a **warning**.
An expired row authorises nothing: legacy `admission()`, seochod and the v2
`acquire` all treat it as dead. It is housekeeping debt for `migrate`, not a
broken token invariant. Pass `--strict` to make warnings fail the run.

The P1 exit gate ("운영 DB에서 audit 위반 0건 ... expired-but-held", design §9
P1 row) is therefore run as `seocho-lifecycled audit --strict <db>`; the design
row was amended to say so.

## Gate

```
cargo clippy -p seocho-lifecycled --all-targets --offline -- -D warnings
cargo test -p seocho-lifecycled --offline
```
