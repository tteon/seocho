# Publish research implementation on a review branch

Purpose: let another agent/reviewer inspect the FinDER agent observability and
matched coordination setup alongside its purpose, completed evidence and gaps.
Use a new codex/agent-observability-research branch from current origin/main;
keep the original dirty research checkout unchanged and isolate unrelated memory
benchmark commits. Include only SDK/runtime research code, synthetic examples,
tests, contracts and aggregate reports. Exclude corpora/raw stores/keys/private
server identifiers. Allocate new ADR0236–0238 because current main already uses
historical0152–0154 for different decisions.

Review lenses: professor—completion versus correctness and public paths versus
internal reasoning remain explicit; single identity is a negative control,
live240-trial gate is still pending. Engineer—adapt to current main, dependency
lock/package assets/CI coverage must match new modules, not copy old main files.
Systems—Recorder-only claims remain scoped to runners; no paid run or production
route change occurs during publication, raw/transcripts stay local.

Progress: source/contract transfer and purpose/code-map guide prepared. Validate
current-main full basic CI, no-private-artifact scan, imports/package assets and
ADR index. Commit scoped files with GitHub no-reply identity, rebase this isolated
branch on fetched main, push new branch and verify remote SHA/status. Do not
merge main. Keep worktree attached for review; no unrelated changes are reverted.

Acceptance: published GitHub branch contains runnable Mock examples and code
map; all checks/gaps reported accurately and completed-versus-pending distinction
is visible without private files. Recover by retaining worktree and fixing
integration issues before publication; never overwrite existing remote branch.

Validation on current-main branch: full basic CI1342 passed,5 skipped; identity, imports, ADR index and lock checks passed. Public Mock acceptance20 conditions/88calls/388events passed, explicitly protocol-only. Newly added content/paths scanned for keys/private hosts/raw corpora; no newly added private artifacts found. Runtime research files retained without unrelated benchmark branch commits.
