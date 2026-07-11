# ADR-0146: Production observability profiles and metric contract

- Status: Accepted
- Date: 2026-07-11

## Context

SEOCHO already exports OTel traces and has a critical-scenario dashboard, but a
scenario scorecard is not a complete production telemetry contract. At the
same time, emitting cluster, TLS, replication, and provider-cost metrics when
the relevant capability is absent creates false assurance. Evaluation quality
metrics also have a different operational meaning from runtime SLO signals.

## Decision

SEOCHO owns the complete metric and dashboard contract in
`docs/PRODUCTION_OBSERVABILITY_METRICS_SPEC.md`.

1. Core product telemetry is enabled through an observability profile.
2. PostgreSQL, DozerDB, etcd, cluster, TLS, and evaluation signals are
   capability-gated profiles; unsupported signals are explicit, never fake
   healthy zeroes.
3. Production SLO dashboards and blockchain evaluation dashboards are
   separate supported surfaces.
4. Derived lag, ratios, and compression use recording rules instead of
   duplicate instruments.
5. Metrics use bounded enumerations. Tenant, wallet, transaction, prompt,
   response, query text, and other unbounded identifiers are forbidden labels.
6. Traces provide per-request causality; metrics provide aggregate operations;
   auditable receipts provide exact historical reproduction.

## Consequences

- SEOCHO must implement application instruments, dependency scrapes, recording
  and alert rules, seven dashboards, cardinality/privacy tests, and live OTLP
  verification.
- Enabling observability does not require enabling every database topology.
- Evaluation metrics remain first-class but cannot page production operators.
- New metrics require ownership, unit, type, label budget, enablement profile,
  and verification coverage before release.

