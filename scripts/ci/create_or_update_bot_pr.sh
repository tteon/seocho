#!/usr/bin/env bash
set -euo pipefail

branch_name="${SEOCHO_BOT_BRANCH:?SEOCHO_BOT_BRANCH is required}"
commit_message="${SEOCHO_BOT_COMMIT_MESSAGE:?SEOCHO_BOT_COMMIT_MESSAGE is required}"
pr_title="${SEOCHO_BOT_PR_TITLE:?SEOCHO_BOT_PR_TITLE is required}"
pr_body_file="${SEOCHO_BOT_PR_BODY_FILE:?SEOCHO_BOT_PR_BODY_FILE is required}"
base_branch="${SEOCHO_BOT_BASE_BRANCH:-main}"
git_name="${SEOCHO_BOT_GIT_NAME:-seocho-codex}"
git_email="${SEOCHO_BOT_GIT_EMAIL:-seocho-codex@users.noreply.github.com}"
draft_pr="${SEOCHO_BOT_PR_DRAFT:-true}"
labels="${SEOCHO_BOT_PR_LABELS:-}"

if [ ! -f "$pr_body_file" ]; then
  echo "PR body file not found: $pr_body_file" >&2
  exit 1
fi

if [ -z "$(git status --porcelain)" ]; then
  echo "No changes to commit."
  exit 0
fi

git config user.name "$git_name"
git config user.email "$git_email"

git checkout -B "$branch_name"
git add -A

if git diff --cached --quiet; then
  echo "No staged changes after add."
  exit 0
fi

git commit -m "$commit_message"
git push --force-with-lease origin "$branch_name"

existing_pr_number="$(gh pr list \
  --head "$branch_name" \
  --base "$base_branch" \
  --state open \
  --json number \
  --jq '.[0].number // empty')"

if [ -n "$existing_pr_number" ]; then
  gh pr edit "$existing_pr_number" --title "$pr_title" --body-file "$pr_body_file"
  if [ -n "$labels" ]; then
    IFS=',' read -r -a label_items <<< "$labels"
    for label in "${label_items[@]}"; do
      trimmed="$(printf '%s' "$label" | xargs)"
      if [ -n "$trimmed" ]; then
        gh pr edit "$existing_pr_number" --add-label "$trimmed"
      fi
    done
  fi
  echo "Updated PR #$existing_pr_number"
  exit 0
fi

draft_flag=()
if [ "$draft_pr" = "true" ]; then
  draft_flag+=(--draft)
fi

label_args=()
if [ -n "$labels" ]; then
  IFS=',' read -r -a label_items <<< "$labels"
  for label in "${label_items[@]}"; do
    trimmed="$(printf '%s' "$label" | xargs)"
    if [ -n "$trimmed" ]; then
      label_args+=(--label "$trimmed")
    fi
  done
fi

gh pr create \
  --base "$base_branch" \
  --head "$branch_name" \
  --title "$pr_title" \
  --body-file "$pr_body_file" \
  "${draft_flag[@]}" \
  "${label_args[@]}"
