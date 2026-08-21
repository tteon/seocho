# Ontology RDF Read Model

SEOCHO declares an ontology once in JSON-LD. This is the authored SDK format:
it preserves SEOCHO node, relationship, property, alias, and governance
metadata. Do not independently edit the derived RDF files.

`build_rdf_ontology_bundle()` produces a portable bundle:

It also writes purpose-specific, content-addressed agent profiles; see
[`docs/ontology/AGENT_ONTOLOGY_PROFILES.md`](ontology/AGENT_ONTOLOGY_PROFILES.md).

- `ontology.jsonld` — canonical SEOCHO declaration.
- `ontology.ttl` — derived OWL/RDFS/SKOS vocabulary for Oxigraph and Neo4j
  n10s import.
- `shapes.ttl` — derived SHACL shapes for offline validation.
- `manifest.json` — file hashes plus the bundle SHA-256 identity.

The Rust `dataplane/oxigraph_read_model` daemon loads `ontology.ttl` into an
in-memory Oxigraph store and listens only on a Unix-domain socket. It accepts
newline-delimited JSON requests for `health` and `term`. Its output is
ontology evidence, never a graph write or an inferred fact.

Configure an optional runtime read model per registered graph:

```json
[
  {
    "graph_id": "finance",
    "database": "finance",
    "ontology_path": "finance.jsonld",
    "workspace_id": "default",
    "profile": "default",
    "oxigraph_socket": "/run/seocho/finance-ontology.sock"
  }
]
```

If the socket is absent or unavailable, the runtime logs the condition and
continues without this additional evidence. `workspace_id` and the ontology
context hash travel with each request as audit receipts; policy enforcement
remains in SEOCHO's Python control plane.

Oxigraph is not a SHACL validator or an OWL reasoner in this design. Run
pySHACL and any chosen inference engine offline against the same versioned
bundle, then promote only results that include the source bundle digest and a
derivation receipt. This prevents inferred triples from silently bypassing
the ontology guardrail.

Run the offline gate with an instance data graph:

```bash
seocho ontology rdf-governance \
  --bundle outputs/ontology-bundle \
  --data examples/data.ttl \
  --output outputs/ontology-bundle/governance-receipt.json
```

The receipt is promotable only after bundle hashes verify and pySHACL actually
runs and conforms. `--run-reasoner` adds the optional Owlready2/Pellet
consistency check. A missing optional reasoner is recorded but does not turn a
successful SHACL result into a failure; a proven ontology inconsistency does.
