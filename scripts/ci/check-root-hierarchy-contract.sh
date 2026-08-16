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
  # ".claude" is forbidden outright now. ADR-0113 carved out .claude/skills/ as
  # shared project tooling; that exception is withdrawn, so the whole directory
  # is agent state like the others and the special-case check below is gone.
  ".claude"
  ".githooks"
  # .jules and .serena were on CLAUDE.md's non-tracked list and missing from
  # this one, so .jules/bolt.md sat in the tree for months while the contract
  # reported passing. A hygiene list that does not match the documented rule is
  # worse than none: it certifies the drift.
  ".jules"
  ".serena"
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


required_paths=(
  "src/seocho/__init__.py"
  "tests/seocho"
  "examples/datasets/finder/all_slices.csv"
  "examples/datasets/finder/manifest.json"
  "docs/assets/banner.png"
  "docs/assets/systemOverview.png"
  "docs/ontology/ONTOLOGY_GUIDE.md"
  "docs/GITHUB_AUTOMATION.md"
  "docs/REPOSITORY_HIERARCHY_REVIEW.md"
)

for path in "${required_paths[@]}"; do
  if [ ! -e "$path" ]; then
    echo "Required hierarchy path missing: $path" >&2
    exit 1
  fi
done

echo "Root hierarchy contract checks passed."
