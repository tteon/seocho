# Vendored neo4j 0.2.0 (Apache-2.0, Copyright Rouven Bauer)

Unmodified copy of the crates.io `neo4j` 0.2.0 package, wired in through
`[patch.crates-io]` in the workspace `Cargo.toml`, with one local change:

- `src/driver/io/bolt/handshake.rs`: `TcpConnector::connect` and
  `connect_timeout` call `TcpStream::set_nodelay(true)`. Upstream never sets
  TCP_NODELAY, so every Bolt message larger than the driver's 8 KiB
  `BufWriter` stalls about 40 ms on Nagle plus delayed ACK. Measured against
  DozerDB 5.26: 1k nodes with 12 KB properties took 84.9 s per item without
  the patch and 0.66 s with it.

The `authors` entry in `Cargo.toml` and `Cargo.toml.orig` keeps the author's
name but drops the contact address, because this public repository only
allows allowlisted addresses in tracked files (see
`scripts/ci/check-identity-contract.sh`). The address is published with the
crate on crates.io. This is a metadata-only change.

There is no public hook to set the option and the workspace forbids
`unsafe`, hence the vendored copy. Drop it once upstream sets TCP_NODELAY
(re-check before moving to 0.3).
