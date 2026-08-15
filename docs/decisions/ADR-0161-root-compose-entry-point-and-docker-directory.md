# ADR-0161: One root compose entry point, overlays and side stacks under `docker/`

Date: 2026-08-15 · Status: accepted

## Context

The repository root carried seven tracked `docker-compose.*.yml` files plus two
agent-only dotfile docs. Of the 25 tracked root files, ten were environment
definitions or agent conventions rather than entry points, so the first thing a
visitor saw on GitHub was a list of environments, not a list of ways in.

`docs/REPOSITORY_LAYOUT.md` had encoded the crowding as contract ("Root Files …
one-command local stack entry points", listing five compose files as `keep in
root: yes`), and its own Compose Files section claimed "only two compose files
are part of the tracked repo contract" while five were tracked. The layout doc
was both prescribing and misdescribing the root.

Two of the seven files were effectively invisible. `docker-compose.instance.yml`
was reachable only through `seocho serve --instance`, and
`docker-compose.tls-enterprise.yml` was referenced by name nowhere in the
repository — no Makefile target, no doc, no script — despite backing the
cert-rotation qualification in `scripts/benchmarks/neo4j_tls_rotation_probe.py`
and `scripts/setup/generate-neo4j-tls-cert.sh`.

The constraint on any move is Compose's path resolution. Relative paths
(`context: .`, `./data/neo4j/data`, `./extraction:/app`) and `${...}`
interpolation from `.env` — 106 interpolation sites across the seven files —
resolve against the *project directory*, which Compose defaults to the
directory of the **first** `-f` file. Moving a compose file without accounting
for this silently repoints bind mounts and drops the root `.env`.

## Decision

Keep exactly one compose file in the repository root and move the rest under
`docker/`, renamed to the Compose Specification's `compose.yaml` convention:

| Was | Is |
|---|---|
| `docker-compose.yml` | `compose.yaml` |
| `docker-compose.dev.yml` | `docker/compose.dev.yaml` |
| `docker-compose.instance.yml` | `docker/compose.instance.yaml` |
| `docker-compose.memory.yml` | `docker/compose.memory.yaml` |
| `docker-compose.opik.yml` | `docker/compose.opik.yaml` |
| `docker-compose.tls-enterprise.yml` | `docker/compose.tls-enterprise.yaml` |
| `docker-compose.tutorials.yml` | `docker/compose.tutorials.yaml` |

The root file keeps the default entry point intact: `compose.yaml` precedes
`docker-compose.yml` in Compose's discovery order, so bare `docker compose up`,
`make up`, and `seocho serve` behave as before.

**No relative path inside any compose file changes.** Instead the invocation
contract absorbs the difference, in two cases:

- **Overlays** are passed after the root file, so the project directory is
  already the repo root:
  `docker compose -f compose.yaml -f docker/compose.dev.yaml`.
- **Side stacks** are invoked alone and must pin it:
  `docker compose --project-directory <root> -f docker/compose.<name>.yaml`.
  This matches the pattern `examples/mdm/07_up_instances.sh` already used.

`src/seocho/local.py` emits `--project-directory <resolved root>` for the
instance stack, and `find_project_dir` now accepts `compose.yaml` while still
recognising `docker-compose.yml` so an older checkout keeps resolving.

Example-scoped stacks are **not** moved. `examples/observability/` and
`examples/mdm/` compose files sit beside the configs they mount
(`prometheus.yml`, `otel-collector.yaml`, `config/`), and colocation is more
useful there than a central directory.

Two agent-only dotfile docs move out of the root as well:
`.AGENTS.md` → `docs/maintainers/AGENT_EXECPLAN_CONVENTIONS.md` and
`.PLANS.md` → `docs/maintainers/EXECPLAN_SPEC.md`. This also retires half of
the "AGENTS.md/.AGENTS.md duality" weakness recorded in
`docs/ARCHITECTURE_HEALTH.md` (seocho-b01.3).

`docker/README.md` documents the stack inventory and the project-directory
rule, and gives `compose.tls-enterprise.yaml` its first documented entry point.

### ADR id reservation

This ADR was written as 0155, renumbered to 0156, and renumbered again to 0157.
Both moves were forced by a branch that landed while this one was open: 0155 by
the Rust data plane ADR (#484), 0156 by the H0 gate verdict. Separately,
`docs/execplans/finbench-graph-agent-scalability.md` had pre-assigned 0155 to an
unwritten PG->LPG projection ADR — four claims across two ids.

The two failure modes are different, and only one was already covered:

- **Two files claiming an id.** `check_no_new_duplicates` catches this, and did:
  merging main surfaced the 0156 collision immediately rather than at merge
  time. Working. No change.
- **An id reserved in prose for an ADR nobody has written.** Invisible to both
  existing checks — there is no file to be duplicate, and it never reaches
  `DECISION_LOG`. This is what let the execplan sit on 0155.

So the rule is now: **cite an ADR id only once its file exists.**
`check_no_dangling_references` enforces it across `docs/**/*.md` and the
execplan's reservation is removed.

Neither check can prevent a collision *between open branches* — an id is only
knowable as taken once it lands. The mitigation is the cheap one: pick the
number last, and merge main before you do.

## Consequences

- The tracked root drops from 25 files to 17, and every remaining one is an
  entry point, a standard GitHub file, or tool configuration.
- Anyone invoking a side stack by hand without `--project-directory` gets wrong
  bind-mount paths. Build-context stacks fail loudly (`docker/` has no
  `extraction/Dockerfile`), but `compose.memory.yaml` — which has no relative
  paths, only interpolation — would start with an unpopulated `.env`. The rule
  is stated in `docker/README.md`, the Makefile, and this ADR; it is not
  enforced mechanically.
- Existing containers and named volumes created under the old project names are
  unaffected, because every `make` target already pinned
  `COMPOSE_PROJECT_NAME`, and the root stack's project directory is unchanged.
- Historical ADRs (`ADR-0016`, `ADR-0046`, `ADR-0075`, `ADR-0076`, `ADR-0085`,
  `ADR-0104`) still name the old paths. They are decision records and were left
  as written; this ADR is the rename table they resolve against.

## Validation

- `bash scripts/ci/run_basic_ci.sh`
- `bash scripts/ci/check-doc-contracts.sh`
- `bash scripts/ci/check-root-hierarchy-contract.sh`
- `bash scripts/ci/check-runtime-shell-contract.sh`
- `bash scripts/ci/check-module-ownership-contract.sh`
- `docker compose config` on every moved file, asserting the resolved project
  directory, `.env` source, and bind-mount paths match the pre-move stack.
