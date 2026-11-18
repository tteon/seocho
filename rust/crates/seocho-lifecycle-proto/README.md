# seocho-lifecycle-proto

Wire types for the SEOCHO lifecycle authority, protocol `seocho.lifecycle.v1`
(design: `docs/design/rust-lifecycle-authority.md` §4.3).

- `ErrorCode`: the stable `snake_case` error codes.
- `ObservedToken`: a fencing token as a verifier sees it. You can compare it,
  but you can't convert it into the authority's `seocho_lifecycle::FencingToken`.
- `Capability`: the Python admission dict `{lease_id, fingerprint, generation,
  epoch, fencing_token}` plus the optional `purpose` and `expires_at_ms` that
  `admit` adds. A capability without `purpose` is not a projection
  capability, so it fails closed.

This is the only lifecycle crate that a DozerDB writer (`seochod`) may depend
on (invariant I11). It has no way to mint a token.

Golden fixtures: `contract/v1/*.json`. Python will replay the same files, so
treat any change to them as a wire break.

## Deviations from the design

- `ErrorCode` adds `invalid_request` and `no_active`. §4.3 uses
  `invalid_request` for socket dispatch but leaves it out of the code list.
  `no_active` covers `acquire` on a scope with no active pointer, which the
  legacy store reports as `ValueError("no active ontology ...")`.

## Gate

```
cargo clippy -p seocho-lifecycle-proto --all-targets --offline -- -D warnings
cargo test -p seocho-lifecycle-proto --offline
```
