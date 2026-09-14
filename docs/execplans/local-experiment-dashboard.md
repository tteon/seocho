# Local experiment dashboard

## Purpose / Big Picture


Let a user browse saved SEOCHO experiments in a browser, find failures, inspect
question evidence and compare two runs under the existing evidence contract.
An artifact is a saved report.json. A baseline is the earlier run selected for
comparison; a candidate is the run being evaluated against it.

## Progress


- [x] Audit run views, inference workbench, live evaluation app and local memory.
- [x] Claim beads seocho-rw3q and isolate codex/experiment-dashboard.
- [x] Record the local application boundary in ADR-0235.
- [x] Implement bounded catalog, read-only HTTP API and packaged dashboard UI.
- [x] Validate real local HTTP/browser behavior and malformed receipt boundaries.
- [ ] Run basic CI, packaging and docs checks; publish and land the scoped PR.

## Surprises & Discoveries


PR #681 already supplies an inference cost workbench with a different input
schema. Reuse existing E2E comparison rather than invent a second cost ledger.
The current run viewer exports only one report at a time. User sharing preference
was requested; local operation is the initial assumption pending that answer.

## Decision Log


2026-09-14: Start with a read-only local catalog. Semantic lens: no pooled quality
scores or synthetic trace spans. Engineering lens: separate catalog, transport
and assets; reuse comparison and export. Systems lens: cap scan/report budgets,
snapshot receipts on refresh and avoid model/service calls. Real browser and
HTTP tests can falsify navigation, filtering and reading-boundary assumptions.

## Outcomes & Retrospective


The local dashboard is implemented with no new runtime dependencies. Full basic
CI passed: 1,222 tests passed, 5 skipped. Real loopback HTTP and Chrome checks
covered search, outcomes, diagnostics, conditions, safe text rendering, matched
and rejected comparison, result invalidation, sources and explicit refresh.
Desktop (1440px) and mobile (390px) screenshots were inspected. A browser check
found an absolutely positioned screen-reader label escaping the scrollable
table; positioning its container fixed mobile viewport expansion.

Docs contracts and the ADR index passed. The wheel contains all three browser
assets and their bytes match the source. No model, graph or paid calls were
made; synthetic browser fixtures only establish local UI behavior. Publishing
and landing remain in progress.

## Context and Orientation


`src/seocho/cli/runs.py` registers saved-run commands. `run_comparison.py` rejects
unmatched execution conditions. `run_visualization.py` renders a standalone
report. Add `src/seocho/dashboard/` for the catalog/server and browser assets.
`evaluation/` remains the live chat proxy; no runtime API changes are needed.

## SEOCHO Evidence Contract


Preserve original questions, answers, support, selected relation triples,
missing slots, diagnostics and workspace_id. Describe only recorded timings.
Use the same declared-change and hypothesis checks as the compare command.
Legacy reports remain viewable but do not acquire new evidence retroactively.

## SEOCHO Review Panel


Use the three written lenses in the Decision Log. No additional agents or model
calls are required. UI fixture data must be labelled synthetic and excluded from
claims about answer quality, throughput or live service compatibility.

## Cost, Latency, and Provider Policy


No new provider calls, graph queries, resources or paid experiments. Local HTTP
and browser execution validate the dashboard only. Preserve JSONL/OTLP contracts.

## Plan of Work


Implement catalog snapshots and summary models, then a small loopback HTTP server.
Build a searchable experiment table with run detail and baseline/candidate views.
Package all assets in wheels and register the CLI. Add regression tests, public
usage docs, and basic CI coverage. Verify with synthetic receipts on a real local
server and headless browser before landing.

## Concrete Steps


From a checkout with the dev extra installed:

    uv run seocho runs dashboard ./runs --port 8765
    uv run pytest tests/seocho/test_experiment_dashboard.py -q
    bash scripts/ci/run_basic_ci.sh

Open the printed localhost address, choose a run, inspect Questions/Diagnostics/
Conditions, and compare two runs with an explicit hypothesis when settings change.

## Validation and Acceptance


Catalog refresh discovers new valid runs and surfaces invalid files without
altering them. Search and filters select the expected rows. Completed query
evidence and interruption state survive display. Comparison agrees with the CLI,
including rejected comparisons. Untrusted text stays text. Unknown IDs, unsafe
Host/origin values and filesystem escapes are rejected. A wheel includes assets.

## Idempotence and Recovery


The server is read-only; stop with Ctrl+C. Refresh never rewrites a receipt.
Use the original run/view/compare commands independently. Preserve current main
and unrelated research checkouts during development and landing.

## Artifacts and Notes


Local test logs, screenshots and synthetic reports remain ignored under
`.seocho/validation/`. `browser_dashboard.py` runs the Chrome acceptance check;
`dashboard-desktop.png`, `dashboard-detail.png`, `dashboard-compare.png` and
`dashboard-mobile.png` capture synthetic-only UI fixtures. `basic-ci.log` retains
full validation output. The public PR and this plan retain validation summaries.

## Interfaces and Dependencies


Use the Python standard library for catalog and HTTP serving. Packaged HTML/CSS/
JavaScript use native browser APIs. Keep typed Python interfaces and no new
runtime dependencies. Public run/view/compare commands remain compatible.
