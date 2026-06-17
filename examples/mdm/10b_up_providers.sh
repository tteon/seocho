#!/usr/bin/env bash
# Bring up the provider fleet (scenario hq-42k): 4 MARA-model DozerDB instances.
# Idempotent; waits until each answers HTTP.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

docker compose --project-directory . -f examples/mdm/docker-compose.providers.yml up -d

for c in dozer-deepseek dozer-gptoss dozer-minimax25 dozer-minimax27; do
  ok=0
  for _ in $(seq 1 60); do
    if docker exec "$c" wget -q -O /dev/null http://localhost:7474 2>/dev/null; then
      ok=1; break
    fi
    sleep 2
  done
  if [[ "$ok" == 1 ]]; then
    echo "== $c up =="
  else
    echo "!! $c did not come up within 120s — docker logs $c" >&2
    exit 1
  fi
done
echo "== provider fleet online: deepseek :7691 · gptoss :7692 · minimax25 :7693 · minimax27 :7694 =="
