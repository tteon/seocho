# Inference FinOps and graph schema workbench

## Purpose / Big Picture


An operator can inspect token cost by model, tenant and route, connect requests to queue/prefill/decode/cache evidence, compare draft-target serving configurations, and discover graph schema through permitted read-only procedures. Financial answer quality stays a separate prerequisite for a deployment recommendation. A tenant here is an operator-assigned accounting group, not a new multi-tenant runtime or authorization mechanism.

## Progress


- [x] Audit existing production observability, private serving counters and Neo4j connector.
- [x] Create isolated checkout and claim local task seocho-c2sr.
- [x] Add normalized request/cost ledger, telemetry ingestion, dashboard and drift/cost alerts.
- [x] Add bounded draft-target/APC/chunked-prefill process prototype with acceptance counters.
- [x] Add capability-driven read-only schema discovery and operator instructions.
- [x] Run focused contracts and basic CI; inspect dashboard with actual preserved engine traces.
- [ ] Complete fresh GPU/DCGM/speculative and Dozer procedure live gates; keep pending results distinct.

## Surprises & Discoveries


Existing research checkout already measures vLLM queue, prefill, decode, prefix and speculative counters. The clean public baseline has production tracing and Grafana but does not ship those research-only runners. Preserve this distinction; do not relabel implemented flags as measured speedups. Current finite FinDER execution remains frozen while these additive tools are developed.

## Decision Log


Per-tenant accounting is an offline ledger dimension. GPU telemetry remains engine/device scoped; costs require recorded allocation or explicit unit prices. Missing prices are unknown, never zero. Client SSE chunk gaps are not token-level ITL, particularly under speculative decoding. Server ITL and queue/prefill/decode need actual engine telemetry.

Use existing schema procedures before adding Java code. Procedure availability, mode and authorization must be inspected on the actual DozerDB version. Admin/schema-changing procedures stay operator-only. Observed property types and sampled mandatory flags are observations, not FIBO axioms. Tenant-specific schema cannot be inferred safely from a database-wide meta scan in a shared store.

## Outcomes & Retrospective


The operator prototype is implemented. Sixteen focused contract checks pass;
basic CI passed 1,214 tests with five skips. Private preserved run receipts
exercised request ingestion, engine-span joins and incomplete-case alerts.
Browser checks exercised route filtering and request
drilldown without JavaScript errors. These observations validate ingestion of
real evidence, not ontology benefit or serving speedup. Those ingestion checks
do not establish DCGM or speculative compatibility. Private allocation records explicitly separate
estimated attributed and unallocated cost from invoices. Fresh compatibility
and optimization measurements remain pending.

## Context and Orientation


Production instruments live in src/seocho/metrics.py and tracing.py; examples/observability owns Grafana/Prometheus. src/seocho/connectors/neo4j.py currently reads db.schema node/relationship property metadata. New reusable analysis lives under src/seocho/eval/ with a CLI in scripts/benchmarks/. Raw financial data, credentials and experimental outputs stay in ignored directories.

## Plan of Work


First implement a strict request ledger and server telemetry summary, then render an interactive file-based report with honest coverage and provenance. Next generate serving commands and run bounded measured probes against an explicitly supplied endpoint; acceptance rate comes from accepted draft tokens divided by proposed draft tokens. Finally wrap schema capability discovery behind fixed read-only queries, preserving sampling and authority boundaries.

## Concrete Steps


From the task checkout run focused pytest tests for the new workbench and schema discovery, then bash scripts/ci/run_basic_ci.sh. Use CLI --help for report, telemetry probe, serving-plan and schema commands. Existing live artifacts may validate ingestion but do not establish telemetry that was not collected. Keep corrected implementation runs distinct from the active FinDER baseline.

## Validation and Acceptance


Tests must reject duplicate request charging, unknown-cost dilution, reset counter deltas, false ITL from stream chunks, incorrect draft denominators, mismatched drift cohorts and administrative procedure execution. A report must render without external assets and show missing metrics explicitly. Actual vLLM/Dozer/DCGM checks must name versions and hardware; unavailable DCGM is a gap.

## Idempotence and Recovery


Append request receipts with unique IDs and reject overwriting run directories. Repeat analysis safely from immutable inputs. Never restart the active FinDER server to test new serving flags. Use the already authorized Vast lifecycle for any additional controlled serving run, with timeouts, checkpoints, collection and verified teardown.

## Artifacts and Notes


Keep normalized JSONL requests, engine/DCGM snapshots, alerts, HTML report and serving manifests separate. Raw traces link through request/trace IDs; no credentials or source text in metric labels. Local beads task: seocho-c2sr.

## Interfaces and Dependencies


Reuse standard JSONL, HTTPX, existing SEOCHO tracing, Neo4j driver and operator Prometheus/OTLP deployment. No new vendor telemetry backend or mandatory Java plugin. A custom read-only procedure is conditional on a reproducible gap in built-ins/APOC, not an unconditional extra component.

## SEOCHO Evidence Contract


Each measurement identifies source, scope, units, observation window and unknown fields. Each schema response identifies database/workspace scope, procedure, sampled status and errors. Definition and inference rules remain separately versioned ontology evidence.

ProcedureMemory is considered for bounded traversal intermediates, not as a
page-cache or KV-cache optimization. Inspect the actual deployed API, then test
injection, transaction-budget enforcement and cleanup separately. Prioritize
fixed Cypher/batching and supported warmup before introducing a custom Java
plugin. Compare result equivalence and total application latency as well as
procedure-local allocation or page-cache faults.

## SEOCHO Review Panel


Semantic lens: distinguish observed structure from ontology truth; preserve question-quality outcomes alongside cost. Software lens: typed inputs, deterministic identity, fail-closed read-only capability selection and no hidden retries. Systems lens: no invented device attribution or token-level timings; distinguish counter windows, warm/cold cache, draft cost and request latency. These written lenses are design review, not independent agent or human endorsements.

## Cost, Latency, and Provider Policy


No paid calls are necessary for report generation. A live probe is explicit and bounded. Draft and target resources, failures, retries and idle allocation must appear in the cost boundary. Price estimates and actual billing are different sources. Alert baselines match model, tenant, route, configuration and workload; output differences signal non-determinism, not automatically quality drift.
