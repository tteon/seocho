#!/usr/bin/env bash
set -euo pipefail

lane="${1:-}"

if [ -z "$lane" ]; then
  echo "Usage: $0 <feature-improvement|refactor|e2e-investigation>" >&2
  exit 1
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

if ! command -v codex >/dev/null 2>&1; then
  echo "codex CLI is required." >&2
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI (gh) is required." >&2
  exit 1
fi

base_branch="${SEOCHO_CODEX_BASE_BRANCH:-main}"
current_branch="$(git branch --show-current)"

if [ "$current_branch" != "$base_branch" ]; then
  echo "Run local Codex automation from branch '$base_branch' in a dedicated clone." >&2
  exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "Working tree must be clean before running local Codex automation." >&2
  exit 1
fi

git pull --rebase origin "$base_branch"

prompt_file=""
branch_name=""
commit_message=""
pr_title=""
pr_labels=""

case "$lane" in
  feature-improvement)
    prompt_file="scripts/codex/prompts/feature-improvement-pr.md"
    branch_name="codex/feature-improvement"
    commit_message="feat: codex feature improvement"
    pr_title="feat: codex feature improvement"
    pr_labels="automation,codex,feature-improvement"
    ;;
  refactor)
    prompt_file="scripts/codex/prompts/refactor-pr.md"
    branch_name="codex/refactor"
    commit_message="refactor: codex bounded cleanup"
    pr_title="refactor: codex bounded cleanup"
    pr_labels="automation,codex,refactor"
    ;;
  e2e-investigation)
    prompt_file="scripts/codex/prompts/e2e-investigation-pr.md"
    branch_name="codex/e2e-investigation"
    commit_message="fix: codex e2e investigation"
    pr_title="fix: codex e2e investigation"
    pr_labels="automation,codex,e2e"
    ;;
  *)
    echo "Unknown lane: $lane" >&2
    exit 1
    ;;
esac

if [ ! -f "$prompt_file" ]; then
  echo "Prompt file not found: $prompt_file" >&2
  exit 1
fi

mkdir -p outputs/codex
timestamp="$(date +%Y%m%d-%H%M%S)"
final_file="outputs/codex/${lane}-${timestamp}.md"
pr_body_file="outputs/codex/${lane}-pr-body.md"

codex exec \
  --model "${SEOCHO_CODEX_MODEL:-gpt-5.4}" \
  --full-auto \
  -C "$repo_root" \
  -o "$final_file" \
  < "$prompt_file"

if [ -z "$(git status --porcelain)" ]; then
  echo "No changes produced for lane '$lane'."
  exit 0
fi

cp "$final_file" "$pr_body_file"
bash scripts/ci/validate_pr_body.sh "$pr_body_file"

export SEOCHO_BOT_BRANCH="$branch_name"
export SEOCHO_BOT_COMMIT_MESSAGE="$commit_message"
export SEOCHO_BOT_PR_TITLE="$pr_title"
export SEOCHO_BOT_PR_BODY_FILE="$pr_body_file"
export SEOCHO_BOT_PR_DRAFT="true"
export SEOCHO_BOT_GIT_NAME="${SEOCHO_BOT_GIT_NAME:-seocho-codex}"
export SEOCHO_BOT_GIT_EMAIL="${SEOCHO_BOT_GIT_EMAIL:-seocho-codex@users.noreply.github.com}"
export SEOCHO_BOT_PR_LABELS="$pr_labels"
export SEOCHO_BOT_BASE_BRANCH="$base_branch"

bash scripts/ci/create_or_update_bot_pr.sh
