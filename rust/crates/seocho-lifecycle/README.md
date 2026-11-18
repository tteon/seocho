# seocho-lifecycle

Library for the SEOCHO ontology lifecycle authority. This crate is PR-1 of
`docs/design/rust-lifecycle-authority.md` §9 and changes no production path.

It turns the #783 contract ("fencing tokens are strictly increasing and never
reused per `(workspace_id, package_id)`") from a comment into three layers of
enforcement:

| layer | where |
|---|---|
| type | `FencingToken` has a private field and no ctor/`From`/`Default`/`Deserialize`. The crate-private `WriteTxn::mint` (`src/fence.rs`) is its only source, and the `compile_fail` doctests pin this. |
| database | schema v2 triggers T1-T7 + M1 (`schema::DDL_V2`) run for every writer in every language. |
| tests | seeded model, legacy-writer replay (raw SQL and real Python), multi-process, audit fixtures |

## API

- `schema::{apply_v2, create_for_test, ddl_sha256, expected_ddl_sha256, verify_v2, detect}`
- `SqliteBackend::{create, open}` plus the `LifecycleBackend` trait: `acquire`, `release`, `activate`
- `Clock` (`SystemClock`, `ManualClock`). The effective time is
  `max(clock, lifecycle_meta.last_wall_ms)`, and it is written back in every
  write txn (I9).
- `audit::{audit_path, audit_connection, open_read_only}`: read-only
  (`file:...?mode=ro`) audit of v0/v1/v2 databases.

### Mint

```
token = max(high_water, MAX(live lease), MAX(tombstone), pointer) + 1
UPDATE ontology_fence_high_water SET high_water = token, minted_for = <label>
  (INSERT only when the scope has no row yet; never UPSERT/REPLACE)
```

`<label>` is the lease id for a lease, or `activate:<generation>:<epoch>` for
the pointer row that will carry the token. The write that carries the token
consumes the label (`minted_for = 'consumed:' || label`, by an AFTER trigger),
so one mint authorises exactly one lease row or one pointer write.

### Schema v2

The legacy tables keep their exact columns, so a legacy statement parses and
is then stopped by a trigger with a `lifecycle Tn: ...` message. v2 adds the
following:

- `lifecycle_meta(key, value)`: `schema_version`, `db_uuid`, `writer`,
  `ddl_sha256`, `migrated_from` and `last_wall_ms`. `PRAGMA user_version = 2`.
  `verify_v2` and every write txn require `last_wall_ms` to be present and
  decimal (`schema_tampered` otherwise, never a silent 0).
- `ontology_fence_high_water.minted_for` and `CHECK(high_water >= 0)`.
- Unique indexes on `ontology_lease(ws, pkg, purpose)` and `(ws, pkg, fencing_token)`.
- `ontology_lease_tombstone(..., ended_at_ms, end_reason IN ('released','expired'))`.
  `release` moves the row there, and `acquire` first moves any expired rows of
  the scope there.

| trigger | rule |
|---|---|
| M1 | `lifecycle_meta`: INSERT over an existing key, DELETE, and any UPDATE other than `last_wall_ms` moving forward to a decimal value are refused |
| T1 | high-water UPDATE must strictly increase (or only consume the label); the scope is immutable. INSERT is refused when the scope already has a row (this is what stops `INSERT OR REPLACE` and UPSERT) and must dominate every lease, tombstone and pointer token of the scope |
| T2 | high-water DELETE is refused |
| T3 | lease INSERT needs `hw.high_water = NEW.fencing_token AND hw.minted_for = NEW.lease_id` (an unconsumed label), a lease id not starting with `activate:`/`consumed:`, and a lease id not present in the tombstone; AFTER INSERT consumes the label |
| T4 | lease UPDATE may change only `expires_at_ms`; lease DELETE needs a matching tombstone row (same id, scope, token); tombstone DELETE (P2 prune) needs `hw >= token` |
| T5 | pointer UPDATE needs `epoch = OLD.epoch + 1`, the same generation, `token > OLD.token`, `token = hw` and `minted_for = 'activate:<gen>:<epoch>'`; AFTER UPDATE consumes the label |
| T6 | pointer INSERT needs `epoch = 0`, `generation = generation_hwm.hwm`, `token = hw` and an unconsumed `minted_for = 'activate:<gen>:0'`; AFTER INSERT consumes the label |
| T7 | `generation_hwm` must not decrease; DELETE is refused; INSERT is refused when the scope already has a row, and must be `>= 0` and `>=` the pointer generation |

**Why the insert-once triggers.** `INSERT OR REPLACE` resolves a key conflict
by deleting the old row *without* firing DELETE triggers (SQLite's default
`recursive_triggers = OFF`) and then inserting, so BEFORE UPDATE/DELETE
triggers alone can be bypassed (review r1 reproduced lowering the high-water
from 5 to 0 and `generation_hwm` to -5 that way). BEFORE INSERT fires before
conflict resolution, so refusing an INSERT over an existing key closes REPLACE
and UPSERT together. The authority writes both tables with UPDATE-then-INSERT.
Consequence for legacy writers: the #783 high-water UPSERT now fails closed
with `lifecycle T1` (before its lease INSERT is reached), and legacy `recreate`
fails with `lifecycle T7` at its generation UPSERT.

Not guarded in PR-1: `DELETE FROM active_ontology` (recreate with CAS is
P2), tombstone INSERT (it can only raise the mint floor) and
`projection_idempotency` (T8 moves with `idem_*` in P3). A deleted pointer
can't cause a token to be reused, because the next `activate` mints above the
durable high-water, which no statement can lower or remove. It can't be
re-inserted with other content under its old `(generation, epoch, token)`
either, because its activation label was consumed by the original write.
Legacy main `release()` (`DELETE FROM ontology_lease ...`, room
lifecycle.py:244) is refused by T4 on a v2 DB: legacy writers cannot acquire
on v2 anyway, and a release that skipped the tombstone would break the §4.3
move rule.

Every condition is written as `(<allowed>) IS NOT 1`, so a NULL in a legacy
nullable column aborts the statement instead of slipping through.

`ddl_sha256` hashes the whitespace-normalised `sqlite_master.sql` of every
object attached to an owned table. `open` and `verify_v2` compare it with the
digest of the compiled-in DDL, so they detect a dropped trigger and an added
one alike.

## Deviations from the design (and why)

1. **T5/T6 bind `minted_for` to `activate:<generation>:<epoch>`.** The design
   only asks for `LIKE 'activate:%'`. With the looser rule, a legacy `publish`
   issued right after an authority `activate` could reuse the pointer token,
   because `hw = pointer token` and the label still starts with `activate:`.
   That would publish a second pointer under one token. The test
   `legacy_publish_with_caller_chosen_token_is_aborted_by_t5` covers the case.
2. **T6 requires `generation = hwm` and `epoch = 0`.** The design says `<= hwm`.
   Every legitimate insert, whether a first publish or a recreate, uses exactly
   the freshly bumped high-water. A lower generation would let an old
   generation's epochs come back.
3. **T4 freezes every lease column except `expires_at_ms`.** The design only
   names `fencing_token`. If `purpose` could change, a `query` lease could be
   rewritten into a `projection` lease, which breaks I6.
4. **T1 is strict (`>`).** A same-value UPDATE that only rebinds `minted_for`
   would forge T3/T5 eligibility. The single exception is consuming the label
   (`'consumed:' || OLD.minted_for`, once), which removes eligibility rather
   than granting it.
5. **T3 also refuses lease ids found in the tombstone.** Otherwise a released
   lease could be re-inserted with its own id and token while the high-water
   still names it.
6. **The mint also folds in `MAX(tombstone)`.** This is defence in depth; in a
   healthy v2 DB the high-water already dominates it.
7. **`LifecycleBackend` carries domain operations, not the GAT `{Txn, write, read}` shape.**
   With the GAT shape, a public trait would expose the crate-private
   `WriteTxn`, which is what keeps minting crate-local.
8. **`sha2` (0.11.1, already in `Cargo.lock`) is used for `ddl_sha256`.** §3
   says "this design does not use sha2", but `ddl_sha256` needs SHA-256, and
   hand-rolling it would be worse.
9. **`apply_v2` refuses v0/v1 databases.** Upgrading one is `migrate` (P2),
   which needs a backup and a DozerDB floor first.
10. **Tests use `/dev/shm` when available** for the fsync-heavy model and
    multi-process tests. Production always sets `synchronous=FULL`, and the
    crate has no switch for a non-durable mode.
11. **Review r1 additions beyond the design's T1-T7.** Insert-once rules on
    the high-water and `generation_hwm` (REPLACE/UPSERT bypass), single-use
    mint labels (closes lease and pointer resurrection after a DELETE), the
    lease DELETE / tombstone DELETE guards, and M1 on `lifecycle_meta` (I9 and
    I13 depended on an unguarded table). Apart from the `consumed:` label
    prefix left on `minted_for`, none of them changes what the
    authority itself writes, only how (UPDATE-then-INSERT instead of UPSERT).

## Gate

```
cargo clippy -p seocho-lifecycle --all-targets --offline -- -D warnings
cargo test -p seocho-lifecycle --offline
# fail instead of skipping when python3 or the repository's src/ is missing;
# optionally also replay a pre-#783 (v0) Python checkout:
SEOCHO_LIFECYCLE_REQUIRE_PYTHON=1 [SEOCHO_LIFECYCLE_V0_PY_SRC=<v0>/src] \
  cargo test -p seocho-lifecycle --offline --test python_legacy
```
