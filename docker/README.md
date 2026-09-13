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
| `compose.tutorials.yaml` | Side stack: FinDER tutorial JupyterLab + Neo4j | `make tutorials-up` |

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

The former Enterprise TLS overlay was removed (#617). Deployment-specific TLS
configuration belongs with the deployment; it is not part of this local stack.
The Opik overlay was removed in ADR-0172; use the vendor-neutral stack under
`examples/observability/` instead.
