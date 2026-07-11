# Local observability stack (ADR-0144)

A lightweight, local OpenTelemetry stack for SEOCHO traces — the alternative to
self-hosted Opik (8 containers) when you just want local visibility. Opik stays
the **cloud** team backend; this is for local development.

Four containers:

| Service | Image | Host port | Role |
|---|---|---|---|
| `otel-collector` | `otel/opentelemetry-collector-contrib:0.119.0` | 4317 / 4318 / 8889 | receives OTLP, fans out |
| `tempo` | `grafana/tempo:2.7.2` | 3200 | trace store |
| `prometheus` | `prom/prometheus:v2.55.1` | 9091 | metrics store |
| `grafana` | `grafana/grafana:11.5.1` | 3000 | dashboards / Explore |

Flow: `SEOCHO → OTLP gRPC → Collector → Tempo (traces) + Prometheus (metrics) → Grafana`.

> Pattern borrowed from [LMCache's `examples/observability`](https://github.com/LMCache/LMCache/tree/dev/examples/observability),
> **not** a copy — SEOCHO services push to the collector by container name, so no
> `host.docker.internal` is needed. LMCache's KV-cache metrics don't apply here
> (SEOCHO calls external LLM APIs; no local vLLM+LMCache serving).

## Run

The stack joins the **external** `seocho-net` network created by the repo's main
`docker-compose.yml`, so bring the main stack up first, then:

```bash
docker compose -f examples/observability/docker-compose.observability.yml \
    --profile observability up -d
```

Point SEOCHO at the collector:

```bash
# app running inside the compose network:
export SEOCHO_TRACE_BACKEND=otlp
export SEOCHO_TRACE_OTLP_ENDPOINT=http://otel-collector:4317
# app running on the host: the default http://localhost:4317 already works
export SEOCHO_TRACE_BACKEND=otlp
```

Install the exporter deps the backend needs: `pip install 'seocho[otel]'`.

Open Grafana at <http://localhost:3000> (anonymous admin) → **Explore → Tempo**:

```
{ name = "rag.ask" }
{ name = "db.query" && span.db.duration_hydrate_ms > 50 }   # pure-python codec fallback
{ span.workspace_id = "<workspace>" }
```

## Caveats (dev-grade)

- Anonymous-admin Grafana, login disabled, local-disk storage, 7-day retention.
  Fine for local dev; **harden (auth + durable backends) before any shared/prod
  use.** Image tags are pinned — bump deliberately.
- Full prompt/Cypher bodies are only captured with `SEOCHO_TRACE_CAPTURE_CONTENT=1`
  (off by default). Attributes (ids, hashes, counts, timings) always flow.
- Port `9091` (not 9090) avoids clashing with the `opik` profile's MinIO console.

## Live acceptance

Running containers are not sufficient evidence. A validated profile must show:

1. Prometheus reports the Collector target as `up`.
2. Grafana provisions both Tempo and Prometheus datasources.
3. Tempo returns the expected root workflow span.
4. Required retrieval, context, model, and governance children are present.

Use stable container names (`seocho-tempo`, `seocho-otel-collector`, and
`seocho-prometheus`) when launching an ad-hoc profile outside this compose
file; Docker resolves actual names unless explicit aliases are configured.

## Critical-scenario dashboard

Provision `grafana-dashboards.yaml` and mount `dashboards/` at
`/var/lib/grafana/dashboards`. Grafana then exposes **SEOCHO Critical Agent
Memory** (`uid=seocho-critical-agent-memory`) in the SEOCHO folder. It shows:

- critical-scenario pass ratio and current support state;
- authoritative PostgreSQL sequence versus DozerDB projection watermark;
- silent-stale answers and ontology disclosure violations;
- per-scenario, per-stage p95 latency;
- a link to the matching `seocho-agent-memory` Tempo traces.

Only bounded identifiers such as `scenario_id`, `stage`, and `support_status`
are metric labels. Prompts, query text, wallet identifiers, and transaction
payloads must remain out of metrics and trace attributes.
