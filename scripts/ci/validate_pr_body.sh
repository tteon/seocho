#!/usr/bin/env bash
set -euo pipefail

body_file="${1:-}"

if [ -z "$body_file" ]; then
  echo "Usage: $0 <pr-body-file>" >&2
  exit 1
fi

if [ ! -f "$body_file" ]; then
  echo "PR body file not found: $body_file" >&2
  exit 1
fi

required_headings=(
  "## Feature"
  "## Why"
  "## Design"
  "## Expected Effect"
  "## Impact Results"
  "## Validation"
  "## Risks"
)

for heading in "${required_headings[@]}"; do
  if ! grep -Fqx "$heading" "$body_file"; then
    echo "Missing required heading in PR body: $heading" >&2
    exit 1
  fi
done
