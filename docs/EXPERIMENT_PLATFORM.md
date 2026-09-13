# Reproducible experiment environment

Use this entry point for an isolated SDK, DozerDB client, Agents SDK and OTel
environment. It preserves an existing `.venv` and keeps generated state under
the ignored `.seocho/` directory.

## Setup and validation

Install `uv`, then run from the repository root:

```bash
make platform-setup
make platform-check
make platform-ci
```

`platform-setup` installs Python 3.11 with the committed `uv.lock` and the `dev`
and `otel` extras into `.seocho/venvs/platform`. The dev extra includes the
Oxigraph Python client used for RDF experiments. `platform-check` checks the
lock, imports the SDK/agent/graph packages, and saves the installed package list
to `.seocho/platform/environment.txt`. `platform-ci` runs basic CI in that same
environment. Override `PLATFORM_ENV` to use another isolated location.

The project still supports Python 3.10, 3.11 and 3.12. `.python-version` selects
the contributor default; CI explicitly selects each matrix interpreter with
`UV_PYTHON`. CI and scheduled maintenance resolve from the same committed lock.
After an intentional dependency change, run `uv lock`, review both
`pyproject.toml` and `uv.lock`, and repeat validation.

## Services and evidence

Use [the deployment guide](RUNTIME_DEPLOYMENT.md) for DozerDB/runtime setup and
[the observability example](../examples/observability/README.md) for OTLP
collection. `make up` starts the configured images. To build current source
into the runtime image, use `make up-build`. An import check proves neither
deployed source parity nor live graph compatibility.

Before any benchmark, follow the continuity gate in [BENCHMARKS.md](BENCHMARKS.md):
read prior manifests and local memory, identify the closest arm, record the
incremental hypothesis, and use a fresh result directory. Keep datasets,
credentials, traces and receipts out of Git. Read-only planning and analysis
must remain distinguishable from paid generation/judging.

The specialist graph-agent runner and trace-analysis commands currently under
local development are a separate review surface. This main-branch entry point
installs and validates their prerequisites; it does not advertise commands that
depend on uncommitted scripts. A completed local run is evidence for its recorded
source snapshot and service configuration, not for a different checkout or image.

Tracing is vendor-neutral (`none`, `console`, `jsonl`, `otlp`). Opik was removed
in ADR-0172; select an OTLP backend explicitly. Preserve original receipts when
exporting saved spans so later verification does not rewrite what the original
run actually observed.
