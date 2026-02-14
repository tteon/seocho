# Ops Runbook

## Standard Flow
1. `scripts/check-beads-path.sh`
2. `scripts/ops-check.sh --fix` (or `--rig <name> --fix`)
3. Resolve fail items immediately
4. `bd sync`
5. `git pull --rebase && git push`

## Notes
- If `gt doctor` warns about `misclassified-wisps`, create/refresh a `bd` bug and attach latest raw log path.
- Keep runtime files out of git tracking (`daemon/activity.json`, sqlite `-shm/-wal`).
- Avoid committing embedded git clones under rig directories.

## One-command Landing
- Dry-run quality gate: `scripts/gt-land.sh --rig seocho`
- Include push: `scripts/gt-land.sh --rig seocho --push`
