# ADR-0234: Python implementation ownership and stat-based file tracking

- Status: Accepted
- Date: 2026-09-13
- Related: ADR-0172, ADR-0230, ADR-0233

## Context

A Python audit found a dataclass field/property collision that prevents default
execution-result JSON serialization, duplicate DataHub tag normalization that
loses namespaced approval tags, and runtime tools that redefine imported helpers
with a different schema source. Repeated semantic helpers and large facade/parser
functions also make maintenance depend on updating several unrelated locations.

## Decision

Keep one stored `ExecutionResult.agent_pattern` mapping. An empty mapping is
initialized from a dictionary in `answer_envelope` at construction; a non-empty
explicit mapping wins. It is a snapshot, not a property that follows later
envelope mutation. Keep both the constructor argument and serialized field.

Keep DataHub dataset tag display (name or full source URN) separate from glossary
approval normalization. Glossary normalization removes only `urn:li:tag:` from
a fallback URN, preserving names such as `seocho:approved`.

`runtime/server_runtime.py` owns database discovery, JSON graph descriptors and
registered-DB schema retrieval. `agent_server.py` exposes those implementations
through its existing tools; it does not read fixed schema files or invent label
hints when a file is missing. Unknown database names are rejected before opening
a connector. Existing workspace and query-policy boundaries remain in place.

Canonical ontology hints, semantic profile packages, approved vocabulary lookup,
artifact reading, SHACL candidate merging and shared row scoring belong to SDK
modules. Extraction imports remain compatibility adapters. Preserve differences
in artifact-store access and company matching instead of treating similar-looking
functions as semantically interchangeable.

Separate SDK execution-plan construction and async delegates from the sync
facade. Preserve `seocho.client` imports, type-checker visibility and lazy backend
loading. Move self-contained CLI commands into modules owning both parser and
handler; reuse CommandGroup. Split indexing chunk preparation and telemetry from
document orchestration, preserving callbacks, validation, provenance and writes.
Experiment phases consume narrow index/query Protocols; the composition root
continues to construct concrete clients. Provider dispatch reuses specialized
backend classes and operator-selected model defaults.

File tracking remains an mtime/size cache. State version 2 explicitly records
`change_detection: mtime_size`; version 1 remains readable. Legacy content_hash
values and the optional mark_indexed argument remain compatible, but new indexing
does not reread entire files to calculate an unused normalized-text hash. Source
and experiment content fingerprints are separate contracts and remain unchanged.

## Consequences

Maintainers can change shared behavior in one owner while existing callers keep
their import paths. Public results are JSON data, and namespaced approval tags
survive fallback normalization. Product-wide Ruff E9/F checks and regression tests
join basic CI to catch unused/undefined names and accidental redefinitions.

File tracking performs no extra content read after ingestion. Edits preserving
both mtime and size are outside this cache's contract: use force or no-track.
This does not claim streaming ingestion, byte-level cache validation, improved
answer quality, or live backend compatibility. Optional connectors/providers,
registered factories and historical experiment/ADR evidence are retained.
