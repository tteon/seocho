# Neo4j/DozerDB Intra-cluster TLS Observability

## Scope

Cluster transport security is part of answer reliability. A coordinator,
primary, or replica that cannot authenticate a peer can surface as graph
timeout, partial federation, stale projection, or causal-read failure. SEOCHO
therefore correlates transport health with the request path without placing
certificate identity or secret material in telemetry.

Neo4j exposes standard SSL/TLS policies for its communication channels. Neo4j
Enterprise versions from 2025.03 also expose
`dbms.security.tls_reload_enabled`, which permits dynamic TLS configuration and
certificate reload through a procedure. DozerDB version/edition compatibility
must be verified in the deployed image; SEOCHO must not assume the setting is
available merely because its Bolt surface is Neo4j-compatible.

## Collection path

```text
Neo4j/DozerDB Prometheus endpoint ----+
                                      v
TLS certificate probe ----------> OTel Collector
                                      |
                        +-------------+-------------+
                        v                           v
                    Prometheus                   Tempo
                        +-------------+-------------+
                                      v
                                   Grafana
```

The Collector Prometheus receiver scrapes native database and cluster metrics.
SEOCHO request spans carry only bounded target role, database role, projection
watermark, retry, timeout, and TLS failure class. Grafana links a metric spike
to sampled `graph.query` or `graph.federated_retrieve` traces through
exemplars/time correlation.

## Native metrics and supplemental probes

Prefer native database metrics for availability, cluster/raft/discovery state,
replication lag, connection failures, and query latency. Native metrics do not
necessarily provide certificate expiry and reload outcome across every
Neo4j/DozerDB version. A low-frequency probe therefore supplies:

- `seocho_tls_certificate_expiry_seconds`
- `seocho_tls_handshake_success` by bounded endpoint role
- `seocho_tls_reload_total{outcome}`
- `seocho_tls_reload_last_success_timestamp_seconds`

The reload operation must emit a `db.tls.reload` operational span/event with
deployment generation, endpoint role, outcome, and duration. It must not emit
certificate PEM, private-key path, subject, SANs, serial number, or raw peer
address.

## Cardinality and overhead

Allowed metric labels are `environment`, `cluster`, `endpoint_role`,
`channel`, and bounded `outcome`. Node IDs, pod names, certificate fingerprints,
database names created per tenant, workspace IDs, and trace IDs do not belong
in metric labels. Per-node diagnosis remains in sampled traces or secured
logs.

Certificate probes run every 5-15 minutes and after a controlled reload. They
are not part of the hot query path. Database Prometheus scraping uses a normal
15-30 second interval. A telemetry failure must never trigger a database TLS
reload or fail an agent request.

## Alerts

- certificate expires within 30/14/7 days
- handshake failures above a bounded rate
- reload failed or no post-reload handshake succeeded
- replica/raft lag rises after reload
- graph partial-result or projection-lag rate rises in the same window
- Collector scrape or export failure hides the transport signal

## Evaluation scenario

Rotate one non-production cluster certificate while concurrent Q4 federated
and Q5 causal-read workloads run. The gate passes when existing and new
connections recover within the declared budget, no unauthorized plaintext
fallback occurs, the reload is visible in telemetry, projection consistency is
preserved, and answer partial/stale statuses remain explicit.
