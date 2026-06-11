#!/usr/bin/env bash
# Bring up the bronze tier: 3 physical DozerDB department instances.
# Idempotent; waits until every instance answers HTTP.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

docker compose --project-directory . -f examples/mdm/docker-compose.instances.yml up -d

for c in dozer-risk dozer-research dozer-compliance; do
  ok=0
  for _ in $(seq 1 60); do
    if docker exec "$c" wget -q -O /dev/null http://localhost:7474 2>/dev/null; then
      ok=1; break
    fi
    sleep 2
  done
  if [[ "$ok" == 1 ]]; then
    echo "== $c is up =="
  else
    echo "!! $c did not come up within 120s — docker logs $c" >&2
    exit 1
  fi
done
echo "== bronze tier online: risk :7688 · research :7689 · compliance :7690 =="
