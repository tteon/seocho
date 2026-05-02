#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -d dist ]]; then
  echo "dist/ is missing. Run npm run build before checking built links." >&2
  exit 1
fi

mapfile -t targets < <(
  find dist -type f \( -name '*.html' -o -name '*.xml' \) -print0 |
    xargs -0 rg -o '((href|src)="/[^"#?]+")' |
    sed -E 's/.*="([^"]+)"/\1/' |
    sort -u
)

missing=()

for target in "${targets[@]}"; do
  if [[ "$target" == "/" ]]; then
    candidate="dist/index.html"
    [[ -f "$candidate" ]] || missing+=("$target -> $candidate")
    continue
  fi

  route_path="${target#/}"
  if [[ "$target" == */ ]]; then
    candidate="dist/${route_path}index.html"
    [[ -f "$candidate" ]] || missing+=("$target -> $candidate")
    continue
  fi

  if [[ -f "dist/${route_path}" ]]; then
    continue
  fi

  if [[ -f "dist/${route_path}/index.html" ]]; then
    continue
  fi

  missing+=("$target")
done

if (( ${#missing[@]} > 0 )); then
  echo "Broken built-site links detected:" >&2
  printf '  - %s\n' "${missing[@]}" >&2
  exit 1
fi

echo "Built-site internal links look valid."
