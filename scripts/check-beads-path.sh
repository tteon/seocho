#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-.}"
cd "$ROOT"

exit_code=0
while IFS= read -r -d '' rigbeads; do
  rig_dir="$(dirname "$rigbeads")"
  redirect_file="$rigbeads/redirect"
  tracked_beads="$rig_dir/mayor/rig/.beads"

  if [[ -f "$redirect_file" ]]; then
    local_db_files=$(find "$rigbeads" -maxdepth 1 -type f \( -name 'beads.db' -o -name 'beads.db-shm' -o -name 'beads.db-wal' -o -name 'issues.jsonl' \) | wc -l)
    if [[ "$local_db_files" -gt 0 ]]; then
      echo "ERROR: redirect+local beads conflict at $rigbeads"
      echo "  tracked beads: $tracked_beads"
      exit_code=1
    fi
  fi
done < <(find . -mindepth 2 -maxdepth 3 -type d -name .beads -print0)

if [[ "$exit_code" -eq 0 ]]; then
  echo "OK: no redirect/local-beads conflicts"
fi

exit "$exit_code"
