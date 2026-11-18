# Oxigraph ontology read model

This Rust daemon loads a derived `ontology.ttl` artifact into Oxigraph and exposes bounded ontology-term reads over a Unix-domain socket. It is an optional read model: DozerDB remains SEOCHO's canonical graph write/Cypher store.

Create `config.json` with `socket`, `turtle`, and `bundle_sha256`, then run:

    cargo run --release -p seocho-oxigraph-read-model -- config.json

The newline-delimited JSON protocol accepts `{"op":"health"}` and `{"op":"term","term":"Person","workspace_id":"default","ontology_context_hash":"..."}`. The caller owns tenancy/policy enforcement; the daemon only returns RDF vocabulary evidence.

Term lookup matches an exact subject URI or case-insensitive literal labels from
RDFS label and SKOS prefLabel/altLabel. Other predicate objects are not labels.
Lookups use indexed scans: one exact-subject probe when the term parses as an
IRI (with optional `<>`), plus one predicate-bound scan per label predicate.
Multiple matching subjects return `ambiguous_term`, never an arbitrary winner; a
subject matched both by URI and by label is a single candidate. Empty/missing
terms are rejected. The lookup examines at most 100,000 label quads (non-label
quads do not count); exhaustion is an error, not a negative lookup. `definition`
is the resolved subject's first `skos:definition` literal, falling back to the
first `rdfs:comment` literal (store order). Returned label/definition text is
truncated to 8 KiB. This is not a general SPARQL service.

Request framing is limited to 16 KiB including the newline, with a 100 ms total
read deadline. Responses are capped at 64 KiB with a write timeout. Processing
is serial; this is a bounded local sidecar, not a high-concurrency server.
Existing socket paths are never unlinked at startup: confirm that the previous
process has stopped, then remove only its stale socket before restarting.
`bundle_sha256` is a caller-supplied receipt; verify the bundle in the Python
control plane before launching, not by trusting this receipt as attestation.

Verification from the Rust workspace root (`rust/`):

    cargo test -p seocho-oxigraph-read-model --offline
    cargo build -p seocho-oxigraph-read-model --offline

Python-side checks that exercise the native binary take its path from
`SEOCHO_OXIGRAPH_BINARY`; from the repository root that is the binary built
above:

    SEOCHO_OXIGRAPH_BINARY=rust/target/debug/seocho-oxigraph-read-model
