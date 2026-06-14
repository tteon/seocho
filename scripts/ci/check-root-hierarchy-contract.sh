#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

tracked_existing_under() {
  local path="$1"
  local file
  while IFS= read -r file; do
    if [ -e "$file" ]; then
      echo "$file"
      return
    fi
  done < <(git ls-files -- "$path")
}

forbidden_tracked_paths=(
  ".agents"
  ".beads"
  # ".claude" is checked separately below — .claude/skills/ is intentionally
  # tracked (shared project skills, ADR-0113); everything else under .claude/
  # stays forbidden.
  ".githooks"
  ".github/README.md"
  ".gitattributes"
  "experiments/retrieval_comparison"
  "setup_env.sh"
  "setup_opengds.sh"
  "seocho"
  "dataset"
  "images"
  "ontology"
  "neo4j/plugins"
)

for path in "${forbidden_tracked_paths[@]}"; do
  if [ -n "$(tracked_existing_under "$path")" ]; then
    echo "Forbidden tracked root hierarchy path: $path" >&2
    git ls-files -- "$path" | while IFS= read -r file; do
      [ -e "$file" ] && echo "$file" >&2
    done
    exit 1
  fi
done

# .claude/ is untracked EXCEPT .claude/skills/ (ADR-0113). Flag any tracked
# file under .claude/ that is not a shared skill.
claude_forbidden="$(git ls-files -- ".claude" ":(exclude).claude/skills" ":(exclude).claude/skills/**" 2>/dev/null | head -1)"
if [ -n "$claude_forbidden" ]; then
  echo "Forbidden tracked path under .claude/ (only .claude/skills/ may be tracked): $claude_forbidden" >&2
  exit 1
fi

required_paths=(
  "src/seocho/__init__.py"
  "tests/seocho"
  "examples/datasets/finder/all_slices.csv"
  "examples/datasets/finder/manifest.json"
  "docs/assets/banner.png"
  "docs/assets/systemOverview.png"
  "docs/ontology/ONTOLOGY_GUIDE.md"
  "docs/GITHUB_AUTOMATION.md"
  "docs/internal/REPOSITORY_HIERARCHY_REVIEW.md"
)

for path in "${required_paths[@]}"; do
  if [ ! -e "$path" ]; then
    echo "Required hierarchy path missing: $path" >&2
    exit 1
  fi
done

echo "Root hierarchy contract checks passed."
