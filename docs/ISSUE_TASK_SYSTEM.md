# Issue & Task System (Sprint + Roadmap)

## 1. Objective

Store every issue/task in a collaboration-ready format so triage, sprint planning,
and roadmap execution are consistent.

## 2. Work Item Types

- `issue` (created as `bd` type `bug`): incident, defect, outage, regression
- `task` (created as `bd` type `task`): implementation unit, refactor, integration, docs

Both must carry:

- priority (`P0`..`P4`)
- severity
- impact
- urgency
- sprint
- roadmap
- area

## 3. Required Label Taxonomy

Every active item (`open`, `in_progress`, `blocked`) must include:

- `sev-*` : `sev-critical|sev-high|sev-medium|sev-low`
- `impact-*` : `impact-critical|impact-high|impact-medium|impact-low`
- `urgency-*` : `urgency-now|urgency-this_sprint|urgency-next_sprint|urgency-later`
- `sprint-*` : example `sprint-2026-s03`
- `roadmap-*` : example `roadmap-graph-modeling`
- `area-*` : example `area-frontend|area-backend|area-data|area-infra`
- `kind-*` : `kind-issue|kind-task`

## 4. Priority Guidance

- `P0`: immediate business/system risk, blocks delivery
- `P1`: high urgency, must be done in current sprint
- `P2`: normal planned execution
- `P3`: low urgency / can slip
- `P4`: backlog / speculative

## 5. Scripts

Create issue:

```bash
scripts/pm/new-issue.sh \
  --title "Rules export fails on malformed profile" \
  --summary "500 returned when rule_profile payload is missing rules array" \
  --severity high \
  --impact high \
  --urgency this_sprint \
  --sprint 2026-S03 \
  --roadmap platform-workflow \
  --area backend \
  --priority P1
```

Create task:

```bash
scripts/pm/new-task.sh \
  --title "Add validation error contract for /rules/export/cypher" \
  --goal "Return structured 400 with actionable details for bad profile payload" \
  --severity medium \
  --impact medium \
  --urgency this_sprint \
  --sprint 2026-S03 \
  --roadmap platform-workflow \
  --area backend \
  --priority P2
```

Sprint board:

```bash
scripts/pm/sprint-board.sh --sprint 2026-S03
```

Lint active work items for required collaboration labels:

```bash
scripts/pm/lint-items.sh
scripts/pm/lint-agent-docs.sh
```

## 6. Sprint Cadence

1. Plan sprint backlog from roadmap slices.
2. Ensure each selected item has full label taxonomy.
3. Execute with daily board check:
   - blocked items
   - P0/P1 open items
   - roadmap distribution in sprint
4. Close sprint:
   - close or re-sprint unfinished work with updated urgency
   - carry risks as new items, not hidden notes

## 7. Roadmap Linking Rule

Each item must map to exactly one primary roadmap label (`roadmap-*`).
If work spans multiple tracks, split into separate child tasks under one parent.
