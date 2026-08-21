# Add an Oxigraph ontology read-model sidecar

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This plan follows `.PLANS.md` from the repository root. Beads item: `seocho-ua6`.

## Purpose / Big Picture

SEOCHO users will be able to declare an ontology once in JSON-LD, generate a versioned RDF bundle, and serve its Turtle graph through a Rust Oxigraph process over a Unix-domain socket. Neo4j/DozerDB remains the canonical property-graph write and Cypher store. Oxigraph is a read model: an independently replaceable RDF representation used for ontology term, hierarchy, and vocabulary reads.

## Progress

- [x] (2026-08-21) Inspected the current JSON-LD, Turtle, SHACL, runtime-registry, and Rust/Bolt seams.
- [x] (2026-08-21) Chose JSON-LD source plus derived Turtle and SHACL Turtle bundle; chose Unix socket transport for the first OS-coupled process boundary.
- [x] (2026-08-21) Added bundle and Python Unix-socket client contracts.
- [x] (2026-08-21) Added the Rust Oxigraph sidecar and optional runtime manifest integration.
- [x] (2026-08-21) Added focused Python tests and ran a Rust type-check.
- [x] (2026-08-21) Documented the declaration bundle, optional runtime manifest, and SHACL/inference boundary.
- [x] (2026-08-21) Added a hash-pinned offline pySHACL gate and optional Owlready2/Pellet consistency receipt.

## Surprises & Discoveries

- Observation: JSON-LD is already the canonical SEOCHO persistence format and Turtle is already derived from it, but this relationship is not published as a versioned multi-consumer bundle.
  Evidence: `src/seocho/ontology/core.py` and `src/seocho/ontology/serialization.py`.
- Observation: Oxigraph supplies an RDF/SPARQL store, not SHACL validation or OWL reasoning. SHACL remains an offline `pyshacl` concern; the sidecar only makes RDF data available to an eventual validator/reasoner.
  Evidence: Oxigraph public API scope and `src/seocho/ontology/governance.py`.

## Decision Log

- Decision: Keep SEOCHO JSON-LD as the authored source and create `ontology.jsonld`, `ontology.ttl`, `shapes.ttl`, and a SHA-256 manifest as one derived bundle.
  Rationale: JSON-LD keeps the existing SDK contract stable; Turtle is the portable RDF exchange representation for Oxigraph and Neo4j n10s; SHACL is intentionally derived rather than a competing source of truth.
  Date/Author: 2026-08-21 / Codex
- Decision: Use a local Unix-domain socket and newline-delimited JSON protocol for the first sidecar.
  Rationale: It is an explicit OS IPC boundary, avoids exposing an unauthenticated TCP endpoint, and remains testable without Docker. The control plane retains timeout and payload bounds.
  Date/Author: 2026-08-21 / Codex
- Decision: Do not claim that Oxigraph performs SHACL validation or OWL inference.
  Rationale: The first slice serves versioned RDF. Offline pySHACL remains the validator; a future reasoner must return a versioned derivation receipt before its output enters a guardrail.
  Date/Author: 2026-08-21 / Codex

## Outcomes & Retrospective

The first slice is implemented. It leaves SHACL validation and reasoning out of
the daemon on purpose: they require an explicit engine and derivation receipt.
The offline gate now produces that receipt. The remaining promotion gate is a
supervised daemon integration test and live latency measurement against the
intended deployment shell.

## Context and Orientation

An ontology is the declared vocabulary of node types, relationship types, properties, aliases, and constraints. A read model is a query-optimized copy that is never allowed to become the canonical write authority. A Unix-domain socket is an OS-provided local IPC endpoint addressed by a filesystem path; it lets the Python control plane communicate with a separately supervised Rust process without using a network port.

`src/seocho/ontology/serialization.py` converts the current `Ontology` into Turtle. `src/seocho/ontology/governance.py` derives SHACL Turtle. `runtime/ontology_registry.py` compiles ontology contexts for runtime requests. `runtime/` and `src/seocho/` must remain compatible when the sidecar is absent.

## SEOCHO Evidence Contract

This slice does not alter answer selection. Each sidecar result carries `workspace_id`, `ontology_context_hash`, source bundle digest, term URI, requested term text, and an explicit `found` state. Absence means ontology evidence is unavailable; it must not be converted to a graph fact or an inferred answer.

## SEOCHO Review Panel

The professor lens selects RDF Turtle as exchange format because it preserves ontology vocabulary and hierarchy across RDF tools. The software-engineer lens requires one JSON-LD source, deterministic artifacts, bounded IPC messages, optional configuration, and no duplicated schema editing. The computer-systems lens selects a Unix socket sidecar because it provides a concrete local process boundary while retaining a synchronous, low-QPS ontology lookup path. The decision is falsified if bundle generation is non-deterministic, sidecar absence blocks runtime boot, or live latency shows the sidecar on the graph-query hot path.

## Cost, Latency, and Provider Policy

This work makes no LLM calls. DozerDB remains the canonical graph backend. Oxigraph is a local Rust dependency only. The Python client uses a short timeout and bounded response size; no result payloads are emitted as metric labels.

## Plan of Work

Add a Python `RdfOntologyBundle` builder that writes deterministic, content-addressed JSON-LD/Turtle/SHACL artifacts. Add an `OxigraphReadModelClient` that sends bounded JSON requests through a Unix socket. Add a small Rust binary under `dataplane/oxigraph_read_model` that loads one Turtle bundle into an Oxigraph named graph and handles health/reload/term requests. Extend the runtime manifest with an optional socket path and expose a registry lookup method without making runtime startup depend on the daemon.

## Concrete Steps

From the repository root:

    uv run pytest -q tests/seocho/test_ontology_rdf_bundle.py tests/seocho/test_oxigraph_read_model.py extraction/tests/test_ontology_registry_oxigraph.py
    cargo test --manifest-path dataplane/oxigraph_read_model/Cargo.toml
    bash scripts/ci/run_basic_ci.sh

## Validation and Acceptance

A focused test must prove that the same ontology yields the same manifest digest and that its bundle contains JSON-LD, Turtle, and SHACL Turtle. A local Unix-socket fake must prove timeout/error/response validation and a runtime manifest must prove sidecar configuration remains optional. Cargo tests must prove the sidecar returns a health response and an RDF vocabulary term from a loaded Turtle graph.

## Idempotence and Recovery

Bundle builds write only to caller-selected output directories and atomically replace individual artifact files. The sidecar removes only its configured Unix socket path on startup/shutdown; it never deletes ontology artifacts. If the daemon is unavailable, the registry logs the condition and returns `None` from the optional read-model lookup.

## Artifacts and Notes

The bundle manifest records artifact filenames and SHA-256 hashes. It is the handoff object to Neo4j n10s import, Oxigraph load, pySHACL validation, and a future inference job.

## Interfaces and Dependencies

`build_rdf_ontology_bundle(ontology, output_dir)` returns an immutable bundle descriptor. `OxigraphReadModelClient(socket_path).lookup_term(term, workspace_id, context_hash)` returns a typed optional response. The Rust protocol accepts `health`, `reload`, and `term` operations as one JSON object per line. `runtime/ontology_registry.py` accepts an optional `oxigraph_socket` manifest field and exposes an optional lookup method.

## Revision Notes

2026-08-21: Created for the first implementable Oxigraph read-model slice requested by the user.
2026-08-21: Updated after implementation; clarified that the first daemon is an
in-memory, bundle-loaded read model, not a SHACL/reasoning engine.
