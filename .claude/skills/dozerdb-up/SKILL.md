---
name: dozerdb-up
description: Bring up the local DozerDB (Neo4j-compatible) graph backend for SEOCHO benchmarks and verify it is reachable on bolt. Invoke before any run that needs a graph store (finder-graphrag-bench, graph/hybrid retrieval, phase extraction to Neo4j) or when a script fails with "Connection refused" on 7687 / "Graph not found" / auth errors.
---

# dozerdb-up

Purpose: get the graph backend running and reachable with one predictable recipe, so benchmark runs don't fail mid-flight on a down container, a missing database, or an auth mismatch. DozerDB is the fixed graph backend (CLAUDE.md §1).

## When to invoke

- Before `finder-graphrag-bench`, phase extraction with `--graph bolt://...`, or any graph/hybrid retrieval.
- When a run errors with: `Couldn't connect to localhost:7687`, `Connection refused`, `ServiceUnavailable`, `Neo.ClientError.Security.Unauthorized`, or `Neo.ClientError.Database.DatabaseNotFound: Graph not found: <db>`.
- After a host reboot or a long idle (the container is flaky across sessions and exits silently).

## Bring-up recipe

1. **Set credentials in `.env`** (repo root). `docker-compose.yml` requires `NEO4J_PASSWORD` or compose aborts:
   ```
   NEO4J_URI="bolt://localhost:7687"
   NEO4J_USER="neo4j"          # lowercase — the DozerDB default account is 'neo4j', NOT 'NEO4J'
   NEO4J_PASSWORD="<your-password>"
   NEO4J_BOLT_PORT=7687
   NEO4J_HTTP_PORT=7474
   ```
2. **Start the container** (service name is `neo4j`, image `graphstack/dozerdb`):
   ```bash
   docker compose --env-file .env up -d neo4j
   ```
3. **Wait for bolt before connecting** — the container reports "Started" before bolt is ready; poll the log:
   ```bash
   until docker logs graphrag-neo4j 2>&1 | grep -q "Bolt enabled" || [ $SECONDS -gt 90 ]; do sleep 3; done
   ```
4. **Verify** connectivity + auth:
   ```bash
   python3 - <<'PY'
   import os
   from neo4j import GraphDatabase
   d = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]))
   d.verify_connectivity(); print("DozerDB OK")
   PY
   ```

## Gotchas (each cost real debugging time)

- **Username case sensitivity.** `NEO4J_USER="NEO4J"` (uppercase) fails auth; the default account is lowercase `neo4j`. Normalize before connecting.
- **Wrong / forgotten password → reset by wiping the volume.** If you don't know the password, stop+remove the container and delete `./data/neo4j/` (DESTRUCTIVE — confirm with the user first), then recreate with a fresh `NEO4J_PASSWORD`.
- **DozerDB does NOT auto-create a database on first write.** Multi-DB layouts (one DB per ontology, e.g. `fibobeindlpg`) must be created explicitly — see `finder-graphrag-bench` for the `ensure_database` step. A write to a non-existent DB silently lands 0 nodes; reads then raise `Graph not found`.
- **Container exits silently across sessions.** Always re-run the bring-up + bolt-wait at the start of a graph session; don't assume yesterday's container is still up.
- **Multi-database needs an enterprise-capable image.** DozerDB supports `CREATE DATABASE`; stock Neo4j Community does not. Confirm the image is `graphstack/dozerdb` if multi-DB `ensure_database` is failing with a syntax/privilege error.

## Verify done

`verify_connectivity()` returns without error and `SHOW DATABASES` (run on the `system` db) lists at least `neo4j` + `system`. For multi-DB benchmark runs, also confirm each ontology-derived DB exists.
