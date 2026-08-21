# Ontology serialization and agent-context candidate survey

This document records candidates for a measured design decision. It does not
select JSON-LD, Turtle, TriG, or a binary format in advance.

## Design invariant

The persistent identity of an ontology is its normalized RDF dataset, not the
bytes of any serializer. A build must canonicalize the RDF dataset with
RDFC-1.0, emit canonical N-Quads, and calculate:

    semantic_graph_sha256 = SHA-256(canonical.nq)

The manifest records that identity beside byte digests for every source and
derived artifact. Consequently, semantically equivalent JSON-LD and Turtle
serializations share one semantic identity but remain independently auditable
as source files. Canonicalization must have triple/blank-node/resource limits
because adversarial blank-node graphs can make canonicalization expensive.

## Candidate layers

| Layer | Candidates | Decision role |
|---|---|---|
| Human authoring and review | compact JSON-LD, Turtle, TriG | Compare, do not preselect. TriG is attractive when ontology, candidate, approved, and provenance named graphs need one source document. |
| Semantic identity and diff | canonical N-Quads | Required non-agent artifact. It is deterministic, line-oriented, hashable, and names graphs. |
| RDF validation/read | source RDF plus SHACL; rebuildable Oxigraph store | JSON-LD and Turtle are valid RDF inputs. Oxigraph is a read cache, never the portable source of truth. |
| Agent contract | fixed-schema compact JSON ontology IR | Candidate default for agents only after outcome testing; it is not an RDF replacement. |
| Agent task context | purpose profile plus deterministic adjacency slice | Give each agent only complete task-relevant types, predicates, slots, constraints, and provenance rules. |
| Answer context | typed evidence retrieval pack | Give answer synthesis selected facts, source spans, missing-slot reasons, and receipts rather than the full ontology. |
| Transport/archive | zstd pack export; later CBOR/MessagePack or HDT research | Never feed binary format directly to an LLM. Only investigate after a measured wire/archive bottleneck. |

## Candidate shortlist

### S1: compact JSON-LD source

JSON-compatible authoring and tool integration. A manifest must pin an
in-bundle context file, its digest, and JSON-LD processing mode; remote mutable
contexts are forbidden for locked artifacts.

### S2: Turtle source

Compact, review-friendly RDF graph source. Keep it as an equal authoring arm;
it does not automatically make an LLM better at relation direction or SHACL
constraints.

### S3: TriG source

Candidate when named graphs are first-class: ontology, candidate extraction,
approved graph, provenance, and validation report can remain separate without
losing dataset identity. Compare its authoring/diff ergonomics against S1/S2.

### S4: canonical N-Quads

Required identity, signature, reproducible diff, and receipt substrate. It is
not an agent prompt candidate because line-oriented fully-qualified IRIs are
token-expensive.

### S5: typed ontology IR JSON

A non-RDF, fixed-schema agent contract derived from the normalized graph. It
contains stable IDs, aliases, relation direction/domain/range, cardinality,
required slots, provenance rules, forbidden patterns, and a bounded example
set. It must carry the semantic/profile digests and never invent semantics
absent from S1-S4.

### S6: purpose profile plus adjacency slice

The primary agent optimization candidate. Start from seed types, requested
slots, and permitted relations; take directed one-hop closure; expand required
relations to two hops; then add SHACL-required provenance/cardinality. Sort by
stable identifier. If a token budget is exceeded, use weighted set cover with
`task frequency × error severity × retrieval utility`, but never remove a
constraint needed by a required slot.

### Deferred candidates

HDT is an offline large immutable snapshot/read-acceleration candidate, not a
mutable bundle format. CBOR-LD and other binary RDF encodings are wire/storage
research candidates only: they lack a current native SEOCHO validation/read
path and are not LLM-readable. RDF/LD patch belongs to an append-only delta
journal after snapshot identity is correct, not to the first bundle format.

## Portable artifact and control plane

    .seocho/ontology/
      bundles/<semantic-graph-sha256>/
        manifest.jcs.json
        source.<jsonld|ttl|trig>
        derived/canonical.nq
        derived/shapes.<ttl|jsonld>
        agent-profiles/indexing.jcs.json
        agent-profiles/query.jcs.json
        agent-profiles/projection.jcs.json
        governance-receipt.jcs.json
      state.sqlite

`manifest.jcs.json` declares source MIME type, parser and derivation versions,
semantic digest, byte digests, profile digests, SHACL receipt, and parent
identity. JCS is used for the JSON manifest/profile byte identity; RDFC-1.0 is
used for RDF semantic identity.

Reuse existing `ActiveOntologyPointer`, `VersionPinRegistry`, and
`SafeReclamationGate` as the local SQLite WAL control plane rather than adding
a second lock system. There are three distinct mechanisms:

1. A read pin holds one immutable `(workspace, purpose, semantic digest,
   profile digest, generation)` tuple for an agent run; it never serializes
   readers.
2. Activation CAS briefly serializes only `current` pointer changes and creates
   a new generation/epoch/fencing token.
3. A writer lease authorizes publish/projection, has owner and TTL, and carries
   the fencing token to the Rust daemon. A stale writer is rejected.

The Rust pool is a bounded weighted LRU keyed by:

    (workspace_id, semantic_graph_sha256, profile_sha256, purpose, profile_schema_version)

Its value is verified bytes plus parsed profile/adjacency index in an `Arc`.
Only unpinned entries may be evicted. Every UDS response returns the pinned
digest, profile, generation, and fence. `mmap`/FST sidecars are deferred until
the compact JSON parse cache is proven to be a bottleneck.

## Agent workload inputs

Each 40-60 case gold workload contains raw source text, gold RDF triples,
source spans, mandatory slots, allowed types/relations, expected answer or
correct abstention, and labelled invalid candidates. Use:

- 20 extraction cases: alias resolution, direction, domain/range, provenance.
- 15 retrieval cases: inverse and multi-hop relations, version-specific terms.
- 10 answer cases: grounded answers and required abstentions.
- 10 adversarial cases: syntax noise, dense irrelevant graph, source-free fact.
- 5 lifecycle cases: publish, activate, rollback, crash under 1/4/16 readers.

Compare S1, S2, S3, S5, and S6 with the same model, temperature, prompt
envelope, normalized RDF graph, workspace, documents, and retrieval results.
S4 verifies semantic equivalence only and is not given to an agent.

## Adoption gates

A source serialization is acceptable only if its canonical digest and SHACL
outcome equal the other serialization arms. S5/S6 are adopted only when triple
F1 and required-slot/provenance recall are non-inferior within 2 points, while
token count, p95 latency, or aggregate RSS improves by a pre-registered
threshold. Any cross-workspace leak, mixed generation, stale-fence projection,
or torn manifest/profile/receipt tuple is a hard failure. Run 10,000
publish/read/rollback/crash operations before claiming CLI lock correctness.

## Sources

- W3C, [RDF Dataset Canonicalization](https://www.w3.org/TR/rdf-canon/).
- W3C, [JSON-LD 1.1](https://www.w3.org/TR/json-ld11/).
- W3C, [RDF 1.2 TriG](https://www.w3.org/TR/rdf12-trig/) and [RDF 1.2 N-Quads](https://www.w3.org/TR/rdf12-n-quads/).
- [Oxigraph](https://github.com/oxigraph/oxigraph) and its [architecture notes](https://github.com/oxigraph/oxigraph/wiki/Architecture).
- [SQLite WAL](https://www.sqlite.org/wal.html) and [RFC 8785 JSON Canonicalization Scheme](https://www.rfc-editor.org/rfc/rfc8785).
