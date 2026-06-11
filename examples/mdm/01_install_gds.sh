#!/usr/bin/env bash
# Install OpenGDS (open-core GDS build, GPL packaging) into the DozerDB container.
#
# Official Neo4j GDS distributions are Enterprise-licensed; OpenGDS is the
# open-core build compiled for open distributions like DozerDB. Pinned to the
# build the DozerDB project itself compiles and distributes (dist.dozerdb.org,
# "works with Neo4j Core 5.23 and up" → covers 5.26.3); it still ships the
# legacy gds.graph.project.cypher used by seocho/gds. User-approved source
# (2026-06-11): same distributor as the graphstack/dozerdb image in use.
#
# Idempotent: skips the download when the jar is already in place. The
# docker-compose.yml allowlist change (apoc.*,n10s.*,gds.*) is committed in
# the repo; environment changes need `docker compose up -d` (a plain restart
# keeps the old env).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PLUGINS_DIR="${REPO_ROOT}/data/neo4j/plugins"
CONTAINER="graphrag-neo4j"

GDS_VERSION="2.12.0"
JAR_NAME="open-gds-${GDS_VERSION}.jar"
JAR_URL="https://dist.dozerdb.org/plugins/open-gds/${JAR_NAME}"

wait_up() {
  for _ in $(seq 1 60); do
    if docker exec "${CONTAINER}" wget -q -O /dev/null http://localhost:7474 2>/dev/null; then
      return 0
    fi
    sleep 2
  done
  echo "!! DozerDB did not come back within 120s — check 'docker logs ${CONTAINER}'" >&2
  exit 1
}

# 1. Apply the compose env (allowlist apoc.*,n10s.*,gds.*) FIRST — env changes
#    need a recreate, and a recreate would orphan anything docker-cp'd into the
#    old container right before it (observed: the jar vanished with it).
echo "== applying compose env + recreating neo4j =="
cd "${REPO_ROOT}"
docker compose up -d neo4j
wait_up

# 2. Now place the jar in the live container's /plugins bind mount.
if [[ -f "${PLUGINS_DIR}/${JAR_NAME}" ]]; then
  echo "== OpenGDS ${GDS_VERSION} already installed (${PLUGINS_DIR}/${JAR_NAME}) =="
else
  echo "== downloading OpenGDS ${GDS_VERSION} =="
  TMP_JAR="$(mktemp /tmp/opengds-XXXXXX.jar)"
  curl -fL --retry 3 -o "${TMP_JAR}" "${JAR_URL}"
  # The plugins bind-mount is owned by the container user (7474); docker cp
  # writes through the daemon so no sudo is needed on the host.
  echo "== copying jar into ${CONTAINER}:/plugins =="
  docker cp "${TMP_JAR}" "${CONTAINER}:/plugins/${JAR_NAME}"
  rm -f "${TMP_JAR}"
  echo "== restarting neo4j to load the plugin =="
  docker restart "${CONTAINER}" >/dev/null
  wait_up
fi

echo "== DozerDB is up; verify with: python examples/mdm/00_preflight.py =="
