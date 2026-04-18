# ADR-0085: Image-Backed Local Runtime Source Contract

Date: 2026-04-18
Status: Accepted

## Context

The local `extraction-service` runtime has been started from a container whose
application code is bind-mounted directly from the host checkout:

- `./extraction -> /app`
- `./runtime -> /app/runtime`
- `./seocho -> /app/seocho`

That arrangement kept the staged `extraction/ -> runtime/` migration simple,
but it created an operational problem: `http://localhost:8001` no longer
matched a known source snapshot unless the host checkout itself was clean and at
the intended commit. A dirty or divergent checkout could silently change the
runtime behavior without any image rebuild.

For benchmark, smoke, and support loops this is the wrong default. We need the
default local runtime to be reproducible from a known source snapshot.

## Decision

Make the default local compose path image-backed.

- `docker-compose.yml` now builds `extraction-service` from repo-root context
  using `extraction/Dockerfile`.
- That image bakes `extraction/`, `runtime/`, `seocho/`, `notebooks/`, and
  `demos/` into the container image.
- The default `make up` path now runs `docker compose up -d --build` so the
  running runtime matches the source snapshot that was just built.

Keep live bind mounts, but move them behind an explicit development override:

- `docker-compose.dev.yml`
- `make up-live`
- `make dev-up`

This preserves the fast edit loop for active local development without making
it the default runtime activation contract.

## Consequences

- `extraction-service` on port `8001` now reflects an image-built snapshot by
  default, not an arbitrary dirty checkout.
- Runtime smoke checks, FinDER loops, and support debugging can reason about a
  concrete source snapshot again.
- The default local activation path becomes a little slower because it rebuilds
  the runtime image.
- Developers who explicitly want bind-mounted live code still have a supported
  path via `docker-compose.dev.yml`.

## Follow-Ups

- Reduce ontology-context warning noise on mixed-property graphs so the
  reproducible runtime logs are easier to read.
- Remove remaining ambiguity between the historical `extraction-service` name
  and the canonical `runtime/*` ownership when the service rename is practical.

## Related Documents

- `docker-compose.yml`
- `docker-compose.dev.yml`
- `docs/WORKFLOW.md`
- `docs/ARCHITECTURE.md`
- `docs/RUNTIME_PACKAGE_MIGRATION.md`
- `docs/decisions/ADR-0074-runtime-flat-entrypoint-compatibility.md`
