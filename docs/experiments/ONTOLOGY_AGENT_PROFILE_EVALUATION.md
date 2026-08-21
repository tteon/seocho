# Ontology file and agent-profile evaluation protocol

This protocol evaluates the same governed ontology in three representations:

1. Canonical JSON-LD: complete semantic source of truth.
2. Derived Turtle/SHACL: Oxigraph and SHACL/RDF interoperability form.
3. Purpose-specific agent profile: minimal indexing, query, or projection JSON.

For each representation, record bytes on disk, cold parse/load time, agent
prompt tokens, extraction schema-validity, query evidence coverage, and
canonical projection receipt completeness. Run every condition against the
same documents, ontology bundle digest, MARA model, DozerDB instance, and
workspace-isolated graph. A profile is an optimization only if it preserves
the governed bundle hash and does not reduce SHACL conformance or evidence
coverage relative to canonical JSON-LD.

OS measurements include Unix-socket request size, Rust daemon wall time,
Bolt round trips, process RSS, and file lifecycle operations. Agent
measurements include profile token count, response latency, valid extraction
rate, relation recall, and missing-slot rate. Each arm also records its
execution runtime: `seocho_direct` for the deterministic `ask_response()` path,
or `agents_sdk` only when it actually invokes an OpenAI Agents SDK `Runner`
over SEOCHO-owned tools. An agent object that was merely constructed is not
evidence of an Agents-SDK experiment.

Store content-free JSONL trace evidence and immutable run metadata outside the
repository; promote only aggregate, reproducible evidence into a dated report.
JSONL is a portable trace artifact, not a metrics database or an observability
stack. The live stack is OTLP Collector, Tempo, Prometheus, and Grafana; it
must be able to retrieve the run's root trace before a live result is accepted.
Receipt/digest/source-span detail belongs in trace/run artifacts, while metric
labels remain bounded and exclude paths, workspace IDs, request IDs, digests,
and source text.

The filesystem lifecycle is immutable: write a new content-addressed bundle
directory, validate it offline, then atomically update a `current` pointer.
Agents read a fixed version directory and projection receipts name its hashes.
Rollback therefore changes the pointer, never rewrites a live ontology file.
