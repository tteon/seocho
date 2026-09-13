# ADR-0233: Versioned experiment conditions and local visual inspection

- Status: Accepted
- Date: 2026-09-13
- Extends: ADR-0230
- Related: ADR-0172 (retired tracing integration)

## Context

A saved-run comparison could accept identical YAML and Python sources while
runtime environment switches changed model routing or query behavior. Interrupted
query phases lost completed answers, and persisted preflight errors could contain
URI credentials. These boundaries need a precise contract before visualizing them.

## Decision

Version input evidence as `seocho.run_evidence.v2`. Add a `runtime_settings`
fingerprint over explicit behavioral environment settings, including the contents
of explicitly configured immutable hint/admission files. Keep secrets out of the
setting allowlist and store only a combined digest. Unset and explicitly set
values can differ conservatively even when a runtime default makes them equivalent.
The allowlist is maintained with environment-driven SDK behavior; it does not
certify unknown external libraries or mutable graph/cache contents. v1 evidence
cannot certify this additional condition and remains diagnostic-only.

Checkpoint each completed question and the identity of the active question in
both direct and Agents SDK paths. Retain failed/degraded extraction signals during
file aggregation; success caching requires a clean result. Sanitize endpoint
credentials and known secret values in diagnostic text before persistence or
presentation. Original connection credentials are still used only for connection.

Extend `seocho runs` with `view`, exporting one standalone HTML file. It renders
saved status, diagnostics, question evidence, optional matched baseline comparison,
and an architectural module map. The map describes ownership, not an observed
call graph. Stage/module timings are never invented. Missing telemetry remains
unavailable, and comparison uses the same pure function as the existing CLI.

HTML has no remote assets or uploads. All artifact content is escaped and a
restrictive content security policy allows only the fixed filtering script.
Rendering refuses to overwrite an existing file and does not rewrite receipts.
The existing evaluation web app remains the live chat surface.

## Consequences

Users can inspect experiments without a database, model key, browser account,
third-party telemetry SDK, or running web server. Per-question writes add local
filesystem overhead; these changes make no throughput or answer-quality claim.
An exported HTML file contains the selected report's content and must be treated
like that report when sharing. Offline inspection is not a live integration gate.

Opik is excluded from supported third-party integrations. Remaining setup flags,
optional imports, notebook calls and current recommendations are removed under
ADR-0172. Historical decisions and original experiment provenance remain intact.
