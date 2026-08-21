# Docker Compose Stacks

The repo root holds exactly one Compose file — `compose.yaml`, the default local
stack. Everything optional lives here so the root stays a list of entry points
rather than a list of environments.

| File | Kind | Brought up by |
|---|---|---|
| `../compose.yaml` | Default stack: DozerDB, extraction service, platform UI | `make up` |
| `compose.dev.yaml` | Overlay: live bind mounts for `extraction/`, `runtime/`, `src/seocho/` | `make up-live` |
| `compose.instance.yaml` | Side stack: isolated per-worktree app tier on the shared DozerDB | `seocho serve --instance <id>` / `make up INSTANCE=<id>` |
| `compose.memory.yaml` | Side stack: authoritative PostgreSQL agent memory | `make memory-up` |
| `compose.opik.yaml` | Side stack: self-hosted Opik observability | `make opik-up` |
| `compose.tutorials.yaml` | Side stack: FinDER tutorial JupyterLab + Neo4j | `make tutorials-up` |
| `compose.tls-enterprise.yaml` | Side stack: Neo4j Enterprise with bolt TLS, for cert-rotation qualification | manual, see below |

Example-scoped stacks stay next to the configs they load and are **not** listed
here: `examples/observability/docker-compose.observability.yml` and
`examples/mdm/docker-compose.instances.yml`.

## The one rule: pin the project directory

Compose resolves relative paths (`./data`, `context: .`) and looks for `.env`
in the **project directory**, which defaults to the directory of the *first*
`-f` file. Every file here is written against the repo root, so:

- **Overlays** need nothing extra — the root `compose.yaml` comes first, so the
  project directory is already the repo root:

  ```bash
  docker compose -f compose.yaml -f docker/compose.dev.yaml up -d
  ```

- **Side stacks** are invoked alone, so they must pin it explicitly. Without
  this, `./data/...` resolves under `docker/` and the root `.env` is never read:

  ```bash
  docker compose --project-directory . -f docker/compose.memory.yaml up -d
  ```

The `make` targets already do this; the pattern only matters when you invoke
Compose by hand.

## Neo4j Enterprise TLS stack

`compose.tls-enterprise.yaml` has no `make` target because it needs an Enterprise
licence and generated certificates. It backs the cert-rotation qualification in
`scripts/benchmarks/neo4j_tls_rotation_probe.py`.

```bash
bash scripts/setup/generate-neo4j-tls-cert.sh
NEO4J_ACCEPT_LICENSE_AGREEMENT=yes NEO4J_TLS_PASSWORD=... \
  docker compose --project-directory . -f docker/compose.tls-enterprise.yaml \
  --profile tls-enterprise up -d
```
