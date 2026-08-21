# Oxigraph ontology read model

This Rust daemon loads a derived `ontology.ttl` artifact into Oxigraph and exposes bounded ontology-term reads over a Unix-domain socket. It is an optional read model: DozerDB remains SEOCHO's canonical graph write/Cypher store.

Create `config.json` with `socket`, `turtle`, and `bundle_sha256`, then run:

    cargo run --release -- config.json

The newline-delimited JSON protocol accepts `{"op":"health"}` and `{"op":"term","term":"Person","workspace_id":"default","ontology_context_hash":"..."}`. The caller owns tenancy/policy enforcement; the daemon only returns RDF vocabulary evidence.
