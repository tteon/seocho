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

## Module-quality admission for agents

Each generated profile now also carries `module_quality`: a versioned
structural scorecard, threshold policy, and decision. The normal profile is
scored against the full ontology; a publisher can supply a narrower
`module_specs[purpose]` evaluation boundary with its classes, required
relationship interface, sibling modules, and threshold overrides. This
metadata does not silently narrow the profile vocabulary: a prompt-slice change
remains an explicit, separately reviewable context decision. The stored
measures include relative size, target-fit, intra/inter-module distance,
cohesion, coupling, redundancy, encapsulation, independence, attribute
richness, and inheritance richness.

The decision is a control-plane primitive, not a scalar “quality score”:

- `ready`: use the bounded profile.
- `needs_reasoning`: the agent must take the bounded extra verification steps
  stated in the gate, normally an `ontology_slice` for the interface terms.
- `reject`: withhold vocabulary and deny slices; select or repair an ontology
  version before graph work.

This does **not** claim OWL logical completeness. `source_subset_valid` checks
that declared classes and internal relationship contracts exist in the source;
`interface_complete` checks the caller-declared interface. Entailment
preservation, SHACL conformance, and promotion remain separate semantic
evidence recorded in the RDF governance receipt. This distinction keeps an
agent from treating a convenient small module as proof of semantic correctness.

The Agent SDK `ontology_profile` tool returns only the action-oriented gate and
profile digests to preserve context budget. The full numeric scorecard and
policy are immutable metadata in the profile artifact, linked by
`profile_sha256`; trace spans and the
`seocho.ontology.module.quality.decision.count` metric record each exposure.

The intended promotion path is:

    JSON-LD source -> Turtle/SHACL -> offline SHACL validation ->
    governance receipt (promotable=true) -> approved candidate ->
    projection profile -> seochod Rust Bolt write -> DozerDB canonical graph

Do not send raw user documents, arbitrary Cypher, or a mutable ontology file
directly to `seochod`. Filesystem-managed bundles should be immutable version
directories, with a separate atomic `current` pointer controlled by the
SEOCHO CLI. This gives agents stable read snapshots and makes rollback a pointer
change rather than an in-place edit.

For just-in-time context, use `seocho ontology context profile --bundle ...
--purpose query` for the fixed purpose profile, or `ontology context slice
--terms Account,TRANSFER --max-chars 4000` for a bounded, verified view. A
slice returns its canonical bundle and profile digests plus allowed vocabulary;
it never returns a host path or silently injects the entire ontology. This is
the CLI-level primitive that an Agents SDK tool should expose, rather than
placing a raw JSON-LD file into every prompt.

`build_graph_agent(..., ontology_bundle_dir=...)` wires the same two tools
(`ontology_profile`, `ontology_slice`) into a real OpenAI Agents SDK agent.
Without that explicit argument, the existing graph agent remains compatible and
does not claim agent-selected JIT context delivery.

Oxigraph is the bounded RDF read model in this flow; it is not the SHACL
validator. The current offline validation implementation is PySHACL. This
separation keeps expensive governance work out of the request and projection
hot paths.

For receipt-enforced Rust projection, set `SEOCHO_RDF_GOVERNANCE_RECEIPT` and
`SEOCHO_AGENT_ONTOLOGY_PROFILE` together in the SEOCHO process, and set
`SEOCHOD_REQUIRE_GOVERNANCE=1` in the daemon process. SEOCHO validates that the
profile derives from the promotable governed bundle; `seochod` stamps the four
resulting hashes on canonical nodes and relationships.

## Governed E2E contract

The run spec declares projection canonicality independently from extraction
enforcement:

```yaml
governance:
  mode: governed # direct | shadow | governed | lockdown
```

`governed` and `lockdown` fail preflight unless a valid projection receipt,
live lifecycle lease, and local `seochod` socket are present. The online
preflight also checks DozerDB and daemon health before an LLM request is sent.
Each E2E report and root experiment trace records the selected mode, receipt
hashes, lease ID, generation, epoch, and fencing token without recording raw
RDF or filesystem paths. `direct` and `shadow` reports explicitly set
`canonical_claim_allowed=false`; neither is evidence of a governed canonical
write.

The present CLI workflow intentionally keeps semantic validation offline:
produce an extracted candidate RDF graph, validate it with
`seocho ontology rdf-governance`, then use the resulting receipt for the
approved projection. A normal immediate LLM indexing run must not reuse a
receipt for a different candidate graph; candidate staging/promotion is the
required boundary before calling it a governed ingestion workflow.
