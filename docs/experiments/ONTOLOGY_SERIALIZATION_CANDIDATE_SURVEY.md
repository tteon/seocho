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
`SafeReclamationGate` as the first local control-plane implementation rather
than adding a second lock system. SQLite WAL is not an ontology store, an agent
pool, or a mandatory deployment dependency: it is one durable same-host CAS
backend for activation/lease/fence metadata. The control-plane interface must
remain backend-neutral so a later etcd or PostgreSQL adapter can serve
multi-host ownership without changing bundle/profile contracts. A filesystem
lock is a useful single-host publish guard but is not sufficient as the source
of truth for stale-writer fencing. There are three distinct mechanisms:

1. A read pin holds one immutable `(workspace, purpose, semantic digest,
   profile digest, generation)` tuple for an agent run; it never serializes
   readers.
2. Activation CAS briefly serializes only `current` pointer changes and creates
   a new generation/epoch/fencing token.
3. A writer lease authorizes publish/projection, has owner and TTL, and carries
   the fencing token to the Rust daemon. A stale writer is rejected.

For a one-process CLI, an in-memory implementation is acceptable for
development only and must report `durability=ephemeral`; it cannot be used as
evidence for crash recovery. For one host with multiple CLI/daemon processes,
the existing SQLite backend is the simplest correct starting point. For
multiple hosts, use an external linearizable CAS backend rather than a shared
SQLite file on network storage.

The Rust pool is a bounded weighted LRU keyed by:

    (workspace_id, semantic_graph_sha256, profile_sha256, purpose, profile_schema_version)

Its value is verified bytes plus parsed profile/adjacency index in an `Arc`.
Only unpinned entries may be evicted. Every UDS response returns the pinned
digest, profile, generation, and fence. `mmap`/FST sidecars are deferred until
the compact JSON parse cache is proven to be a bottleneck.

## Context engineering: what is sent to an agent

Serialization selection and context delivery are separate decisions. JSON-LD,
Turtle, and TriG remain RDF-compatible ontology artifacts, but none should be
treated as the normal LLM payload merely because it is the source file. The
agent's context is finite and includes system instructions, tools, history,
memory, and retrieved data. SEOCHO therefore treats the ontology bundle as a
receipt-pinned external semantic memory with progressive disclosure.

| Context arm | Initial agent context | Subsequent retrieval | Intended use |
|---|---|---|---|
| J0 | Entire source RDF serialization | None | Deliberately costly baseline. |
| J1 | Complete compact purpose profile | None | Static-profile baseline. |
| J2 | Task kernel, small profile summary, digest/lock identity, four ontology tools | Deterministic stage router loads bounded profile/slice | Default production candidate. |
| J3 | Same as J2 | Agent chooses bounded expansions | Test whether autonomy pays for navigation cost. |
| J4 | Typed evidence pack, source spans, missing slots, receipt | Optional evidence refinement only | Answer synthesis; ontology source remains out of context. |

The task kernel contains no filesystem path and no arbitrary graph traversal
authority. It contains the purpose, token/tool-call budget, requested slots,
allowed vocabulary summary, and the immutable `(workspace, semantic digest,
profile digest, generation, fence)` tuple. The tuple is also carried by every
tool request/response so a slice cannot silently cross an ontology activation.

Expose exactly four normal-task tools:

1. `ontology.profile`: bounded schema/profile summary for a declared purpose.
2. `ontology.slice`: task- and slot-scoped semantic closure; returns a stable
   handle, relevant terms/triples/constraints, provenance rules, and a declared
   truncation or insufficiency result.
3. `ontology.constraint`: targeted domain/range/cardinality/SHACL rule lookup
   for already-known terms, not free-form ontology dumping.
4. `ontology.evidence-pack`: selected facts, source spans, required/missing
   slots, and provenance receipt for answer synthesis.

`bundle diff`, activation, lease administration, rollback, and filesystem
inspection are curator/CLI capabilities, not ordinary agent tools. The CLI may
surface logical namespace, purpose, recency, and estimated result size to make
the four tool choices legible without exposing host-specific paths.

For long-running work, persist a bounded `run-note` JSON document separately
from the conversation. It stores the pinned tuple, task/decision state,
retrieved handles, confirmed source-grounded facts, unresolved slots, and
rejected candidates with reasons. Reload this note after compaction; do not
replay unbounded historical tool output. It is an auditable memory index, not
an alternate source of ontology truth.

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
S4 verifies semantic equivalence only and is not given to an agent. Then
compare J0-J4 independently with identical task cases and semantic identity.
Record initial and total context tokens, tool calls, no-result/insufficient
responses, tool-selection errors, retrieval turns, slice/evidence size,
context-assembly latency, and post-compaction outcome. Context arms pass only
when gold task metrics are non-inferior to J0 while their declared token or
navigation-cost gate improves.

## Single-host multi-model versioning workload

Run three independent worker processes through the same local control plane:

| Worker | MARA model |
|---|---|
| `worker-minimax` | `MiniMax-M2.7` |
| `worker-gptoss` | `gpt-oss-120b` |
| `worker-gemma` | `gemma-4-31B-it` |

Workers do not share mutable agent state. Each pins a complete ontology tuple
before prompt assembly: semantic/profile digest, generation, epoch, and
fencing token. First send identical gold cases under generation N and report
model-specific triples, direction/type errors, required-slot/provenance recall,
SHACL, repair count, tokens/latency, and answer/abstention accuracy. Then
activate N+1 while workers still hold N pins. N candidates retain N receipts;
they must be revalidated to receive an N+1 admission and can never project with
an N+1 identity by accident. Finally roll back to N and verify fresh pins.

The workload makes lock value measurable even on one host: different models
produce different plausible semantic graphs, while concurrent version changes
would otherwise let one model's result be written under another ontology.

## Adoption gates

A source serialization is acceptable only if its canonical digest and SHACL
outcome equal the other serialization arms. S5/S6 are adopted only when triple
F1 and required-slot/provenance recall are non-inferior within 2 points, while
token count, p95 latency, or aggregate RSS improves by a pre-registered
threshold. Any cross-workspace leak, mixed generation, stale-fence projection,
or torn manifest/profile/receipt tuple is a hard failure. Run 10,000
publish/read/rollback/crash operations before claiming CLI lock correctness.
For the three-model workload, a missing `(model, semantic digest, profile
digest, generation, fencing token)` receipt, cross-model profile leakage, or
stale-fence projection is also a hard failure.

## Sources

- W3C, [RDF Dataset Canonicalization](https://www.w3.org/TR/rdf-canon/).
- W3C, [JSON-LD 1.1](https://www.w3.org/TR/json-ld11/).
- W3C, [RDF 1.2 TriG](https://www.w3.org/TR/rdf12-trig/) and [RDF 1.2 N-Quads](https://www.w3.org/TR/rdf12-n-quads/).
- [Oxigraph](https://github.com/oxigraph/oxigraph) and its [architecture notes](https://github.com/oxigraph/oxigraph/wiki/Architecture).
- [SQLite WAL](https://www.sqlite.org/wal.html) and [RFC 8785 JSON Canonicalization Scheme](https://www.rfc-editor.org/rfc/rfc8785).
