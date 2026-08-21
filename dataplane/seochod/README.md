# seochod

`seochod` is SEOCHO's local Rust data-plane daemon. It owns a Unix-domain
socket and uses the Rust `neo4j` Bolt driver to project already-approved,
ontology-shaped nodes and relationships into DozerDB. It intentionally does
not accept arbitrary Cypher, raw documents, ontology reasoning, or LLM input.

Configure its Bolt credentials only through process environment variables:
`SEOCHOD_BOLT_HOST`, `SEOCHOD_BOLT_PORT`, `SEOCHOD_BOLT_USER`, and
`SEOCHOD_BOLT_PASSWORD`. Start it with `cargo run -- /tmp/seochod.sock`, then
set `SEOCHO_RUST_PROJECTOR_SOCKET=/tmp/seochod.sock` for the SEOCHO CLI.

The daemon fails closed: when the socket is configured, a failed projection is
returned as an indexing failure rather than silently falling back to Python.
