#!/usr/bin/env bash
# SessionStart hook for SEOCHO — injects lightweight repo context once per session.
# Output goes to stdout and is injected into the conversation as system context.
# Keep output short: this is a one-shot session primer, not a dashboard.

set -o pipefail
cd /home/hadry/lab/seocho 2>/dev/null || exit 0

# bd ready (bounded timeout, non-daemon for stability per CLAUDE.md §13)
ready_out="$(timeout 3 bd --no-daemon ready 2>/dev/null | head -6 || true)"

cat <<EOF
=== SEOCHO session primer (.claude/hooks/session-context.sh) ===
Contracts: CLAUDE.md + AGENTS.md + .beads  ·  Push target: main
Track B safety skills (invoke by name when relevant):
  - refactor-safety       → before multi-file renames/moves/splits
  - workspace-id-audit    → after runtime endpoint changes (§6.1)
  - cypher-safety         → when editing files with Cypher (§8)
  - owlready-boundary     → when touching owlready2 imports (§6.3)
EOF

if [ -n "${ready_out}" ]; then
  printf '\nbd ready (top 6):\n%s\n' "${ready_out}"
fi
