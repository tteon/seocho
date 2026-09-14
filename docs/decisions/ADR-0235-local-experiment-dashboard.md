# ADR-0235: Local experiment dashboard over saved evidence

- Status: Accepted
- Date: 2026-09-14
- Extends: ADR-0230, ADR-0233

## Context

The experiment platform can save, compare and render individual runs. Users need
one place to find runs, inspect failures and compare a baseline to a candidate.
The existing evaluation app proxies live chat, and the inference cost workbench
reads request-accounting artifacts. Neither is a catalog of saved E2E runs.

## Decision

Add `seocho runs dashboard` as a loopback-only, read-only local application.
An explicit set of directories provides `report.json` artifacts. A bounded
catalog validates and snapshots them; the HTTP layer exposes opaque catalog IDs,
never arbitrary filesystem reads. Refresh is explicit and preserves original
files. Corrupt, oversized and unsupported artifacts appear as scan diagnostics.

Keep catalog, HTTP transport and packaged browser assets in `seocho.dashboard`.
Reuse `query_state`, `compare_runs` and `render_run_view`. The dashboard supports
run search and status/workspace/model filters, question evidence, diagnostics,
recorded phase durations and condition-aware comparison. No aggregate answer
quality or performance claim is inferred across unmatched runs. Missing cost,
trace spans and timings stay unmeasured. A phase summary is not a trace waterfall.

Use Opik's list-to-detail-to-comparison workflow as product inspiration. This
does not change ADR-0172 or install a vendor integration. The application uses
packaged assets without external fonts, scripts, APIs or a frontend build chain.
No model invocation, graph mutation or paid judging is launched from this UI.

## Consequences

Users can inspect their own runs without a telemetry service or new database.
Loopback binding, Host/origin validation, no CORS, escaped DOM construction and
a restrictive content security policy protect the local reading surface.
There is no shared-user authentication or authorization layer; team hosting
requires a separate design and must not be enabled by changing the bind address.
The served artifacts contain user data, including questions and evidence.
Catalog budgets keep resource use bounded and report omissions explicitly.
The request-accounting workbench and live chat app retain their existing roles.
