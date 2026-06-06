# ADR-0104: Worktree-Isolated Runtime Boot (shared neo4j + ephemeral logical DB)

Date: 2026-06-06
Status: Proposed

## Context

`docker-compose.yml` was a single shared instance with hardcoded ports
(7474/7687/8001/8501) and hardcoded `container_name`s. Two worktrees (or two
agents) could not boot the runtime concurrently: container names collided, the
default compose project was shared, and `make clean` was destructive and
manual. There was no SEOCHO analog of Codex's "boot an isolated instance, drive
it, delete its logs/metrics/DB when done."

This closes the last child of the observability epic (`seocho-6q9`), after the
trace read/query API (`seocho-6q9.1`, ADR-merged via #187) and the perf-budget
gate (`seocho-6q9.2`).

## Decision

Per-worktree isolation follows SEOCHO's existing **single-neo4j,
multi-database** model rather than a full stack per worktree:

- **Shared graph backend.** `make up` starts one neo4j (fixed compose project
  `seocho`, default ports). Worktrees share it. The compose network is given a
  fixed name (`seocho-net`) so per-instance projects can attach as `external`.
- **Per-instance app tier.** `make up INSTANCE=<id>` boots only the stateless
  app tier (extraction API + UI) in its own compose project (`seocho-<id>`)
  from a self-contained `docker-compose.instance.yml`, on hash-derived offset
  ports, reaching the shared neo4j by container name on `seocho-net`.
- **Ephemeral logical database.** Each instance drives its own database
  `wt<hash>`, created with `CREATE DATABASE … IF NOT EXISTS` before the app
  tier connects and dropped with `DROP DATABASE … IF EXISTS` on teardown.
- **Scoped teardown.** `make down INSTANCE=<id>` removes only that instance's
  app project and drops only its logical database; the shared neo4j and other
  instances are untouched.

Canonical derivation is side-effect-free in `src/seocho/instance.py`
(`derive_instance` → `InstanceLayout`); the database name is validated against
the existing runtime contract `^[a-z][a-z0-9]{2,62}$`
(`runtime_contract.DATABASE_NAME_PATTERN`) before any Cypher interpolation.
Boot/teardown orchestration lives in `src/seocho/local.py` and is surfaced via
`seocho serve/stop --instance` and the Makefile.

## Consequences / Tradeoffs

- The heavyweight backend stays shared, so concurrent instances are cheap
  (no neo4j-per-worktree RAM cost), but neo4j is a **shared failure domain** —
  one instance can exhaust shared backend resources.
- App-tier ports are derived from a hash over 200 slots (widened from an
  initial 40 after the isolation experiment measured P(collision) ≈ 0.50 at
  8 tenants; 200 slots brings this to ≈ 0.14, and ≈ 0.01 at the canonical
  two-worktree case). Residual collisions are **detectable** via
  `InstanceLayout.collides_with(...)`. Crucially, a port-slot collision is an
  availability concern only: **data isolation is preserved** because the data
  plane routes by the ephemeral database key, derived independently of the port
  slot (proven in the experiment, Finding 3). A future revision can
  probe-and-reassign at boot.
- `container_name` was removed from the app services (compose now derives
  `<project>-extraction-service-<n>`); shared singletons (neo4j, opik-*) keep
  fixed names. `docker compose exec extraction-service …` still works (service
  name, not container name).

## Validation

- `tests/seocho/test_instance.py` (20 tests): deterministic derivation,
  distinct-instance non-collision, same-slot collision detection, DB-name
  contract compliance, injection rejection, dry-run + fake-runner command
  construction (CREATE-before-up, down-before-DROP), backward-compatible
  no-instance path.
- `docker compose -f docker-compose.instance.yml config` renders a valid
  app-only stack on `external: seocho-net` with offset port maps and the
  ephemeral DB env.
- `bash scripts/ci/run_basic_ci.sh` and `check-doc-contracts.sh` pass.
- Live two-worktree concurrent boot is the documented manual acceptance check
  (`docs/RUNTIME_DEPLOYMENT.md` §3.1).
