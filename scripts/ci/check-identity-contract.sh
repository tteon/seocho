#!/usr/bin/env bash
set -euo pipefail

# Identity contract for a public repository.
#
# Two leak paths put a real-world address on github.com permanently:
#
#   1. file content  — an address hardcoded in tracked source (e.g. a
#      SEC EDGAR User-Agent string) is readable by anyone who clones.
#   2. commit metadata — an author/committer/co-author address is what
#      GitHub uses to attribute a commit to an *account*. A corporate
#      address there links every such commit to that corporate account,
#      and `.mailmap` does not undo it (GitHub attributes by the
#      email/account link, not by mailmap).
#
# Both are checked against an allowlist rather than a denylist, so the
# check never has to spell out the addresses it is protecting.
#
# Usage:
#   bash scripts/ci/check-identity-contract.sh [<commit-range>]
#
# Env overrides:
#   SEOCHO_IDENTITY_RANGE       commit range to audit (default: base..HEAD)
#   SEOCHO_ALLOWED_EMAILS       extra space-separated exact addresses
#   SEOCHO_ALLOWED_SUFFIXES     extra space-separated address suffixes

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

# Addresses that are safe to appear publicly: project contact points,
# GitHub/bot no-reply identities, and documentation placeholders.
ALLOWED_EXACT=(
  "support@seocho.io"
  "security@seocho-project.org"
  "noreply@github.com"
  "noreply@anthropic.com"
  "agent@warp.dev"
  "git@github.com"
)
ALLOWED_SUFFIX=(
  "@users.noreply.github.com"
  "@example.com"
  "@example.org"
  "@example.net"
)

read -r -a extra_exact <<<"${SEOCHO_ALLOWED_EMAILS:-}"
read -r -a extra_suffix <<<"${SEOCHO_ALLOWED_SUFFIXES:-}"
[ "${#extra_exact[@]}" -gt 0 ] && ALLOWED_EXACT+=("${extra_exact[@]}")
[ "${#extra_suffix[@]}" -gt 0 ] && ALLOWED_SUFFIX+=("${extra_suffix[@]}")

EMAIL_RE='[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'

is_allowed() {
  local email allowed
  # GitHub matches identities case-insensitively; normalise before comparing.
  email="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  [ -n "$email" ] || return 1
  for allowed in "${ALLOWED_EXACT[@]}"; do
    [ -z "$allowed" ] && continue
    [ "$email" = "$(printf '%s' "$allowed" | tr '[:upper:]' '[:lower:]')" ] && return 0
  done
  for allowed in "${ALLOWED_SUFFIX[@]}"; do
    [ -z "$allowed" ] && continue
    case "$email" in
      *"$(printf '%s' "$allowed" | tr '[:upper:]' '[:lower:]')") return 0 ;;
    esac
  done
  return 1
}

violations=0

report() {
  violations=$((violations + 1))
  printf '  %s\n' "$1" >&2
}

# --- 1. tracked file content ------------------------------------------------
#
# `.mailmap` is exempt: its whole purpose is to name already-public historical
# identities so `git shortlog` collapses them. Entries there must still be
# pruned by hand once the commits they map are gone.

echo "Checking tracked files for non-allowlisted addresses..."
content_hits="$(
  git grep -oIEn "$EMAIL_RE" -- . ':!.mailmap' || true
)"

if [ -n "$content_hits" ]; then
  while IFS= read -r hit; do
    [ -n "$hit" ] || continue
    location="${hit%:*}"
    email="${hit##*:}"
    is_allowed "$email" && continue
    report "$location -> $email"
  done <<<"$content_hits"
fi

if [ "$violations" -gt 0 ]; then
  cat >&2 <<'EOF'

Non-allowlisted address in tracked content (above).
Read it from the environment with a safe default instead:

    USER_AGENT = os.environ.get("SEC_USER_AGENT", "seocho-ingest support@seocho.io")

If the address is genuinely public, add it via SEOCHO_ALLOWED_EMAILS or to
ALLOWED_EXACT in this script.
EOF
  exit 1
fi
echo "  ok"

# --- 2. commit metadata -----------------------------------------------------

resolve_range() {
  if [ "$#" -ge 1 ] && [ -n "${1:-}" ]; then
    printf '%s' "$1"
    return
  fi
  if [ -n "${SEOCHO_IDENTITY_RANGE:-}" ]; then
    printf '%s' "$SEOCHO_IDENTITY_RANGE"
    return
  fi
  local base ref
  base="${GITHUB_BASE_REF:-main}"
  for ref in "origin/${base}" "$base"; do
    if git rev-parse --verify --quiet "$ref" >/dev/null; then
      printf '%s..HEAD' "$ref"
      return
    fi
  done
  # Shallow clone or detached CI checkout: audit the tip commit only.
  printf 'HEAD~1..HEAD'
}

RANGE="$(resolve_range "${1:-}")"
if ! git rev-parse --verify --quiet "${RANGE%%..*}" >/dev/null 2>&1; then
  RANGE="HEAD"
  commits="$(git rev-list -1 HEAD)"
  echo "Checking commit identities (tip commit only; no base ref available)..."
else
  commits="$(git rev-list "$RANGE" || true)"
  echo "Checking commit identities in ${RANGE}..."
fi

commit_count=0
while IFS= read -r sha; do
  [ -n "$sha" ] || continue
  commit_count=$((commit_count + 1))
  while IFS=$'\t' read -r role email; do
    [ -n "${email:-}" ] || continue
    is_allowed "$email" && continue
    report "$(git log -1 --format='%h %s' "$sha" | cut -c1-72) [$role] $email"
  done < <(git show -s --format="author%x09%ae%x0acommitter%x09%ce" "$sha")

  while IFS= read -r trailer; do
    [ -n "$trailer" ] || continue
    case "$trailer" in
      *"<"*">"*) ;;
      *) continue ;;
    esac
    email="${trailer#*<}"
    email="${email%%>*}"
    is_allowed "$email" && continue
    report "$(git log -1 --format='%h %s' "$sha" | cut -c1-72) [co-author] $email"
  done < <(git show -s --format='%(trailers:key=Co-authored-by,valueonly=true)' "$sha")
done <<<"$commits"

if [ "$violations" -gt 0 ]; then
  cat >&2 <<'EOF'

Non-allowlisted identity in commit metadata (above). These become permanent
public attribution on github.com, and `.mailmap` will not hide them.

Fix before pushing:

    git config --local user.name  "<name>"
    git config --local user.email "<id>@users.noreply.github.com"
    git rebase -r --exec 'git commit --amend --no-edit --reset-author' <base>

Enable "Keep my email addresses private" plus "Block command line pushes that
expose my email" at https://github.com/settings/emails so the next machine
cannot reintroduce it.
EOF
  exit 1
fi
echo "  ok (${commit_count} commit(s))"

echo "Identity contract satisfied."
