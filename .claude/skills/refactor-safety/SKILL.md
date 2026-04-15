---
name: refactor-safety
description: Pre-refactor safety procedure for SEOCHO — inventory the touch set, verify test coverage, check workspace_id / Cypher / owlready invariants, and establish regression anchors before making multi-file changes. Invoke before any rename-across-files, module split, cross-module move, or structural refactor. Not needed for single-file tweaks.
---

# refactor-safety

Purpose: make SEOCHO refactors reversible. The goal is not to prevent change — it is to ensure a regression leaves a trace you can follow.

Ground contract: `CLAUDE.md` §4.3 (Before Landing), §7 (Coding Standards), §11 (Definition Of Done), §14 (Philosophy Alignment), §15 (Execution Priority).

## When to invoke

- User asks to rename a symbol/module across multiple files
- User asks to split, merge, or move modules (e.g., `extraction/` ↔ `runtime/` shifts like the recent `agent_server.py` alias)
- User asks to change a runtime contract (method signature in `seocho/client.py`, `seocho/session.py`, `runtime/agent_server.py`)
- Before landing a refactor-labeled PR (`kind-refactor`)

Skip for: typo fixes, single-function edits, test-only changes, docs edits.

## Procedure

### 1. Clarify scope with the user

Ask, if unclear:
- Which symbols / paths are in scope?
- Is behavior change intended? (Refactor = no; if yes, split into a feature change.)
- Is this a runtime contract (touches `workspace_id`, agent routing, or storage API) or internal?

### 2. Inventory the touch set

For each target symbol, find all references:

```bash
# rough surface
rg -l "<symbol>" --type py
# precise callsites
rg "<symbol>\(|from .* import .*<symbol>|import .*<symbol>" --type py -n
```

Prefer `mcp__serena__find_referencing_symbols` when the symbol is a function or class — it resolves imports that grep misses.

Record the file count and classify:
- **Runtime hot path**: `runtime/**`, `seocho/session.py`, `seocho/client.py`, `seocho/store/**`, `seocho/query/**`, `semantic/main.py`, `evaluation/server.py`
- **Offline / batch**: `extraction/pipeline.py`, `extraction/ontology_*`, `scripts/**`, CLI wrappers
- **Shims/aliases**: `extraction/agent_server.py` (points at `runtime.agent_server`), `extraction/rule_constraints.py`, `extraction/vector_store.py` — changing the real path requires updating the shim

### 3. Verify test coverage exists

For each file being changed, confirm a test file exists:

```bash
# seocho/foo.py       → seocho/tests/test_foo.py or tests/test_foo_*.py
# extraction/bar.py   → extraction/tests/test_bar.py
# runtime/baz.py      → extraction/tests/test_baz.py or similar
rg -l "from <module> import|import <module>" --type py --glob 'test_*.py' --glob '*_test.py'
```

If no test exists for a file in the touch set, **add a narrow regression test before refactoring**, not after. The test only needs to pin current behavior.

### 4. Run invariant checks relevant to the touch set

Run these in parallel with step 3, based on what you're touching:

- **Runtime endpoints / API routes** → invoke the `workspace-id-audit` skill
- **Any file containing `MATCH`, `MERGE`, `CREATE (`** → invoke the `cypher-safety` skill
- **Any file importing `owlready2`** → invoke the `owlready-boundary` skill
- **Agent execution paths** (`runtime/agent_server.py`, `extraction/agents_runtime.py`) → confirm the refactor does not introduce direct `Runner.run` calls outside `agents_runtime.py` (CLAUDE.md §15 implementation note)

### 5. Establish regression anchor

Run the focused test suite for the touch set **before** making any change:

```bash
PYTHONPATH=/home/hadry/lab/seocho python3 -m pytest <touched test files> -x -q
```

If tests fail at baseline, stop — the touch-set state is already broken and refactor will mask the root cause.

Capture the passing output. This is your regression oracle.

### 6. Make the change

- Keep to the declared scope. If you discover adjacent issues, file follow-up `bd` items (`scripts/pm/new-issue.sh` or `scripts/pm/new-task.sh`) and do not expand the current change.
- Preserve behavior: same inputs → same outputs. If you cannot, you're not refactoring — stop and re-scope with the user.

### 7. Re-run the regression anchor

Run the exact same test command from step 5.

- All green → proceed to landing (`CLAUDE.md` §4.3)
- Red → diff the output vs step 5, fix, repeat. Do not proceed with red tests.

### 8. Before landing

- If a runtime contract changed, update the contract test alongside production code in the same commit
- If behavior changed (even as a side effect), this is no longer a `kind-refactor` — relabel the `bd` item
- Follow `CLAUDE.md` §4.3 (pull --rebase, bd sync, push to main) and §17 (docs sync for architecture-affecting changes)

## Anti-patterns to avoid

- **Scope creep**: "while I'm here, let me also clean up X." → File a follow-up bd, do not bundle.
- **Green tests from wrong reasons**: if a test passed before and still passes but you changed its mock, the test is lying. Read the test body.
- **Shim shortcuts**: renaming the real file without updating `extraction/*` compat shims breaks SDK consumers.
- **Cross-cutting workspace_id changes without audit**: propagating `workspace_id` through a refactor is a runtime contract change — run `workspace-id-audit` every time.
