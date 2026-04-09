# Runtime File Isolation Policy

This policy keeps machine-local runtime artifacts out of tracked git state.

## Scope

- `.beads/*` daemon and SQLite runtime files
- temporary lock/state files generated during local issue operations

## Must Stay Untracked

- `.beads/beads.db*`
- `.beads/daemon.lock`
- `.beads/daemon.pid`
- `.beads/daemon.log`
- `.beads/bd.sock`
- `.beads/.jsonl.lock`
- `.beads/last-touched`
- `.beads/export-state/*.json`

## Can Be Tracked

- `.beads/issues.jsonl`
- `.beads/interactions.jsonl`
- `.beads/config.yaml`
- `.beads/README.md`
- `.beads/.gitignore`

## Enforcement

Use doctor checks before landing:

```bash
scripts/gt-doctor.sh
```

`CLEANUP->runtime-file-isolation` fails when runtime files are tracked by git.

For redirect + local artifact conflicts, use:

```bash
scripts/beads-path-guard.sh
scripts/beads-path-guard.sh --auto-clean
```
