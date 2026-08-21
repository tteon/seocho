# Agent ontology profiles

SEOCHO uses JSON-LD as the human-authored, versioned canonical ontology. It is
appropriate for this role because it carries stable RDF identifiers, JSON tool
compatibility, and can be deterministically derived to Turtle for Oxigraph and
SHACL. It is not the prompt payload that every agent should read in full.

`build_rdf_ontology_bundle()` creates three derived, content-addressed JSON
profiles under `agent-profiles/`:

- `indexing.json` gives an extractor only the permitted labels, relationships,
  and extraction rules.
- `query.json` gives a retrieval agent the query context and deterministic
  query profile.
- `projection.json` gives the Rust projection boundary the allowed graph types
  and mandatory provenance/workspace stamps.

Every profile records `canonical_bundle_sha256`, `ontology_context_hash`, and
its own `profile_sha256`. SEOCHO must select a profile by purpose and include
those receipts in candidate approval and projection logs. A profile can be
regenerated freely when prompt presentation changes; only a JSON-LD/Turtle/
SHACL change creates a new canonical bundle digest and requires governance.

The intended promotion path is:

    JSON-LD source -> Turtle/SHACL -> Oxigraph validation/reasoning ->
    governance receipt (promotable=true) -> approved candidate ->
    projection profile -> seochod Rust Bolt write -> DozerDB canonical graph

Do not send raw user documents, arbitrary Cypher, or a mutable ontology file
directly to `seochod`. Filesystem-managed bundles should be immutable version
directories, with a separate atomic `current` pointer controlled by the
SEOCHO CLI. This gives agents stable read snapshots and makes rollback a pointer
change rather than an in-place edit.
